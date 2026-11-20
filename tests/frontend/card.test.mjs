import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { toMin, pct, renderTimeline, esc } = require("../../custom_components/eg4_tou/frontend/eg4-tou-timeline-card.js");

const model = {
  peaks: [["07:00", "11:00"], ["17:00", "21:00"]],
  hold: { plan: [["00:00", "07:00"], ["11:00", "17:00"], ["21:00", "23:59"]], inverter: [["00:00", "07:00"], ["11:00", "17:00"], ["21:00", "23:59"]] },
  charge: { plan: [["01:00", "05:00"]], inverter: [["01:00", "05:00"]] },
};

test("time maths: 23:59 is end of day, percentages", () => {
  assert.equal(toMin("07:30"), 450);
  assert.equal(toMin("23:59"), 1440);
  assert.equal(pct(720), 50);
  assert.equal(pct(5000), 100);
});

test("renders a lane per rule, bars at the right place, now marker", () => {
  const h = renderTimeline(model, 15 * 60 + 5, "Today");
  assert.match(h, /On-peak hours/); assert.match(h, /Hold windows/); assert.match(h, /Overnight charge/);
  assert.match(h, /left:29\.17%;width:16\.67%/);           // peak 07:00-11:00 (420/1440, 240/1440)
  assert.match(h, /left:62\.85%/);                         // now = 15:05 -> 905/1440
  assert.match(h, /15:05/);
  assert.equal((h.match(/inverter matches/g) || []).length, 2);
  assert.ok(!h.includes("inverter differs") && !h.includes('track inv'));
});

test("a differing inverter shows its own track and a warning", () => {
  const m = structuredClone(model);
  m.charge.inverter = [["02:00", "06:00"]];
  const h = renderTimeline(m, 0, "");
  assert.match(h, /inverter differs/);
  assert.match(h, /track inv/);
  assert.match(h, /Inverter now: 02:00–06:00/);
});

test("unread inverter is flagged, not drawn as a wrong bar", () => {
  const m = structuredClone(model); m.hold.inverter = null;
  const h = renderTimeline(m, 0, "");
  assert.match(h, /inverter not read/);
  assert.equal((h.match(/class="track inv"/g) || []).length, 0);
});

test("no windows says none; text is escaped", () => {
  const h = renderTimeline({ peaks: [], hold: { plan: [], inverter: [] }, charge: { plan: [], inverter: [] } }, 0, "<b>x</b>");
  assert.match(h, /none/);
  assert.ok(!h.includes("<b>x</b>") && h.includes("&lt;b&gt;x&lt;/b&gt;"));
  assert.equal(esc(`a&"'<`), "a&amp;&quot;&#39;&lt;");
});

test("hostile strings in the data never become markup (timeline card)", () => {
  const evil = '<img src=x onerror=alert(1)>';
  const m = { peaks: [[evil, evil]], hold: { plan: [[evil, evil]], inverter: [[evil, evil]] }, charge: { plan: [[evil, evil]], inverter: null } };
  const h = renderTimeline(m, 0, evil);
  assert.ok(!/<img/i.test(h), "an <img> tag leaked through");
  assert.ok(h.includes("&lt;img"), "the text should be shown escaped");
});

// ---- 12/24 hour and the editor ------------------------------------------------------------
const T = require("../../custom_components/eg4_tou/frontend/eg4-tou-timeline-card.js");

test("clock preference: card setting, then profile, then language/browser", () => {
  assert.equal(T.resolveH24({ time_format: "12" }, undefined, "en-GB"), false);
  assert.equal(T.resolveH24({ time_format: "24" }, undefined, "en-US"), true);
  assert.equal(T.resolveH24({ time_format: "24" }, 12, "en-US"), false);           // card option wins
  assert.equal(T.resolveH24({ time_format: "language", language: "en-US" }, undefined, "de-DE"), false);
  assert.equal(T.resolveH24({ time_format: "language", language: "de" }, undefined, "en-US"), true);
  assert.equal(T.resolveH24({ time_format: "system" }, undefined, "en-US"), false);
  assert.equal(T.resolveH24(undefined, undefined, "en-GB"), true);
});

