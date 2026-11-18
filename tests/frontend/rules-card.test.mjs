import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { renderRules, buildModel, fmtTime, dur, toMin } = require("../../custom_components/eg4_tou/frontend/eg4-tou-rules-card.js");

const WINTER_PEAKS = [["07:00", "11:00"], ["17:00", "21:00"]];
const WINTER_HOLD = [["00:00", "07:00"], ["11:00", "17:00"], ["21:00", "23:59"]];
const day = (date, dow, day_type, work) => ({
  date, dow, season: "winter", day_type,
  peaks: work ? WINTER_PEAKS : [], hold: work ? WINTER_HOLD : [["00:00", "23:59"]], charge: [["01:00", "05:00"]],
});
const facts = { mode: "read_only", out_of_sync: 0, start_soc: 65, end_soc: 70, ac_kw: 5 };
const timelineOf = (d) => ({ peaks: d.peaks, hold: { plan: d.hold, inverter: d.hold }, charge: { plan: d.charge, inverter: d.charge } });
// Wed 2026-11-18 .. following week
const wed = [day("2026-11-18", "Wed", "Workday", 1), day("2026-11-19", "Thu", "Workday", 1), day("2026-11-20", "Fri", "Workday", 1),
  day("2026-11-21", "Sat", "Weekend", 0), day("2026-11-22", "Sun", "Weekend", 0), day("2026-11-23", "Mon", "Workday", 1),
  day("2026-11-24", "Tue", "Workday", 1), day("2026-11-25", "Wed", "Workday", 1)];
const fri = wed.slice(2).concat([day("2026-11-26", "Thu", "Holiday: Thanksgiving", 0), day("2026-11-27", "Fri", "Workday", 1)]);
const model = (week, attrsFacts = facts) => buildModel({ week, timeline: timelineOf(week[0]), facts: attrsFacts }, week[0].date);
const text = (html) => html.replace(/<[^>]*>/g, " ").replace(/&nbsp;/g, " ").replace(/\s+/g, " ");
const at = (h, m = 0) => h * 60 + m;
const O = { h24: false };

test("format helpers", () => {
  assert.equal(fmtTime(at(17), false), "5:00 PM"); assert.equal(fmtTime(0, false), "12:00 AM");
  assert.equal(fmtTime(at(17, 5), true), "17:05");
  assert.equal(dur(90), "1h 30m"); assert.equal(dur(45), "45m"); assert.equal(dur(3420), "2d 9h");
  assert.equal(toMin("23:59"), 1440);
});

test("mid-peak: Peak, off-peak countdown, battery now and grid later", () => {
  const t = text(renderRules(model(wed), at(9, 30), null, O));
  assert.match(t, /Season Winter · Workday/);
  assert.match(t, /Billing Window .*Peak/);
  assert.match(t, /Next Window Off-Peak starts in 1h 30m \(11:00 AM\)/);
  assert.match(t, /Power Source .*Battery · Grid in 1h 30m \(11:00 AM\)/);
  assert.match(t, /Grid Charging .*Idle/);
  assert.match(t, /Next Charge in 15h 30m \(Thu 1:00 AM\) · Start\/Stop SOC: 65%\/70% at 5 kW/);
});

test("afternoon off-peak: grid now, battery and peak in 4h", () => {
  const t = text(renderRules(model(wed), at(13), null, O));
  assert.match(t, /Billing Window .*Off-Peak/);
  assert.match(t, /Next Window Peak starts in 4h 0m \(5:00 PM\)/);
  assert.match(t, /Power Source .*Grid \(AC First\) · Battery in 4h 0m \(5:00 PM\)/);
});

test("Friday night: next peak and battery are Monday, weekend holds all day", () => {
  const t = text(renderRules(model(fri), at(22), null, O));
  assert.match(t, /Next Window Peak starts Mon 7:00 AM \(in 2d 9h\)/);
  assert.match(t, /Power Source .*Grid \(AC First\) · Battery in 2d 9h \(Mon 7:00 AM\)/);
  assert.match(t, /Next Charge in 3h 0m \(Sat 1:00 AM\)/);
});

