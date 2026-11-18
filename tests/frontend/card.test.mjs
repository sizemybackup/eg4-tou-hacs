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
