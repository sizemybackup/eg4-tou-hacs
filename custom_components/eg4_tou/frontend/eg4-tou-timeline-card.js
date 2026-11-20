/*
 * eg4-tou-timeline-card: a 24-hour picture of today's EG4 time-of-use schedule.
 * Served by the EG4 Local TOU Manager integration (loaded automatically; nothing to install).
 *
 *   type: custom:eg4-tou-timeline-card
 *   entity: sensor.eg4_tou_active_profile     # optional, found automatically
 *   time_format: 12                            # 12 or 24 (default: follows your Home Assistant profile)
 *   editable: true                             # false hides the Edit button
 *
 * Lanes: on-peak hours (from your tariff), hold windows (AC First) and the overnight
 * charge window, with a "now" marker. For hold and charge lanes, if the inverter holds
 * something different from the plan, the inverter's windows are drawn underneath.
 *
 * Edit (admins): drag the overnight charge window's handles (or the bar itself) on the 24-hour
 * track, open the sliders for the charge limits and AC charge power, then Save. Save calls the
 * eg4_tou.update_settings service once, so the change applies as a whole. On-peak hours come
 * from your tariff and are not editable here (use Reconfigure on the integration).
 */
const DAY = 1440;
const TICKS = [0, 3, 6, 9, 12, 15, 18, 21, 24];

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function toMin(hhmm) {
  const [h, m] = String(hhmm).split(":").map(Number);
  if (h === 23 && m === 59) return DAY; // 23:59 means end of day
  return h * 60 + m;
}
const STEP = 15; // minutes; the editor snaps to a quarter hour

/** 12 or 24 hour clock: the card's time_format wins, then your Home Assistant profile, then the browser. */
function resolveH24(locale, cfgFormat, browserLocale) {
  if (cfgFormat !== undefined && cfgFormat !== null && cfgFormat !== "") return Number(cfgFormat) === 24;
  const tf = locale && locale.time_format;
  if (tf === "24") return true;
  if (tf === "12") return false;
  // "language" follows the profile language, "system" the browser; "auto" is the older name for both
  const lang = tf === "language" && locale && locale.language ? locale.language : browserLocale;
  try {
    const o = new Intl.DateTimeFormat(lang || undefined, { hour: "numeric" }).resolvedOptions();
    return o.hourCycle ? o.hourCycle === "h23" || o.hourCycle === "h24" : !o.hour12;
  } catch (e) { return true; }
}

/** Minutes since midnight as "17:00" or "5:00 PM" (1440 is the end of the day: "24:00" / "12:00 AM"). */
function fmtTime(min, h24) {
  const m = ((min % 1440) + 1440) % 1440, h = Math.floor(m / 60), mm = String(m % 60).padStart(2, "0");
  if (h24) return `${String(min >= 1440 ? 24 : h).padStart(2, "0")}:${mm}`;
  return `${h % 12 || 12}:${mm} ${h < 12 ? "AM" : "PM"}`;
}
/** "HH:MM" from the integration (23:59 = end of day) in the chosen clock. */
const fmtHHMM = (hhmm, h24) => { const m = toMin(hhmm); return fmtTime(m === 1439 ? 1440 : m, h24); };
/** Axis tick: "03" or "3a" (12 noon is "12p"). */
const tickLabel = (h, h24) => (h24 ? String(h).padStart(2, "0") : `${h % 12 || 12}${h % 24 < 12 ? "a" : "p"}`);
const pct = (min) => Math.max(0, Math.min(100, (min / DAY) * 100));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const dash = (s) => String(s).replace("-", "–");
const rangeText = (ws, h24) => (ws && ws.length ? ws.map(([a, b]) => `${esc(fmtHHMM(a, h24))}–${esc(fmtHHMM(b, h24))}`).join(", ") : "none");

function bars(windows, cls, now, h24) {
  const out = (windows || []).map(([a, b]) => {
    const l = pct(toMin(a)), w = pct(toMin(b)) - l;
    if (w <= 0) return "";
    const label = `${fmtHHMM(a, h24)}–${fmtHHMM(b, h24)}`;
    return `<div class="bar ${cls}" style="left:${l.toFixed(2)}%;width:${w.toFixed(2)}%" title="${esc(label)}"><span class="${w >= 26 ? "big" : w >= 14 ? "mid" : "none"}">${w >= 14 ? esc(label) : ""}</span></div>`;
  });
  return out.join("") + `<i class="now" style="left:${pct(now).toFixed(2)}%"></i>`;
}

