/*
 * eg4-tou-storm-card: storm prep at a glance, with its controls on one row.
 * Served by the EG4 Local TOU Manager integration (loaded automatically).
 *
 *   type: custom:eg4-tou-storm-card
 *   # everything below is optional
 *   entity: sensor.eg4_tou_storm_recommendation    # found automatically
 *   time_format: 12                                # 12 or 24 (default: follows your Home Assistant profile)
 *   battery_soc_entity: sensor.my_battery_soc      # adds the battery level
 *   title: Storm prep                              # no title by default
 *
 * The buttons turn storm prep and automatic mode on and off, skip the coming storm, and change
 * the charge target in steps of 5%.
 */
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STEP = 5;
const LOOK = {
  prep: ["mdi:alert-octagon", "var(--error-color,#db4437)", "Prep recommended"],
  watch: ["mdi:alert", "var(--warning-color,#ffa600)", "Keep an eye on the weather"],
  clear: ["mdi:check-circle", "var(--success-color,#43a047)", "No storm expected"],
  not_configured: ["mdi:cog-outline", "var(--secondary-text-color)", "No weather source set up"],
};
const NO_DATA = ["mdi:cloud-off-outline", "var(--secondary-text-color)", "Weather data unavailable"];

/** 12 or 24 hour clock: the card's time_format wins, then your Home Assistant profile, then the browser. */
function resolveH24(locale, cfgFormat, browserLocale) {
  if (cfgFormat !== undefined && cfgFormat !== null && cfgFormat !== "") return Number(cfgFormat) === 24;
  const tf = locale && locale.time_format;
  if (tf === "24") return true;
  if (tf === "12") return false;
  const lang = tf === "language" && locale && locale.language ? locale.language : browserLocale;
  try {
    const o = new Intl.DateTimeFormat(lang || undefined, { hour: "numeric" }).resolvedOptions();
    return o.hourCycle ? o.hourCycle === "h23" || o.hourCycle === "h24" : !o.hour12;
  } catch (e) { return true; }
}

/** "Thu 4:00 AM" in Home Assistant's time zone; "" for a missing or unreadable time. */
function when(iso, h24, timeZone) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { weekday: "short", hour: "numeric", minute: "2-digit", hourCycle: h24 ? "h23" : "h12", timeZone })
    .format(d).replace(",", "");
}

function note(a, h24, timeZone) {
  if (a.prep) {
    const until = when(a.until, h24, timeZone);
    return `Charging to ${esc(a.target_soc)}% in every off-peak window; peak hours still run from the battery.` +
      (until ? ` Normal schedule returns <b>${esc(until)}</b>.` : "");
  }
  if (a.skipped) return "<b>This storm is skipped.</b> Tap <b>Prep</b> if you change your mind.";
  if (a.automatic) return "<b>Automatic mode</b> starts storm prep for National Weather Service storm warnings and watches, up to 24 hours ahead. Tap <b>Skip</b> to leave a storm alone.";
  return "Nothing changes until you turn <b>Prep</b> on.";
}

function button(action, icon, label, active, disabled, title) {
  return `<button type="button" data-act="${action}" class="${active ? `on ${active}` : ""}"${disabled ? " disabled" : ""}` +
    `${title ? ` aria-label="${esc(title)}" title="${esc(title)}"` : ""}><ha-icon icon="${icon}"></ha-icon>${label ? `<span>${esc(label)}</span>` : ""}</button>`;
}

/** The whole card body from the recommendation sensor's state and attributes. */
function renderStorm(state, attrs, opts = {}) {
  const a = attrs || {}, h24 = opts.h24, tz = opts.timeZone;
  const [icon, color, text] = LOOK[state] || NO_DATA;
  const lines = [`<div class="head"><ha-icon icon="${icon}" style="color:${color}"></ha-icon><b>${esc(a.prep ? "Storm prep is on" : text)}</b></div>`];
  const range = (x) => [when(x.onset, h24, tz), when(x.ends, h24, tz)];
  const alerts = a.alerts || [];
  for (const x of alerts) {
    const [from, to] = range(x);
    lines.push(`<div class="line"><b>${esc(x.event)}</b> ${esc(from)}${to ? ` to ${esc(to)}` : ""}</div>`);
  }
  if (a.headline && (a.source === "forecast" || !alerts.length)) {
    const [from, to] = range(a);
    lines.push(`<div class="line">${esc(a.headline)}${from ? ` · ${esc(from)}${to ? ` to ${esc(to)}` : ""}` : ""}</div>`);
  }
  if (opts.soc !== null && opts.soc !== undefined) lines.push(`<div class="line">Battery ${Math.round(opts.soc)}% · target ${esc(a.target_soc)}%</div>`);
  if (state === "not_configured") lines.push(`<div class="line">Add weather zones under the integration's Configure &gt; Storm prep. The buttons work without them.</div>`);
  lines.push(`<div class="note">${note(a, h24, tz)}</div>`);
  const target = Number(a.target_soc), [lo, hi] = a.target_range || [20, 100];
  lines.push(`<div class="row">${button("prep", "mdi:shield-home", `Prep: ${a.prep ? "On" : "Off"}`, a.prep ? "alert" : "")}` +
    `${button("auto", "mdi:autorenew", `Auto: ${a.automatic ? "On" : "Off"}`, a.automatic ? "good" : "")}` +
    `${button("skip", "mdi:debug-step-over", "Skip")}` +
    `<span class="target">${button("down", "mdi:minus", "", "", target <= lo, "Lower the charge target")}` +
    `<ha-icon icon="mdi:battery-arrow-up"></ha-icon><b>${esc(a.target_soc)}%</b>` +
    `${button("up", "mdi:plus", "", "", target >= hi, "Raise the charge target")}</span></div>`);
  return lines.join("");
}

