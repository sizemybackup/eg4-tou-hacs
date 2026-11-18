/*
 * eg4-tou-rules-card: "TOU & AC First Rules" status table for the EG4 Local TOU Manager.
 * Served by the integration (loaded automatically). All countdowns are computed live in the
 * browser from the next 8 days of schedule that the integration publishes.
 *
 *   type: custom:eg4-tou-rules-card
 *   # everything below is optional
 *   entity: sensor.eg4_tou_active_profile      # found automatically
 *   time_format: 12                            # 12 or 24 (default: follows your Home Assistant profile)
 *   battery_soc_entity: sensor.my_battery_soc               # adds a Battery row
 *   battery_charge_power_entity: sensor.my_charge_watts     # W, optional
 *   battery_discharge_power_entity: sensor.my_discharge_watts
 *   load_power_entity: sensor.my_house_load_watts           # with the two below, adds Peak Reserve
 *   battery_capacity_kwh: 32.1
 *   soc_floor: 20                              # % the battery is allowed to fall to
 */
const DAY = 1440;

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const toMin = (hhmm) => {
  const [h, m] = String(hhmm).split(":").map(Number);
  return h === 23 && m === 59 ? DAY : h * 60 + m;
};

function fmtTime(min, h24) {
  const v = ((min % DAY) + DAY) % DAY, h = Math.floor(v / 60), m = v % 60;
  if (h24) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}