function lane(name, cls, plan, inverter, now, hasInverter, h24) {
  let status = "";
  let invTrack = "";
  if (hasInverter) {
    if (inverter === null || inverter === undefined) {
      status = `<span class="st warn">inverter not read</span>`;
    } else if (same(plan, inverter)) {
      status = `<span class="st ok">✓ inverter matches</span>`;
    } else {
      status = `<span class="st warn">⚠ inverter differs</span>`;
      invTrack = `<div class="invcap">Inverter now</div><div class="track inv">${bars(inverter, "invbar", now, h24)}</div>`;
    }
  }
  const text = `${hasInverter ? "Plan: " : ""}${rangeText(plan, h24)}` +
    (invTrack ? `<br>Inverter now: ${rangeText(inverter, h24)}` : "");
  return `<div class="lane"><div class="head"><span class="name">${esc(name)}</span>${status}</div>` +
    `<div class="track">${bars(plan, cls, now, h24)}</div>${invTrack}<div class="txt">${text}</div></div>`;
}

function axis(now, h24) {
  const ticks = TICKS.map((h) => `<span style="left:${(h / 24) * 100}%">${tickLabel(h, h24)}</span>`).join("");
  return `<div class="axis">${ticks}<b class="nowlabel" style="left:${pct(now).toFixed(2)}%">${fmtTime(now, h24)}</b></div>`;
}

/** Pure: model = the sensor's `timeline` attribute, now = minutes since midnight.
 *  opts = { h24 (default true), editable (show the Edit button) } */
function renderTimeline(model, now, title, opts) {
  const m = model || {};
  const h24 = !opts || opts.h24 !== false;
  const peaks = m.peaks || [];
  const edit = opts && opts.editable ? `<button class="btn edit" data-act="edit" title="Change the schedule settings">✎ Edit</button>` : "";
  return `<div class="card-body">` +
    (title || edit ? `<div class="titlebar"><div class="title">${esc(title || "")}</div>${edit}</div>` : "") +
    lane("On-peak hours (your tariff)", "peak", peaks, null, now, false, h24) +
    lane("Hold windows (AC First)", "hold", (m.hold || {}).plan, (m.hold || {}).inverter, now, true, h24) +
    lane("Overnight charge", "charge", (m.charge || {}).plan, (m.charge || {}).inverter, now, true, h24) +
    axis(now, h24) + `</div>`;
}

// ---- Edit mode -------------------------------------------------------------------------------

const SOC_KEYS = [["end_soc_summer", "Summer weekday", "summer"], ["end_soc_winter", "Winter weekday", "winter"],
  ["end_soc_offpeak", "Weekend / holiday", "offpeak"]];
const LIMITS = { soc: [20, 100, 1], kw: [1, 10, 0.5] };

const snap = (min) => Math.round(min / STEP) * STEP;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
/** Draft of the editable settings from the sensor's `settings` attribute (null if the integration is too old). */
function draftFrom(settings) {
  if (!settings || !settings.charge_start || !settings.charge_end) return null;
  const a = toMin(settings.charge_start), b = toMin(settings.charge_end);
  return { start: a, end: b, end_soc_summer: Number(settings.end_soc_summer), end_soc_winter: Number(settings.end_soc_winter),
    end_soc_offpeak: Number(settings.end_soc_offpeak), ac_charge_kw: Number(settings.ac_charge_kw) };
}
/** Move a handle (or the whole window) to a pointer position, keeping 15 min between them and inside the day. */
function dragTo(draft, mode, min, grab) {
  const len = draft.end - draft.start;
  if (mode === "start") return { ...draft, start: clamp(snap(min), 0, draft.end - STEP) };
  if (mode === "end") return { ...draft, end: clamp(snap(min), draft.start + STEP, DAY) };
  const start = clamp(snap(min - (grab || 0)), 0, DAY - len);
  return { ...draft, start, end: start + len };
}
const hhmm = (min) => { const m = min >= DAY ? DAY - 1 : min; return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };
/** Only the settings that changed, as the update_settings service wants them (empty = nothing to save). */
function changes(draft, base) {
  const out = {};
  if (hhmm(draft.start) !== hhmm(base.start)) out.charge_start = hhmm(draft.start);
  if (hhmm(draft.end) !== hhmm(base.end)) out.charge_end = hhmm(draft.end);
  for (const [k] of SOC_KEYS) if (draft[k] !== base[k]) out[k] = draft[k];
  if (draft.ac_charge_kw !== base.ac_charge_kw) out.ac_charge_kw = draft.ac_charge_kw;
  return out;
}
/** Does the charge window overlap an on-peak window (grid charging at peak rates)? */
function overlapsPeak(draft, peaks) {
  return (peaks || []).some(([a, b]) => draft.start < toMin(b) && toMin(a) < draft.end);
}
const durText = (min) => `${Math.floor(min / 60)} h${min % 60 ? ` ${min % 60} min` : ""}`;