/** What a button does: [domain, service, data], or null if its entity is unknown. */
function serviceFor(action, attrs) {
  const c = (attrs && attrs.controls) || {};
  const call = (domain, service, entity, extra) => (entity ? [domain, service, { entity_id: entity, ...extra }] : null);
  if (action === "prep") return call("switch", attrs.prep ? "turn_off" : "turn_on", c.prep);
  if (action === "auto") return call("switch", attrs.automatic ? "turn_off" : "turn_on", c.automatic);
  if (action === "skip") return call("button", "press", c.skip);
  if (action === "up" || action === "down") {
    const [lo, hi] = attrs.target_range || [20, 100];
    const next = Math.max(lo, Math.min(hi, Number(attrs.target_soc) + (action === "up" ? STEP : -STEP)));
    return call("number", "set_value", c.target, { value: next });
  }
  return null;
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 12px 16px; line-height: 1.45; }
  .title { font-size: 1.1em; font-weight: 500; margin-bottom: 8px; }
  .head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .line { margin: 2px 0; }
  .note { font-size: 0.86em; color: var(--secondary-text-color); background: var(--secondary-background-color);
    border-radius: 8px; padding: 8px 12px; margin: 8px 0; }
  .row, .target { display: flex; align-items: center; gap: 5px; }
  .row { flex-wrap: wrap; font-size: 0.82em; }
  button { display: inline-flex; align-items: center; gap: 3px; padding: 3px 8px; border-radius: 16px; font: inherit; font-weight: 500;
    cursor: pointer; white-space: nowrap; color: var(--primary-text-color); background: var(--secondary-background-color);
    border: 1px solid var(--divider-color); }
  button:hover, button:focus-visible { border-color: var(--primary-color); }
  button:disabled { opacity: 0.4; cursor: default; }
  button.on.alert { background: var(--error-color,#db4437); border-color: var(--error-color,#db4437); color: #fff; }
  button.on.good { background: var(--success-color,#43a047); border-color: var(--success-color,#43a047); color: #fff; }
  ha-icon { --mdc-icon-size: 16px; }
  .head ha-icon { --mdc-icon-size: 24px; }
  .msg { color: var(--secondary-text-color); }
`;

if (typeof HTMLElement !== "undefined" && typeof customElements !== "undefined" && !customElements.get("eg4-tou-storm-card")) {
  class Eg4TouStormCard extends HTMLElement {
    setConfig(config) {
      this._config = config || {};
      if (!this.shadowRoot) {
        this.attachShadow({ mode: "open" });
        this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div id="b"></div></ha-card>`;
        this._b = this.shadowRoot.getElementById("b");
        this._b.addEventListener("click", (e) => this._click(e));
      }
      this._entity = this._config.entity || null;
      this._last = null;
    }
    set hass(hass) { this._hass = hass; this._render(); }
    getCardSize() { return 4; }
    static getStubConfig() { return {}; }

    _find() {
      const S = this._hass.states;
      if (this._entity && S[this._entity]) return S[this._entity];
      for (const id of Object.keys(S)) {
        if (id.startsWith("sensor.") && S[id].attributes.controls && "prep" in S[id].attributes) { this._entity = id; return S[id]; }
      }
      return null;
    }
    _render() {
      if (!this._hass || !this._b) return;
      const c = this._config, st = this._find();
      if (!st) { this._b.innerHTML = `<div class="msg">EG4 TOU storm prep not found. Update the integration (v0.12.0 or newer), or set <code>entity:</code>.</div>`; return; }
      const bs = c.battery_soc_entity && this._hass.states[c.battery_soc_entity];
      const soc = bs && Number.isFinite(parseFloat(bs.state)) ? parseFloat(bs.state) : null;
      const key = [st.last_updated, soc].join("|");
      if (key === this._last) return;
      this._last = key;
      const h24 = resolveH24(this._hass.locale, c.time_format, typeof navigator !== "undefined" ? navigator.language : undefined);
      const title = c.title ? `<div class="title">${esc(c.title)}</div>` : "";
      this._b.innerHTML = title + renderStorm(st.state, st.attributes, { h24, soc, timeZone: (this._hass.config || {}).time_zone });
    }
    _click(e) {
      const el = e.target.closest && e.target.closest("[data-act]");
      const st = el && !el.disabled && this._find();
      const call = st && serviceFor(el.getAttribute("data-act"), st.attributes);
      if (call) this._hass.callService(...call);
    }
  }
  customElements.define("eg4-tou-storm-card", Eg4TouStormCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "eg4-tou-storm-card", name: "EG4 TOU storm prep",
    description: "Storm recommendation with Prep, Auto, Skip and charge target buttons on one row (EG4 Local TOU Manager)." });
}

if (typeof module !== "undefined" && module.exports) module.exports = { renderStorm, serviceFor, when, esc, resolveH24, STYLE };