function dur(min) {
  const v = Math.max(0, Math.round(min)), d = Math.floor(v / DAY), h = Math.floor((v % DAY) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${v % 60}m`;
  return `${v}m`;
}

/** Minutes-from-today-midnight (may be >= 1440 for later days) as "5:00 PM" or "Thu 7:00 AM". */
function labelAt(abs, week, h24) {
  const i = Math.floor(abs / DAY);
  const t = fmtTime(abs % DAY, h24);
  return i > 0 && week[i] ? `${esc(week[i].dow)} ${t}` : t;
}

const inWin = (ws, min) => (ws || []).find(([a, b]) => toMin(a) <= min && min < toMin(b)) || null;

/** Hold/charge windows for day i: the inverter's own windows for today when known, else the plan. */
function windowsFor(model, i, kind) {
  if (i === 0 && model.useInverter) {
    const inv = ((model.timeline || {})[kind] || {}).inverter;
    if (inv) return inv;
  }
  return model.week[i][kind] || [];
}

function nextPeakChange(model, now) {
  const w = inWin(model.week[0].peaks, now);
  if (w) return { abs: toMin(w[1]), peak: false, endAbs: toMin(w[1]) };
  for (let i = 0; i < model.week.length; i++) {
    const starts = (model.week[i].peaks || []).map((p) => i * DAY + toMin(p[0])).filter((t) => t > now).sort((a, b) => a - b);
    if (starts.length) return { abs: starts[0], peak: true };
  }
  return null;
}

function nextHoldFlip(model, now) {
  const stateAt = (i, m) => !!inWin(windowsFor(model, i, "hold"), m);
  const cur = stateAt(0, now);
  const cands = new Set();
  model.week.forEach((_, i) => windowsFor(model, i, "hold").forEach(([a, b]) => { cands.add(i * DAY + toMin(a)); cands.add(i * DAY + toMin(b)); }));
  for (const t of [...cands].filter((x) => x > now).sort((a, b) => a - b)) {
    const i = Math.floor(t / DAY);
    if (i >= model.week.length) break;
    const s = stateAt(i, t % DAY);
    if (s !== cur) return { abs: t, toGrid: s };
  }
  return null;
}

function chargeInfo(model, now) {
  const w = inWin(windowsFor(model, 0, "charge"), now);
  if (w) return { inWindow: true, endAbs: toMin(w[1]) };
  let nextAbs = null;
  for (let i = 0; i < model.week.length && nextAbs === null; i++) {
    const starts = windowsFor(model, i, "charge").map((p) => i * DAY + toMin(p[0])).filter((t) => t > now).sort((a, b) => a - b);
    if (starts.length) nextAbs = starts[0];
  }
  return { inWindow: false, nextAbs, any: model.week.some((_, i) => windowsFor(model, i, "charge").length) };
}

const row = (icon, color, label, html, nb) =>
  `<tr${nb ? ' class="nb"' : ""}><td class="i"><ha-icon icon="${icon}" style="color:${color}"></ha-icon></td><td class="l"><b>${label}</b></td><td>${html}</td></tr>`;

/**
 * Pure. model = { week, timeline, facts, useInverter }, now = minutes since midnight today,
 * v = optional live readings { soc, chargeW, dischargeW, loadW } (numbers or null),
 * o = { h24, capacityKwh, floor }.
 */
function renderRules(model, now, v, o) {
  const h24 = !!o.h24, T = (abs) => labelAt(abs, model.week, h24);
  const wk = model.week[0], f = model.facts || {};
  const rows = [];

  const summer = wk.season === "summer";
  rows.push(row(summer ? "mdi:white-balance-sunny" : "mdi:snowflake", summer ? "#ff9800" : "#42a5f5", "Season",
    `<b>${esc(wk.season[0].toUpperCase() + wk.season.slice(1))}</b> · ${esc(wk.day_type)}`));

  const pk = nextPeakChange(model, now);
  const inPeak = !!pk && !pk.peak;
  const holdNow = !!inWin(windowsFor(model, 0, "hold"), now);
  rows.push(row("mdi:cash-clock", inPeak ? "var(--error-color,#db4437)" : "var(--success-color,#43a047)", "Billing&nbsp;Window",
    inPeak ? "🔴 <b>Peak</b>" : "🟢 <b>Off-Peak</b>", true));

  // Peak Reserve: needs battery readings and the configured capacity
  if (inPeak) {
    let txt = null;
    if (holdNow) txt = "⚡ Grid is powering the house (AC First): battery not in use";
    else if (v && v.soc != null && v.loadW != null && o.capacityKwh) {
      const usable = Math.max(0, ((v.soc - (o.floor ?? 20)) / 100) * o.capacityKwh);
      const kw = Math.max(0, v.loadW) / 1000, need = kw * ((pk.endAbs - now) / 60);
      const cl = usable >= need * 1.1 ? "✅ Covered" : usable >= need ? "🟡 Tight" : "🔴 Short";
      txt = `${cl} · <b>${usable.toFixed(1)} kWh</b> usable / <b>${need.toFixed(1)} kWh</b> needed for the rest of the peak at ${kw.toFixed(1)} kW`;
    }
    if (txt) rows.push(row("mdi:battery-heart-variant", "#ffb300", "Peak&nbsp;Reserve", txt, true));
  }

  let nxt = "—";
  if (pk) {
    nxt = pk.peak
      ? (pk.abs >= DAY ? `Peak starts <b>${T(pk.abs)}</b> (in ${dur(pk.abs - now)})` : `Peak starts in <b>${dur(pk.abs - now)}</b> (${T(pk.abs)})`)
      : `Off-Peak starts in <b>${dur(pk.abs - now)}</b> (${T(pk.abs)})`;
  }
  rows.push(row("mdi:clock-outline", "#78909c", "Next&nbsp;Window", nxt, true));

  const flip = nextHoldFlip(model, now);
  rows.push(row("mdi:power-plug-battery", "#ffb300", "Power&nbsp;Source",
    (holdNow ? "⚡ <b>Grid</b> (AC First)" : "🔋 <b>Battery</b>") +
    (flip ? ` · ${flip.toGrid ? "Grid" : "Battery"} in <b>${dur(flip.abs - now)}</b> (${T(flip.abs)})` : ""), true));

  const ch = chargeInfo(model, now);
  const charging = v && v.chargeW != null && v.chargeW > 100;
  let gc;
  if (ch.inWindow) {
    let st;
    if (charging) st = `⚡ <b>Charging</b> to ${esc(f.end_soc)}%`;
    else if (v && v.soc != null && f.end_soc != null && v.soc >= f.end_soc) st = `✅ <b>Target ${esc(f.end_soc)}% reached</b>`;
    else st = `⏳ <b>Window open</b> (charges below ${esc(f.start_soc)}%)`;
    gc = `${st} · ends in <b>${dur(ch.endAbs - now)}</b> (${T(ch.endAbs)})`;
  } else gc = ch.any ? "💤 <b>Idle</b>" : "No grid-charge window set";
  rows.push(row("mdi:battery-charging-high", "#43a047", "Grid&nbsp;Charging", gc, true));

  if (!ch.inWindow && ch.nextAbs !== null) {
    rows.push(row("mdi:calendar-clock", "#43a047", "Next&nbsp;Charge",
      `in <b>${dur(ch.nextAbs - now)}</b> (${T(ch.nextAbs)}) · Start/Stop SOC: ${esc(f.start_soc)}%/${esc(f.end_soc)}% at ${esc(f.ac_kw)} kW`, true));
  }

  const mode = { read_only: " · read-only, nothing is being written", off: " · controller paused", live: "" }[f.mode] ?? "";
  rows.push(row("mdi:sync", f.out_of_sync ? "var(--warning-color,#ffa600)" : "var(--success-color,#43a047)", "Inverter&nbsp;Schedule",
    (f.out_of_sync ? `⚠️ <b>${esc(f.out_of_sync)} setting${f.out_of_sync === 1 ? "" : "s"} differ</b> from the plan` : "✅ <b>Matches the plan</b>") + mode, true));

  if (v && v.soc != null) {
    let act = "idle";
    if (v.chargeW > 20) act = `charging ${Math.round(v.chargeW)} W`;
    else if (v.dischargeW > 20) act = `discharging ${Math.round(v.dischargeW)} W`;
    let eta = "";
    if (v.chargeW > 20 && o.capacityKwh && f.end_soc > v.soc) {
      const m = ((f.end_soc - v.soc) / 100) * o.capacityKwh / (v.chargeW / 1000) * 60;
      eta = ` · ${dur(m)} to ${esc(f.end_soc)}%`;
    }
    rows.push(row("mdi:battery-70", "#66bb6a", "Battery", `${Math.round(v.soc)}% · ${act}${eta}`));
  }
  // last row needs no divider tweak; the table CSS handles it
  return `<table>${rows.join("")}</table>`;
}

/** Pick today's entry from the published week; tolerate a short delay after midnight. */
function buildModel(attrs, todayIso) {
  const week = (attrs.week || []);
  const i = week.findIndex((d) => d.date === todayIso);
  if (i < 0) return null;
  return { week: week.slice(i), timeline: attrs.timeline, facts: attrs.facts, useInverter: i === 0 };
}

function nowParts(timeZone) {
  const now = new Date();
  const p = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone }).formatToParts(now);
  const g = (t) => (p.find((x) => x.type === t) || {}).value;
  return { date: `${g("year")}-${g("month")}-${g("day")}`, min: (Number(g("hour")) % 24) * 60 + Number(g("minute")) };
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 4px 12px; }
  table { width: 100%; border-collapse: collapse; }
  td { padding: 9px 6px; border-top: 1px solid var(--divider-color); vertical-align: middle; }
  tr:first-child td { border-top: none; }
  tr.nb td { border-top: none; }
  td.i { width: 1px; padding-right: 2px; }
  td.l { white-space: nowrap; }
  .msg { padding: 14px 8px; color: var(--secondary-text-color); }
`;

if (typeof HTMLElement !== "undefined" && typeof customElements !== "undefined" && !customElements.get("eg4-tou-rules-card")) {
  class Eg4TouRulesCard extends HTMLElement {
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
    connectedCallback() { this._timer = setInterval(() => this._render(true), 20000); }
    disconnectedCallback() { clearInterval(this._timer); }
    getCardSize() { return 8; }
    static getStubConfig() { return {}; }

    _find() {
      const S = this._hass.states;
      if (this._entity && S[this._entity]) return S[this._entity];
      for (const id of Object.keys(S)) {
        if (id.startsWith("sensor.") && id.endsWith("_active_profile") && S[id].attributes.week) { this._entity = id; return S[id]; }
      }
      return null;
    }
    _num(id) {
      if (!id || !this._hass.states[id]) return null;
      const n = parseFloat(this._hass.states[id].state);
      return Number.isFinite(n) ? n : null;
    }
    _render(force) {
      if (!this._hass || !this._b) return;
      const c = this._config, st = this._find();
      if (!st) { this._b.innerHTML = `<div class="msg">EG4 TOU Manager not found. Set up the integration (v0.6.0 or newer), or set <code>entity:</code>.</div>`; return; }
      const np = nowParts((this._hass.config || {}).time_zone);
      const v = {
        soc: this._num(c.battery_soc_entity), chargeW: this._num(c.battery_charge_power_entity),
        dischargeW: this._num(c.battery_discharge_power_entity), loadW: this._num(c.load_power_entity),
      };
      const key = [st.last_updated, np.date, np.min, v.soc, v.chargeW, v.dischargeW, v.loadW].join("|");
      if (!force && key === this._last) return;
      this._last = key;
      const model = buildModel(st.attributes, np.date);
      if (!model) { this._b.innerHTML = `<div class="msg">Waiting for the integration's next update (it refreshes every 10 minutes).</div>`; return; }
      const lt = this._hass.locale && this._hass.locale.time_format;
      const h24 = c.time_format ? Number(c.time_format) === 24 : lt === "24";
      this._b.innerHTML = renderRules(model, np.min, v, { h24, capacityKwh: c.battery_capacity_kwh, floor: c.soc_floor });
    }
  }
  customElements.define("eg4-tou-rules-card", Eg4TouRulesCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "eg4-tou-rules-card", name: "EG4 TOU & AC First rules",
    description: "Season, billing window, next window, power source, grid charging and more, with live countdowns (EG4 Local TOU Manager)." });
}

if (typeof module !== "undefined" && module.exports) module.exports = { renderRules, buildModel, toMin, fmtTime, dur, nextPeakChange, nextHoldFlip, chargeInfo, labelAt, STYLE };