test("time formatting in both clocks", () => {
  assert.equal(T.fmtTime(17 * 60, true), "17:00");
  assert.equal(T.fmtTime(17 * 60, false), "5:00 PM");
  assert.equal(T.fmtTime(0, false), "12:00 AM");
  assert.equal(T.fmtTime(12 * 60, false), "12:00 PM");
  assert.equal(T.fmtTime(1440, true), "24:00");
  assert.equal(T.fmtHHMM("23:59", false), "12:00 AM");
  assert.equal(T.fmtHHMM("01:05", false), "1:05 AM");
  assert.equal(T.tickLabel(15, false), "3p"); assert.equal(T.tickLabel(0, false), "12a"); assert.equal(T.tickLabel(12, false), "12p");
  assert.equal(T.tickLabel(9, true), "09");
});

test("12 hour view labels bars, text and axis", () => {
  const h = T.renderTimeline(model, 15 * 60 + 5, "Today", { h24: false });
  assert.match(h, /1:00 AM–5:00 AM/); assert.match(h, /3:05 PM/); assert.match(h, />3p</);
  assert.ok(!/>15</.test(h));
});

test("Edit button only when editable", () => {
  assert.ok(!T.renderTimeline(model, 0, "T").includes("data-act=\"edit\""));
  assert.match(T.renderTimeline(model, 0, "T", { editable: true }), /data-act="edit"/);
});

const settings = { charge_start: "01:00", charge_end: "05:00", end_soc_summer: 80, end_soc_winter: 70, end_soc_offpeak: 50, ac_charge_kw: 5 };

test("draft, dragging snaps to 15 minutes and keeps a valid window", () => {
  const d = T.draftFrom(settings);
  assert.deepEqual([d.start, d.end], [60, 300]);
  assert.equal(T.draftFrom({}), null);
  assert.equal(T.dragTo(d, "start", 100, 0).start, 105);
  assert.equal(T.dragTo(d, "start", 400, 0).start, 285);       // cannot pass the end
  assert.equal(T.dragTo(d, "end", 30, 0).end, 75);              // cannot pass the start
  assert.equal(T.dragTo(d, "end", 5000, 0).end, 1440);
  const m = T.dragTo(d, "move", 600, 30);                       // grabbed 30 min into the bar
  assert.deepEqual([m.start, m.end], [570, 810]);
  const edge = T.dragTo(d, "move", 5000, 0);
  assert.equal(edge.end, 1440); assert.equal(edge.end - edge.start, 240);
});

test("changes lists only what differs; end of day saves as 23:59", () => {
  const base = T.draftFrom(settings);
  assert.deepEqual(T.changes({ ...base }, base), {});
  assert.deepEqual(T.changes({ ...base, start: 90, end_soc_winter: 60 }, base), { charge_start: "01:30", end_soc_winter: 60 });
  assert.deepEqual(T.changes({ ...base, end: 1440, ac_charge_kw: 7.5 }, base), { charge_end: "23:59", ac_charge_kw: 7.5 });
});

test("peak overlap warning logic", () => {
  const peaks = [["07:00", "11:00"], ["17:00", "21:00"]];
  assert.equal(T.overlapsPeak({ start: 60, end: 300 }, peaks), false);
  assert.equal(T.overlapsPeak({ start: 360, end: 430 }, peaks), true);
  assert.equal(T.overlapsPeak({ start: 420, end: 660 }, peaks), true);
  assert.equal(T.overlapsPeak({ start: 660, end: 700 }, peaks), false);   // touching is fine
});

test("editor markup: handles, expanders, sliders, active profile, Save disabled until changed", () => {
  const base = T.draftFrom(settings);
  const h = T.renderEditor(base, base, { model, now: 600, h24: true, profile: "winter" });
  assert.match(h, /data-drag="start"/); assert.match(h, /data-drag="end"/); assert.match(h, /data-drag="move"/);
  assert.equal((h.match(/<details/g) || []).length, 2);
  assert.equal((h.match(/type="range"/g) || []).length, 4);
  assert.match(h, /sl active[^>]*><span class="sln">Winter weekday <em>\(today\)/);
  assert.match(h, /data-act="save" disabled/);
  const h2 = T.renderEditor({ ...base, end_soc_winter: 60 }, base, { model, now: 600, h24: false, profile: "summer" });
  assert.ok(!h2.includes('data-act="save" disabled'));
});