test("charge window open: charging, target reached, or waiting", () => {
  const m = model(wed);
  assert.match(text(renderRules(m, at(2), { soc: 60, chargeW: 4800 }, O)), /Charging to 70% . ends in 3h 0m \(5:00 AM\)/);
  assert.match(text(renderRules(m, at(2), { soc: 70, chargeW: 0 }, O)), /Target 70% reached/);
  assert.match(text(renderRules(m, at(2), null, O)), /Window open \(charges below 65%\)/);
  assert.ok(!text(renderRules(m, at(2), null, O)).includes("Next Charge"));
});

test("Peak Reserve: covered, short, and hidden outside peak or without readings", () => {
  const m = model(wed), o = { h24: false, capacityKwh: 32, floor: 20 };
  assert.match(text(renderRules(m, at(8), { soc: 60, loadW: 3000 }, o)), /Peak Reserve .*Covered . 12\.8 kWh usable \/ 9\.0 kWh needed/);
  assert.match(text(renderRules(m, at(8), { soc: 30, loadW: 3000 }, o)), /Peak Reserve .*Short/);
  assert.ok(!text(renderRules(m, at(13), { soc: 60, loadW: 3000 }, o)).includes("Peak Reserve"));
  assert.ok(!text(renderRules(m, at(8), null, o)).includes("Peak Reserve"));
  const hold = structuredClone(wed); hold[0].peaks = [["07:00", "11:00"]]; hold[0].hold = [["00:00", "23:59"]];
  assert.match(text(renderRules(model(hold), at(8), { soc: 60, loadW: 3000 }, o)), /Peak Reserve .*Grid is powering the house/);
});

test("inverter schedule row reports differences and the mode", () => {
  assert.match(text(renderRules(model(wed), at(9), null, O)), /Inverter Schedule .*Matches the plan . read-only, nothing is being written/);
  const f = { ...facts, out_of_sync: 2, mode: "live" };
  assert.match(text(renderRules(model(wed, f), at(9), null, O)), /2 settings differ from the plan/);
});

test("battery row shows soc, activity and time to target", () => {
  const t = text(renderRules(model(wed), at(2), { soc: 55, chargeW: 5000, dischargeW: 0 }, { h24: false, capacityKwh: 32 }));
  assert.match(t, /Battery 55% . charging 5000 W . 58m to 70%/);
  assert.match(text(renderRules(model(wed), at(13), { soc: 80, chargeW: 0, dischargeW: 900 }, O)), /Battery 80% . discharging 900 W/);
});

test("24h clock and midnight rollover", () => {
  assert.match(text(renderRules(model(wed), at(13), null, { h24: true })), /Peak starts in 4h 0m \(17:00\)/);
  // poll still shows yesterday: model shifts to today's entry, plan windows only
  const m = buildModel({ week: wed, timeline: timelineOf(wed[0]), facts }, "2026-11-19");
  assert.equal(m.week[0].date, "2026-11-19"); assert.equal(m.useInverter, false);
  assert.equal(buildModel({ week: wed, facts }, "2026-12-30"), null);
});

test("hostile strings in the data never become markup (rules card)", () => {
  const evil = '<img src=x onerror=alert(1)>';
  const week = wed.map((d) => ({ ...d, dow: evil, day_type: evil, season: evil }));
  const f = { mode: evil, out_of_sync: evil, start_soc: evil, end_soc: evil, ac_kw: evil };
  const m = buildModel({ week, timeline: timelineOf(week[0]), facts: f }, week[0].date);
  for (const now of [at(2), at(9), at(13), at(22)]) {
    const h = renderRules(m, now, { soc: 50, chargeW: 5000, loadW: 2000 }, { h24: false, capacityKwh: 32, floor: 20 });
    assert.ok(!/<img/i.test(h), "an <img> tag leaked through at minute " + now);
  }
});