function slider(key, label, value, [lo, hi, step], unit, active) {
  return `<label class="sl${active ? " active" : ""}"><span class="sln">${esc(label)}${active ? " <em>(today)</em>" : ""}</span>` +
    `<input type="range" data-key="${key}" min="${lo}" max="${hi}" step="${step}" value="${value}">` +
    `<output data-out="${key}">${value}${unit}</output></label>`;
}

/** Pure: the editor. draft/base as from draftFrom(); ctx = { model, now, h24, profile, mode } */
function renderEditor(draft, base, ctx) {
  const { model, now, h24, profile } = ctx;
  const m = model || {}, peaks = m.peaks || [];
  const dirty = Object.keys(changes(draft, base)).length > 0;
  return `<div class="card-body editing">` +
    `<div class="titlebar"><div class="title">Edit schedule</div>` +
    `<span><button class="btn" data-act="cancel">Cancel</button> <button class="btn primary" data-act="save"${dirty ? "" : " disabled"}>Save</button></span></div>` +
    lane("On-peak hours (your tariff)", "peak", peaks, null, now, false, h24) +
    `<div class="lane"><div class="head"><span class="name">Overnight charge window</span><span class="st" data-el="dur"></span></div>` +
    `<div class="track edit" data-el="track"><i class="now" style="left:${pct(now).toFixed(2)}%"></i>` +
    `<div class="bar charge draggable" data-drag="move" data-el="bar"></div>` +
    `<div class="handle" data-drag="start" data-el="hs" role="slider" tabindex="0" aria-label="Charge start" aria-valuemin="0" aria-valuemax="1425"></div>` +
    `<div class="handle" data-drag="end" data-el="he" role="slider" tabindex="0" aria-label="Charge end" aria-valuemin="15" aria-valuemax="1440"></div></div>` +
    `<div class="txt" data-el="when"></div><div class="warn" data-el="peakwarn">⚠ This window overlaps an on-peak period, so it would charge from the grid at peak rates.</div></div>` +
    axis(now, h24) +
    `<details class="exp" open><summary>Charge limits (overnight charge stops at)</summary>` +
    SOC_KEYS.map(([k, label, prof]) => slider(k, label, draft[k], LIMITS.soc, "%", prof === profile)).join("") + `</details>` +
    `<details class="exp"><summary>AC charge power</summary>${slider("ac_charge_kw", "Grid charging power", draft.ac_charge_kw, LIMITS.kw, " kW", false)}</details>` +
    `<div class="note">The hold windows follow your on-peak hours, so they are not edited here. Peak hours come from your tariff (Reconfigure the integration to change them).</div>` +
    `<div class="msg-line" data-el="msg" role="status"></div></div>`;
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
  .titlebar { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 6px; min-height: 32px; }
  .titlebar .title { margin: 0; }
  .btn { font: inherit; font-size: 0.85em; padding: 5px 12px; border-radius: 16px; cursor: pointer; border: 1px solid var(--divider-color, #888);
    background: transparent; color: var(--primary-text-color); }
  .btn.primary { background: var(--primary-color, #03a9f4); border-color: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  .btn[disabled] { opacity: .45; cursor: default; }
  .track.edit { height: 40px; overflow: visible; touch-action: none; user-select: none; border-radius: 8px; }
  .track.edit .bar { top: 4px; bottom: 4px; border-radius: 6px; }
  .bar.draggable { cursor: grab; } .bar.draggable:active { cursor: grabbing; }
  .handle { position: absolute; top: 0; bottom: 0; width: 22px; margin-left: -11px; cursor: ew-resize; display: flex; align-items: center; justify-content: center; z-index: 2; outline: none; }
  .handle::before { content: ""; width: 8px; height: 30px; border-radius: 4px; background: var(--card-background-color, #fff);
    border: 2px solid var(--success-color, #43a047); box-shadow: 0 1px 3px rgba(0,0,0,.4); box-sizing: border-box; }
  .handle:focus-visible::before { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: 2px; }
  .warn { color: var(--warning-color, #ffa600); font-size: 0.8em; margin-top: 3px; display: none; }
  .warn.show { display: block; }
  .exp { margin-top: 8px; border-top: 1px solid var(--divider-color, rgba(127,127,127,.35)); padding-top: 6px; }
  .exp summary { cursor: pointer; font-size: 0.9em; font-weight: 500; padding: 4px 0; }
  .sl { display: grid; grid-template-columns: minmax(90px, 30%) 1fr 56px; align-items: center; gap: 8px; padding: 4px 0; font-size: 0.88em; }
  .sl.active .sln { font-weight: 600; } .sl em { font-weight: 400; font-style: normal; color: var(--secondary-text-color); }
  .sl input { width: 100%; accent-color: var(--primary-color, #03a9f4); }
  .sl output { text-align: right; font-variant-numeric: tabular-nums; }
  .note { font-size: 0.75em; color: var(--secondary-text-color); margin-top: 8px; }
  .msg-line { font-size: 0.85em; min-height: 1.2em; margin-top: 6px; }
  .msg-line.err { color: var(--error-color, #db4437); } .msg-line.ok { color: var(--success-color, #43a047); }
  .banner { font-size: 0.82em; color: var(--success-color, #43a047); margin-bottom: 6px; }
`;

if (typeof HTMLElement !== "undefined" && typeof customElements !== "undefined" && !customElements.get("eg4-tou-timeline-card")) {
  class Eg4TouTimelineCard extends HTMLElement {
    setConfig(config) {
      this._config = config || {};
      if (!this.shadowRoot) {
        this.attachShadow({ mode: "open" });
        this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div id="b"></div></ha-card>`;
        this._b = this.shadowRoot.getElementById("b");
        this._b.addEventListener("click", (e) => this._click(e));
        this._b.addEventListener("input", (e) => this._input(e));
        this._b.addEventListener("pointerdown", (e) => this._pointerDown(e));
        this._b.addEventListener("keydown", (e) => this._key(e));
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
    _h24() { return resolveH24(this._hass.locale, this._config.time_format, typeof navigator !== "undefined" ? navigator.language : undefined); }
    _canEdit(st) {
      const admin = !this._hass.user || this._hass.user.is_admin !== false;
      return this._config.editable !== false && admin && !!draftFrom(st.attributes.settings);
    }
    _render(force) {
      if (!this._hass || !this._b) return;
      const st = this._find();
      if (!st) {
        this._b.innerHTML = `<div class="msg">EG4 TOU Manager not found. Set up the integration (v0.5.0 or newer), or set <code>entity:</code> to its Active profile sensor.</div>`;
        return;
      }
      if (this._edit) return; // never redraw under the user's fingers; Cancel/Save return to the live view
      const now = nowMinutes((this._hass.config || {}).time_zone);
      const h24 = this._h24();
      const key = st.last_updated + "|" + now + "|" + h24 + "|" + this._canEdit(st) + "|" + (this._saved || "");
      if (!force && key === this._last) return;
      this._last = key;
      const tl = st.attributes.timeline;
      const title = this._config.title === undefined ? `Today's schedule (${st.state} profile)` : this._config.title;
      this._b.innerHTML = tl ? (this._saved ? `<div class="banner">${esc(this._saved)}</div>` : "") + renderTimeline(tl, now, title, { h24, editable: this._canEdit(st) })
        : `<div class="msg">The Active profile sensor has no timeline data. Update EG4 Local TOU Manager to v0.5.0 or newer and restart Home Assistant.</div>`;
    }

    // ---- editing
    _startEdit(st) {
      const base = draftFrom(st.attributes.settings);
      this._edit = { base, draft: { ...base }, st };
      this._saved = "";
      this._drawEditor();
    }
    _drawEditor() {
      const e = this._edit, st = e.st;
      this._b.innerHTML = renderEditor(e.draft, e.base, { model: st.attributes.timeline, now: nowMinutes((this._hass.config || {}).time_zone),
        h24: this._h24(), profile: st.state });
      this._sync();
    }
    _el(n) { return this._b.querySelector(`[data-el="${n}"]`); }
    /** Update the handles, bar and texts in place (the sliders keep focus while you drag). */
    _sync() {
      const e = this._edit; if (!e) return;
      const d = e.draft, h24 = this._h24();
      const bar = this._el("bar");
      bar.style.left = pct(d.start).toFixed(2) + "%"; bar.style.width = (pct(d.end) - pct(d.start)).toFixed(2) + "%";
      const hs = this._el("hs"), he = this._el("he");
      hs.style.left = pct(d.start).toFixed(2) + "%"; he.style.left = pct(d.end).toFixed(2) + "%";
      hs.setAttribute("aria-valuenow", d.start); he.setAttribute("aria-valuenow", d.end);
      hs.setAttribute("aria-valuetext", fmtTime(d.start, h24)); he.setAttribute("aria-valuetext", fmtTime(d.end, h24));
      this._el("dur").textContent = durText(d.end - d.start);
      this._el("when").textContent = `${fmtTime(d.start, h24)} → ${fmtTime(d.end, h24)}. Drag the handles, or the bar to move it (steps of ${STEP} min).`;
      this._el("peakwarn").classList.toggle("show", overlapsPeak(d, (e.st.attributes.timeline || {}).peaks));
      for (const o of this._b.querySelectorAll("output[data-out]")) {
        const k = o.getAttribute("data-out");
        o.textContent = d[k] + (k === "ac_charge_kw" ? " kW" : "%");
      }
      this._b.querySelector('[data-act="save"]').disabled = !Object.keys(changes(d, e.base)).length || e.busy;
    }
    _msg(text, cls) { const m = this._el("msg"); if (m) { m.textContent = text; m.className = "msg-line " + (cls || ""); } }
    _click(e) {
      const act = e.target.closest && e.target.closest("[data-act]");
      if (!act) return;
      const a = act.getAttribute("data-act");
      if (a === "edit") { const st = this._find(); if (st) this._startEdit(st); }
      else if (a === "cancel") { this._edit = null; this._last = null; this._render(true); }
      else if (a === "save") this._save();
    }
    _input(e) {
      const k = e.target.getAttribute && e.target.getAttribute("data-key");
      if (!k || !this._edit) return;
      this._edit.draft[k] = Number(e.target.value);
      this._sync();
    }
    _pointerDown(e) {
      const t = e.target.closest && e.target.closest("[data-drag]");
      if (!t || !this._edit || this._edit.busy) return;
      e.preventDefault();
      const track = this._el("track"), rect = track.getBoundingClientRect(), mode = t.getAttribute("data-drag");
      const at = (ev) => ((ev.clientX - rect.left) / rect.width) * DAY;
      const grab = mode === "move" ? at(e) - this._edit.draft.start : 0;
      const move = (ev) => { this._edit.draft = dragTo(this._edit.draft, mode, at(ev), grab); this._sync(); };
      const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
    }
    _key(e) {
      const t = e.target.closest && e.target.closest("[data-drag]");
      const dir = e.key === "ArrowRight" || e.key === "ArrowUp" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : 0;
      if (!t || !dir || !this._edit) return;
      e.preventDefault();
      const mode = t.getAttribute("data-drag"), d = this._edit.draft, by = (e.shiftKey ? 60 : STEP) * dir;
      this._edit.draft = dragTo(d, mode, (mode === "end" ? d.end : d.start) + by, 0);
      this._sync();
    }
    async _save() {
      const e = this._edit, patch = changes(e.draft, e.base);
      if (!Object.keys(patch).length || e.busy) return;
      e.busy = true; this._sync(); this._msg("Saving…");
      try {
        await this._hass.callService("eg4_tou", "update_settings", { entry_id: e.st.attributes.entry_id, ...patch });
      } catch (err) {
        e.busy = false; this._sync();
        this._msg((err && err.message) || "Could not save. Nothing was changed.", "err");
        return;
      }
      const mode = (e.st.attributes.facts || {}).mode;
      this._saved = mode === "live" ? "Saved. The inverter is updated within a few seconds."
        : mode === "read_only" ? "Saved. Read-only mode is on, so the plan changed but nothing was written to the inverter."
        : "Saved. The controller is paused, so nothing is written to the inverter.";
      this._edit = null; this._last = null; this._render(true);
      clearTimeout(this._savedTimer);
      this._savedTimer = setTimeout(() => { this._saved = ""; this._last = null; this._render(true); }, 12000);
    }
  }
  customElements.define("eg4-tou-timeline-card", Eg4TouTimelineCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "eg4-tou-timeline-card", name: "EG4 TOU timeline",
    description: "24-hour view of today's peak, hold and charge windows, with an Edit button for the charge window and limits (EG4 Local TOU Manager)." });
}

if (typeof module !== "undefined" && module.exports) module.exports = { toMin, pct, renderTimeline, nowMinutes, esc, STYLE, resolveH24, fmtTime, fmtHHMM, tickLabel, snap, draftFrom, dragTo, changes, overlapsPeak, renderEditor, hhmm };
