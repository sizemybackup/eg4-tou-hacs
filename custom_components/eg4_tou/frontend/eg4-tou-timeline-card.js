/*
 * eg4-tou-timeline-card: a 24-hour picture of today's EG4 time-of-use schedule.
 * Served by the EG4 Local TOU Manager integration (loaded automatically; nothing to install).
 *
 *   type: custom:eg4-tou-timeline-card
 *   entity: sensor.eg4_tou_active_profile     # optional, found automatically
 *
 * Lanes: on-peak hours (from your tariff), hold windows (AC First) and the overnight
 * charge window, with a "now" marker. For hold and charge lanes, if the inverter holds
 * something different from the plan, the inverter's windows are drawn underneath.
 */
const DAY = 1440;
const TICKS = [0, 3, 6, 9, 12, 15, 18, 21, 24];

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function toMin(hhmm) {
  const [h, m] = String(hhmm).split(":").map(Number);
  if (h === 23 && m === 59) return DAY; // 23:59 means end of day
  return h * 60 + m;
}
const pct = (min) => Math.max(0, Math.min(100, (min / DAY) * 100));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const dash = (s) => String(s).replace("-", "–");
const rangeText = (ws) => (ws && ws.length ? ws.map(([a, b]) => `${a}–${b}`).join(", ") : "none");

function bars(windows, cls, now) {
  const out = (windows || []).map(([a, b]) => {
    const l = pct(toMin(a)), w = pct(toMin(b)) - l;
    if (w <= 0) return "";
    const label = `${a}–${b}`;
    return `<div class="bar ${cls}" style="left:${l.toFixed(2)}%;width:${w.toFixed(2)}%" title="${esc(label)}"><span class="${w >= 26 ? "big" : w >= 14 ? "mid" : "none"}">${w >= 14 ? esc(label) : ""}</span></div>`;
  });
  return out.join("") + `<i class="now" style="left:${pct(now).toFixed(2)}%"></i>`;
}

function lane(name, cls, plan, inverter, now, hasInverter) {
  let status = "";
  let invTrack = "";
  if (hasInverter) {
    if (inverter === null || inverter === undefined) {
      status = `<span class="st warn">inverter not read</span>`;
    } else if (same(plan, inverter)) {
      status = `<span class="st ok">✓ inverter matches</span>`;
    } else {
      status = `<span class="st warn">⚠ inverter differs</span>`;
      invTrack = `<div class="invcap">Inverter now</div><div class="track inv">${bars(inverter, "invbar", now)}</div>`;
    }
  }
  const text = `${hasInverter ? "Plan: " : ""}${rangeText(plan)}` +
    (invTrack ? `<br>Inverter now: ${rangeText(inverter)}` : "");
  return `<div class="lane"><div class="head"><span class="name">${esc(name)}</span>${status}</div>` +
    `<div class="track">${bars(plan, cls, now)}</div>${invTrack}<div class="txt">${text}</div></div>`;
}

function axis(now) {
  const ticks = TICKS.map((h) => `<span style="left:${(h / 24) * 100}%">${String(h).padStart(2, "0")}</span>`).join("");
  const hh = String(Math.floor(now / 60)).padStart(2, "0"), mm = String(now % 60).padStart(2, "0");
  return `<div class="axis">${ticks}<b class="nowlabel" style="left:${pct(now).toFixed(2)}%">${hh}:${mm}</b></div>`;
}

/** Pure: model = the sensor's `timeline` attribute, now = minutes since midnight. */
function renderTimeline(model, now, title) {
  const m = model || {};
  const peaks = m.peaks || [];
  return `<div class="card-body">` +
    (title ? `<div class="title">${esc(title)}</div>` : "") +
    lane("On-peak hours (your tariff)", "peak", peaks, null, now, false) +
    lane("Hold windows (AC First)", "hold", (m.hold || {}).plan, (m.hold || {}).inverter, now, true) +
    lane("Overnight charge", "charge", (m.charge || {}).plan, (m.charge || {}).inverter, now, true) +
    axis(now) + `</div>`;
}

