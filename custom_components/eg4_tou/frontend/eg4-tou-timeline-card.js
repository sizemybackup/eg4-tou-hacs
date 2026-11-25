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

function lane(name, cls, plan, inverter, now, hasInverter, h24, managed) {
  let status = "";
  let invTrack = "";
  if (hasInverter && managed === false) {
    status = `<span class="st">not managed here</span>`;   // switched off, or not mapped: nothing to compare
  } else if (hasInverter) {
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
    (opts && opts.tariff ? `<div class="tariffline">Tariff: <b>${esc(opts.tariff)}</b></div>` : "") +
    lane("On-peak hours (your tariff)", "peak", peaks, null, now, false, h24) +
    lane("Hold windows (AC First)", "hold", (m.hold || {}).plan, (m.hold || {}).inverter, now, true, h24, (m.managed || {}).hold) +
    lane(m.storm ? "Grid charge (storm prep)" : "Overnight charge", "charge", (m.charge || {}).plan, (m.charge || {}).inverter, now, true, h24, (m.managed || {}).charge) +
    axis(now, h24) + `</div>`;
}

// ---- Edit mode -------------------------------------------------------------------------------

const SOC_KEYS = [["end_soc_summer", "Summer weekday", "summer"], ["end_soc_winter", "Winter weekday", "winter"],
  ["end_soc_offpeak", "Weekend / holiday", "offpeak"]];
const LIMITS = { soc: [20, 100, 1], kw: [1, 10, 0.5] };

const snap = (min) => Math.round(min / STEP) * STEP;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const hhmm = (min) => { const m = min >= DAY ? DAY - 1 : min; return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };
const sameWins = (a, b) => a.length === b.length && a.every((w, i) => w.start === b[i].start && w.end === b[i].end);
const winsOf = (list) => (list || []).map(([a, b]) => ({ start: toMin(a), end: toMin(b) })).sort((x, y) => x.start - y.start);
const winStrs = (wins) => wins.map((w) => [hhmm(w.start), hhmm(w.end)]);

/** Draft of the editable settings from the sensor's `settings` and `peak_edit` attributes (null if the integration is too old). */
function draftFrom(settings, pe) {
  if (!settings || !settings.charge_start || !settings.charge_end) return null;
  const a = toMin(settings.charge_start), b = toMin(settings.charge_end);
  const d = { start: a, end: b, end_soc_summer: Number(settings.end_soc_summer), end_soc_winter: Number(settings.end_soc_winter),
    end_soc_offpeak: Number(settings.end_soc_offpeak), ac_charge_kw: Number(settings.ac_charge_kw), peaks: null };
  if (pe && Array.isArray(pe.peaks)) {
    d.peaks = winsOf(pe.peaks); d.def = winsOf(pe.default); d.season = pe.season; d.shift = Number(pe.shift_hours || 0);
    d.max = Number(pe.max_windows || 2); d.overridden = !!pe.overridden;
    d.tariff = pe.tariff || ""; d.builtin = !!pe.builtin; d.tariffs = pe.tariffs || [];
  }
  return d;
}
/** Move a handle (or the whole window) to a pointer position: 15 min minimum length, kept between lo and hi
 *  (the neighbouring window, or the edges of the day). win = {start, end}; returns the new {start, end}. */
function dragWin(win, mode, min, grab, lo = 0, hi = DAY) {
  const len = win.end - win.start;
  if (mode === "start") return { start: clamp(snap(min), lo, win.end - STEP), end: win.end };
  if (mode === "end") return { start: win.start, end: clamp(snap(min), win.start + STEP, hi) };
  const start = clamp(snap(min - (grab || 0)), lo, hi - len);
  return { start, end: start + len };
}
/** The overnight window: same as dragWin on the draft's own start/end (kept for the tests and callers). */
function dragTo(draft, mode, min, grab) { return { ...draft, ...dragWin(draft, mode, min, grab) }; }
/** Room each window has: between the previous window's end and the next one's start. */
const bounds = (peaks, i) => [i > 0 ? peaks[i - 1].end : 0, i < peaks.length - 1 ? peaks[i + 1].start : DAY];
/** If the handle (window id "p0"/"p1", mode start/end) sits exactly on a neighbouring window's handle, the pair
 *  { end: [id, "end"] of the earlier window, start: [id, "start"] of the later one }; else null. */
function touching(peaks, id, mode) {
  if (!peaks || id === "c" || (mode !== "start" && mode !== "end")) return null;
  const i = Number(id.slice(1));
  if (mode === "start" && i > 0 && peaks[i - 1] && peaks[i - 1].end === peaks[i].start) return { end: [`p${i - 1}`, "end"], start: [id, "start"] };
  if (mode === "end" && peaks[i + 1] && peaks[i + 1].start === peaks[i].end) return { end: [id, "end"], start: [`p${i + 1}`, "start"] };
  return null;
}
/** A free hour (or the biggest gap if smaller) for "+ Add window"; null when there is no room for 15 minutes. */
function newPeak(peaks) {
  let best = null, cursor = 0;
  for (const w of [...peaks, { start: DAY, end: DAY }]) {
    if (!best || w.start - cursor > best[1] - best[0]) best = [cursor, w.start];
    cursor = w.end;
  }
  const gap = best[1] - best[0];
  if (gap < STEP) return null;
  const len = Math.min(60, gap), start = clamp(snap(best[0] + (gap - len) / 2), best[0], best[1] - len);
  return { start, end: start + len };
}
/** Hold windows (AC First) the planner derives from these on-peak windows (shifted by `shift` hours in
 *  daylight-saving weeks): the rest of the day, as [[HH:MM, HH:MM], ...]. Mirrors the integration. */
function holdFrom(peaks, shift) {
  const eff = peaks.map((w) => ({ start: w.start + shift * 60, end: Math.min(DAY, w.end + shift * 60) })).filter((w) => w.start < DAY);
  const out = []; let cursor = 0;
  for (const w of eff) { if (w.start > cursor) out.push([cursor, w.start]); cursor = w.end; }
  if (cursor < DAY - 1) out.push([cursor, DAY]);
  return out.map(([x, y]) => [hhmm(x), hhmm(y)]);
}
/** Only the settings that changed, as the update_settings service wants them (empty = nothing to save). */
function changes(draft, base) {
  const out = {};
  if (hhmm(draft.start) !== hhmm(base.start)) out.charge_start = hhmm(draft.start);
  if (hhmm(draft.end) !== hhmm(base.end)) out.charge_end = hhmm(draft.end);
  for (const [k] of SOC_KEYS) if (draft[k] !== base[k]) out[k] = draft[k];
  if (draft.ac_charge_kw !== base.ac_charge_kw) out.ac_charge_kw = draft.ac_charge_kw;
  if (draft.peaks && base.peaks && !sameWins(draft.peaks, base.peaks)) {
    // back to the tariff's own windows = no override
    out.peaks = { [draft.season]: sameWins(draft.peaks, draft.def) ? null : winStrs(draft.peaks) };
  }
  return out;
}
/** Does the charge window overlap an on-peak window (grid charging at peak rates)? peaks: [[HH:MM, HH:MM], ...] */
function overlapsPeak(draft, peaks) {
  return (peaks || []).some(([a, b]) => draft.start < toMin(b) && toMin(a) < draft.end);
}
const durText = (min) => `${Math.floor(min / 60)} h${min % 60 ? ` ${min % 60} min` : ""}`;

function slider(key, label, value, [lo, hi, step], unit, active) {
  return `<label class="sl${active ? " active" : ""}"><span class="sln">${esc(label)}${active ? " <em>(today)</em>" : ""}</span>` +
    `<input type="range" data-key="${key}" min="${lo}" max="${hi}" step="${step}" value="${value}">` +
    `<output data-out="${key}">${value}${unit}</output></label>`;
}

/** The tariff picker row and the "name your tariff" row (shown when a new tariff is being created). */
function tariffBar(d) {
  if (!d.peaks) return "";
  const opts = (d.tariffs || []).map((t) => `<option value="${esc(t.name)}"${t.name === d.tariff ? " selected" : ""}>${esc(t.name)}${t.builtin ? " (built-in)" : ""}</option>`).join("");
  return `<div class="tbar"><label>Tariff <select data-el="tariffsel" aria-label="Active tariff">${opts}</select></label>` +
    `<button class="btn small" data-act="saveas">Save as new tariff…</button>` +
    (d.builtin ? "" : `<button class="btn small" data-act="deltariff">Delete</button>`) + `</div>` +
    `<div class="namerow" data-el="namerow" hidden><div class="txt" data-el="nameinfo"></div>` +
    `<input data-el="newname" maxlength="40" placeholder="Name, e.g. Summer plan" aria-label="New tariff name">` +
    `<button class="btn small primary" data-act="createtariff">Create tariff</button> <button class="btn small" data-act="cancelname">Cancel</button></div>`;
}

/** Pure: the editor. draft/base as from draftFrom(); ctx = { model, now, h24, profile } */
function renderEditor(draft, base, ctx) {
  const { model, now, h24, profile } = ctx;
  const m = model || {};
  const dirty = Object.keys(changes(draft, base)).length > 0;
  const winParts = (id, name, aria) => `<div class="bar ${name} draggable" data-drag="move" data-win="${id}" data-el="bar-${id}"></div>` +
    `<div class="handle" data-drag="start" data-win="${id}" data-el="hs-${id}" role="slider" tabindex="0" aria-label="${aria} start" aria-valuemin="0" aria-valuemax="1425"></div>` +
    `<div class="handle" data-drag="end" data-win="${id}" data-el="he-${id}" role="slider" tabindex="0" aria-label="${aria} end" aria-valuemin="15" aria-valuemax="1440"></div>`;
  const peakLane = draft.peaks
    ? `<div class="lane"><div class="head"><span class="name">On-peak hours (${esc(draft.season)})</span>` +
      `<span><button class="btn small" data-act="addpeak">+ Add window</button> <button class="btn small" data-act="resetpeaks">Reset to tariff</button></span></div>` +
      `<div class="track edit" data-el="track-p"><i class="now" style="left:${pct(now).toFixed(2)}%"></i>${winParts("p0", "peak", "On-peak window 1")}${winParts("p1", "peak", "On-peak window 2")}</div>` +
      [0, 1].map((i) => `<div class="prow" data-el="prow-${i}"><span data-el="ptxt-${i}"></span> <button class="btn small" data-act="rmpeak" data-i="${i}">Remove</button></div>`).join("") +
      `<div class="note" data-el="pnote"></div></div>` +
      `<div class="lane"><div class="head"><span class="name">Hold windows (AC First), follow the peaks</span></div>` +
      `<div class="track" data-el="holdtrack"></div><div class="txt" data-el="holdtxt"></div></div>`
    : lane("On-peak hours (your tariff)", "peak", m.peaks || [], null, now, false, h24);
  return `<div class="card-body editing">` +
    `<div class="titlebar"><div class="title">Edit schedule</div>` +
    `<span><button class="btn" data-act="cancel">Cancel</button> <button class="btn primary" data-act="save"${dirty ? "" : " disabled"}>Save</button></span></div>` +
    tariffBar(draft) +
    peakLane +
    `<div class="lane"><div class="head"><span class="name">Overnight charge window</span><span class="st" data-el="dur"></span></div>` +
    `<div class="track edit" data-el="track-c"><i class="now" style="left:${pct(now).toFixed(2)}%"></i>${winParts("c", "charge", "Charge")}</div>` +
    `<div class="txt" data-el="when"></div><div class="warn" data-el="peakwarn">⚠ This window overlaps an on-peak period, so it would charge from the grid at peak rates.</div></div>` +
    axis(now, h24) +
    `<details class="exp" open><summary>Charge limits (overnight charge stops at)</summary>` +
    SOC_KEYS.map(([k, label, prof]) => slider(k, label, draft[k], LIMITS.soc, "%", prof === profile)).join("") + `</details>` +
    `<details class="exp"><summary>AC charge power</summary>${slider("ac_charge_kw", "Grid charging power", draft.ac_charge_kw, LIMITS.kw, " kW", false)}</details>` +
    `<div class="note">Drag a handle to change a time, or the bar to move the whole window (steps of ${STEP} min; arrow keys work on a focused handle).` +
    (draft.peaks ? " On-peak edits apply to every weekday of that season; weekends and holidays stay all off-peak." : " Peak hours come from your tariff (Configure the integration to change them).") + `</div>` +
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
  .tariffline { font-size: 0.82em; color: var(--secondary-text-color); margin: -2px 0 8px; }
  .tbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; font-size: 0.88em; }
  .tbar select { font: inherit; padding: 4px 6px; border-radius: 6px; max-width: 220px; background: var(--card-background-color, #fff);
    color: var(--primary-text-color); border: 1px solid var(--divider-color, #888); }
  .namerow { margin: 0 0 10px; padding: 8px; border-radius: 8px; background: var(--secondary-background-color, rgba(127,127,127,.12)); display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .namerow[hidden] { display: none; } .namerow .txt { flex-basis: 100%; margin: 0; }
  .namerow input { font: inherit; padding: 5px 8px; border-radius: 6px; flex: 1 1 140px; min-width: 0; border: 1px solid var(--divider-color, #888);
    background: var(--card-background-color, #fff); color: var(--primary-text-color); }
  .btn.small { font-size: 0.78em; padding: 3px 9px; }
  .prow { font-size: 0.8em; color: var(--secondary-text-color); margin-top: 3px; display: flex; gap: 8px; align-items: center; }
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
        this._b.addEventListener("change", (e) => this._change(e));
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
      return this._config.editable !== false && admin && !!draftFrom(st.attributes.settings, null);
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
      this._b.innerHTML = tl ? (this._saved ? `<div class="banner">${esc(this._saved)}</div>` : "") + renderTimeline(tl, now, title, { h24, editable: this._canEdit(st), tariff: (st.attributes.peak_edit || {}).tariff })
        : `<div class="msg">The Active profile sensor has no timeline data. Update EG4 Local TOU Manager to v0.5.0 or newer and restart Home Assistant.</div>`;
    }

    // ---- editing
    _startEdit(st) {
      const base = draftFrom(st.attributes.settings, st.attributes.peak_edit);
      this._edit = { base, draft: { ...base, peaks: base.peaks && base.peaks.map((w) => ({ ...w })) }, st };
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
    /** Position one window's bar and handles. */
    _place(id, w, h24) {
      const bar = this._el(`bar-${id}`), hs = this._el(`hs-${id}`), he = this._el(`he-${id}`);
      for (const el of [bar, hs, he]) el.style.display = w ? "" : "none";
      if (!w) return;
      bar.style.left = pct(w.start).toFixed(2) + "%"; bar.style.width = (pct(w.end) - pct(w.start)).toFixed(2) + "%";
      hs.style.left = pct(w.start).toFixed(2) + "%"; he.style.left = pct(w.end).toFixed(2) + "%";
      hs.setAttribute("aria-valuenow", w.start); he.setAttribute("aria-valuenow", w.end);
      hs.setAttribute("aria-valuetext", fmtTime(w.start, h24)); he.setAttribute("aria-valuetext", fmtTime(w.end, h24));
    }
    /** Update the handles, bars and texts in place (focus and the drag in progress are kept). */
    _sync() {
      const e = this._edit; if (!e) return;
      const d = e.draft, h24 = this._h24(), now = nowMinutes((this._hass.config || {}).time_zone);
      this._place("c", { start: d.start, end: d.end }, h24);
      this._el("dur").textContent = durText(d.end - d.start);
      this._el("when").textContent = `${fmtTime(d.start, h24)} → ${fmtTime(d.end, h24)}`;
      let peaks = (e.st.attributes.timeline || {}).peaks;
      if (d.peaks) {
        [0, 1].forEach((i) => {
          const w = d.peaks[i];
          this._place(`p${i}`, w, h24);
          this._el(`prow-${i}`).style.display = w ? "" : "none";
          if (w) this._el(`ptxt-${i}`).textContent = `Window ${i + 1}: ${fmtTime(w.start, h24)} → ${fmtTime(w.end, h24)}`;
        });
        const add = this._b.querySelector('[data-act="addpeak"]'), reset = this._b.querySelector('[data-act="resetpeaks"]');
        add.disabled = d.peaks.length >= d.max || !newPeak(d.peaks);
        reset.disabled = sameWins(d.peaks, d.def);
        this._el("pnote").textContent = (d.peaks.length ? "" : "No on-peak hours: the battery is never held back. ") +
          (d.shift ? `This week the tariff moves these ${d.shift} h later. ` : "") +
          (e.base.overridden || !sameWins(d.peaks, d.def) ? "Your own hours; Reset to tariff restores the tariff's." : "These are your tariff's hours.");
        const shifted = d.peaks.map((w) => [hhmm(w.start + d.shift * 60), hhmm(w.end + d.shift * 60)]);
        peaks = shifted;
        const hold = holdFrom(d.peaks, d.shift);
        this._el("holdtrack").innerHTML = bars(hold, "hold", now, h24);
        this._el("holdtxt").textContent = hold.length ? "Plan: " + hold.map(([x, y]) => `${fmtHHMM(x, h24)}–${fmtHHMM(y, h24)}`).join(", ") : "none";
      }
      this._el("peakwarn").classList.toggle("show", overlapsPeak(d, peaks));
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
      const a = act.getAttribute("data-act"), ed = this._edit;
      if (a === "edit") { const st = this._find(); if (st) this._startEdit(st); }
      else if (a === "cancel") { this._edit = null; this._last = null; this._render(true); }
      else if (a === "save") this._save();
      else if (ed && a === "saveas") this._nameRow("Save these hours as a new tariff. The current one stays as it is.");
      else if (ed && a === "cancelname") this._el("namerow").hidden = true;
      else if (ed && a === "createtariff") this._createTariff();
      else if (ed && a === "deltariff") this._deleteTariff();
      else if (ed && ed.draft.peaks && a === "addpeak") {
        const w = newPeak(ed.draft.peaks);
        if (w) { ed.draft.peaks = [...ed.draft.peaks, w].sort((x, y) => x.start - y.start); this._sync(); }
      } else if (ed && ed.draft.peaks && a === "rmpeak") {
        ed.draft.peaks = ed.draft.peaks.filter((_, i) => i !== Number(act.getAttribute("data-i"))); this._sync();
      } else if (ed && ed.draft.peaks && a === "resetpeaks") {
        ed.draft.peaks = ed.draft.def.map((w) => ({ ...w })); this._sync();
      }
    }
    _nameRow(info) {
      const row = this._el("namerow"); if (!row) return;
      this._el("nameinfo").textContent = info; row.hidden = false;
      const inp = this._el("newname"); inp.focus();
    }
    async _call(service, data) {
      const st = this._edit.st;
      await this._hass.callService("eg4_tou", service, { entry_id: st.attributes.entry_id, ...data });
    }
    /** Run a service from the editor; on failure show the reason, on success leave edit mode with a banner. */
    async _run(text, fn, done) {
      const e = this._edit; if (!e || e.busy) return;
      e.busy = true; this._sync(); this._msg(text);
      try { await fn(); } catch (err) {
        e.busy = false; this._sync(); this._msg((err && err.message) || "That did not work. Nothing was changed.", "err"); return;
      }
      this._finish(done);
    }
    _finish(text) {
      this._saved = text; this._edit = null; this._last = null; this._render(true);
      clearTimeout(this._savedTimer);
      this._savedTimer = setTimeout(() => { this._saved = ""; this._last = null; this._render(true); }, 12000);
    }
    _change(e) {
      if (!this._edit || !e.target.getAttribute || e.target.getAttribute("data-el") !== "tariffsel") return;
      const name = e.target.value, ed = this._edit;
      if (name === ed.draft.tariff) return;
      if (Object.keys(changes(ed.draft, ed.base)).length && typeof confirm === "function" && !confirm("Discard your unsaved changes and switch tariff?")) {
        e.target.value = ed.draft.tariff; return;
      }
      this._run("Switching…", () => this._call("set_tariff", { name }), `Switched to the “${name}” tariff.`);
    }
    _createTariff() {
      const e = this._edit, name = (this._el("newname").value || "").trim();
      if (!name) { this._msg("Type a name for the new tariff.", "err"); return; }
      const patch = changes(e.draft, e.base), peaks = patch.peaks ? patch.peaks : { [e.draft.season]: winStrs(e.draft.peaks) };
      const rest = { ...patch }; delete rest.peaks;
      this._run("Creating…", async () => {
        await this._call("save_tariff_as", { name, peaks });
        if (Object.keys(rest).length) await this._call("update_settings", rest);
      }, `Saved as the new “${name}” tariff, now active. “${e.draft.tariff}” is unchanged.`);
    }
    _deleteTariff() {
      const e = this._edit, name = e.draft.tariff;
      if (typeof confirm === "function" && !confirm(`Delete the “${name}” tariff? The built-in example becomes active.`)) return;
      this._run("Deleting…", () => this._call("delete_tariff", { name }), `Deleted the “${name}” tariff.`);
    }
    _input(e) {
      const k = e.target.getAttribute && e.target.getAttribute("data-key");
      if (!k || !this._edit) return;
      this._edit.draft[k] = Number(e.target.value);
      this._sync();
    }
    /** The window a handle or bar belongs to: [get, set, lo, hi]. */
    _target(id) {
      const d = this._edit.draft;
      if (id === "c") return { win: { start: d.start, end: d.end }, lo: 0, hi: DAY, set: (w) => { d.start = w.start; d.end = w.end; } };
      const i = Number(id.slice(1)), [lo, hi] = bounds(d.peaks, i);
      return { win: d.peaks[i], lo, hi, set: (w) => { d.peaks[i] = w; } };
    }
    _pointerDown(e) {
      const t = e.target.closest && e.target.closest("[data-drag]");
      if (!t || !this._edit || this._edit.busy) return;
      e.preventDefault();
      let id = t.getAttribute("data-win"), mode = t.getAttribute("data-drag");
      const track = t.closest(".track"), rect = track.getBoundingClientRect(), x0 = e.clientX;
      const at = (ev) => ((ev.clientX - rect.left) / rect.width) * DAY;
      // two touching windows stack their handles: the direction of the drag says which one you meant
      let pair = touching(this._edit.draft.peaks, id, mode);
      let grab = mode === "move" ? at(e) - this._target(id).win.start : 0;
      const move = (ev) => {
        if (pair) {
          if (Math.abs(ev.clientX - x0) < 3) return;
          [id, mode] = ev.clientX < x0 ? pair.end : pair.start;
          pair = null;
        }
        const g = this._target(id); g.set(dragWin(g.win, mode, at(ev), grab, g.lo, g.hi)); this._sync();
      };
      const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
    }
    _key(e) {
      const t = e.target.closest && e.target.closest("[data-drag]");
      const dir = e.key === "ArrowRight" || e.key === "ArrowUp" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : 0;
      if (!t || !dir || !this._edit) return;
      e.preventDefault();
      const mode = t.getAttribute("data-drag"), g = this._target(t.getAttribute("data-win")), by = (e.shiftKey ? 60 : STEP) * dir;
      g.set(dragWin(g.win, mode, (mode === "end" ? g.win.end : g.win.start) + by, 0, g.lo, g.hi));
      this._sync();
    }
    async _save() {
      const e = this._edit, patch = changes(e.draft, e.base);
      if (!Object.keys(patch).length || e.busy) return;
      if (patch.peaks && e.draft.builtin) {   // the shipped example is never changed: name a copy instead
        this._nameRow("The built-in example tariff cannot be changed. Give your hours a name to save them as your own tariff.");
        return;
      }
      const mode = (e.st.attributes.facts || {}).mode;
      const note = mode === "live" ? "Saved. The inverter is updated within a few seconds."
        : mode === "read_only" ? "Saved. Read-only mode is on, so the plan changed but nothing was written to the inverter."
        : "Saved. The controller is paused, so nothing is written to the inverter.";
      this._run("Saving…", () => this._call("update_settings", patch), note);
    }
  }
  customElements.define("eg4-tou-timeline-card", Eg4TouTimelineCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "eg4-tou-timeline-card", name: "EG4 TOU timeline",
    description: "24-hour view of today's peak, hold and charge windows, with an Edit button for the charge window and limits (EG4 Local TOU Manager)." });
}

if (typeof module !== "undefined" && module.exports) module.exports = { toMin, pct, renderTimeline, nowMinutes, esc, STYLE, resolveH24, fmtTime, fmtHHMM, tickLabel, snap, draftFrom, dragTo, dragWin, touching, bounds, newPeak, holdFrom, changes, overlapsPeak, renderEditor, hhmm };