function nowMinutes(timeZone) {
  const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone }).formatToParts(new Date());
  const get = (t) => Number((parts.find((p) => p.type === t) || {}).value || 0);
  return (get("hour") % 24) * 60 + get("minute");
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 12px 16px 14px; }
  .title { font-weight: 500; margin-bottom: 6px; }
  .lane { margin-bottom: 10px; }
  .head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 3px; }
  .name { font-size: 0.9em; font-weight: 500; }
  .st { font-size: 0.8em; }
  .st.ok { color: var(--success-color, #43a047); }
  .st.warn { color: var(--warning-color, #ffa600); }
  .track { position: relative; height: 26px; border-radius: 6px; overflow: hidden;
    background-color: var(--secondary-background-color, rgba(127,127,127,.12));
    background-image: repeating-linear-gradient(to right, transparent 0, transparent calc(12.5% - 1px), var(--divider-color, rgba(127,127,127,.35)) calc(12.5% - 1px), var(--divider-color, rgba(127,127,127,.35)) 12.5%); }
  .track.inv { height: 18px; margin-top: 1px; }
  .invcap { font-size: 0.7em; color: var(--secondary-text-color); margin-top: 4px; }
  .track { container-type: inline-size; }
  .bar span.mid, .bar span.none { display: none; }
  @container (min-width: 480px) { .bar span.mid { display: inline; } }
  .bar { position: absolute; top: 3px; bottom: 3px; border-radius: 4px; color: #fff; font-size: 0.75em;
    display: flex; align-items: center; justify-content: center; overflow: hidden; white-space: nowrap; }
  .bar.peak { background: var(--error-color, #db4437); }
  .bar.hold { background: var(--primary-color, #03a9f4); }
  .bar.charge { background: var(--success-color, #43a047); }
  .track.inv .bar { top: 2px; bottom: 2px; }
  .bar.invbar { background: transparent; border: 2px dashed var(--warning-color, #ffa600); color: var(--primary-text-color); box-sizing: border-box; }
  .now { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--primary-text-color, #000); opacity: .6; }
  .txt { font-size: 0.78em; color: var(--secondary-text-color); margin-top: 2px; }
  .axis { position: relative; height: 36px; margin-top: 2px; font-size: 0.72em; color: var(--secondary-text-color); }
  .axis span { position: absolute; transform: translateX(-50%); }
  .axis span:first-child { transform: none; } .axis span:last-child { transform: translateX(-100%); }
  .axis .nowlabel { position: absolute; top: 17px; transform: translateX(-50%); font-weight: 600; color: var(--primary-text-color);
    background: var(--card-background-color, #fff); padding: 0 3px; border-radius: 3px; }
  .msg { padding: 14px 16px; color: var(--secondary-text-color); }
`;

if (typeof HTMLElement !== "undefined" && typeof customElements !== "undefined" && !customElements.get("eg4-tou-timeline-card")) {
  class Eg4TouTimelineCard extends HTMLElement {
    setConfig(config) {
      this._config = config || {};
      if (!this.shadowRoot) {
        this.attachShadow({ mode: "open" });
        this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div id="b"></div></ha-card>`;
        this._b = this.shadowRoot.getElementById("b");
      }
      this._entity = this._config.entity || null;
      this._last = null;
    }
    set hass(hass) { this._hass = hass; this._render(); }
    connectedCallback() { this._timer = setInterval(() => this._render(true), 30000); }
    disconnectedCallback() { clearInterval(this._timer); }
    getCardSize() { return 5; }
    static getStubConfig() { return {}; }

    _find() {
      if (this._entity && this._hass.states[this._entity]) return this._hass.states[this._entity];
      for (const id of Object.keys(this._hass.states)) {
        if (id.startsWith("sensor.") && id.endsWith("_active_profile") && this._hass.states[id].attributes.timeline) {
          this._entity = id; return this._hass.states[id];
        }
      }
      return null;
    }
    _render(force) {
      if (!this._hass || !this._b) return;
      const st = this._find();
      if (!st) {
        this._b.innerHTML = `<div class="msg">EG4 TOU Manager not found. Set up the integration (v0.5.0 or newer), or set <code>entity:</code> to its Active profile sensor.</div>`;
        return;
      }
      const now = nowMinutes((this._hass.config || {}).time_zone);
      const key = st.last_updated + "|" + now;
      if (!force && key === this._last) return;
      this._last = key;
      const tl = st.attributes.timeline;
      const title = this._config.title === undefined ? `Today's schedule (${st.state} profile)` : this._config.title;
      this._b.innerHTML = tl ? renderTimeline(tl, now, title)
        : `<div class="msg">The Active profile sensor has no timeline data. Update EG4 Local TOU Manager to v0.5.0 or newer and restart Home Assistant.</div>`;
    }
  }
  customElements.define("eg4-tou-timeline-card", Eg4TouTimelineCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "eg4-tou-timeline-card", name: "EG4 TOU timeline",
    description: "24-hour view of today's peak, hold and charge windows (EG4 Local TOU Manager)." });
}

if (typeof module !== "undefined" && module.exports) module.exports = { toMin, pct, renderTimeline, nowMinutes, esc, STYLE };
