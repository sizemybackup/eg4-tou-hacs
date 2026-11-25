import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { renderStorm, serviceFor, when, resolveH24 } = require("../../custom_components/eg4_tou/frontend/eg4-tou-storm-card.js");

const attrs = {
  headline: "High Wind Warning", source: "nws", onset: "2026-11-26T04:00:00-05:00", ends: "2026-11-26T19:00:00-05:00",
  alerts: [{ event: "High Wind Warning", onset: "2026-11-26T04:00:00-05:00", ends: "2026-11-26T19:00:00-05:00" }],
  prep: false, automatic: true, skipped: false, target_soc: 100, target_range: [20, 100], until: null,
  controls: { prep: "switch.p", automatic: "switch.a", skip: "button.s", target: "number.t" },
};
const opts = { h24: false, timeZone: "America/New_York" };

test("times are shown in Home Assistant's zone, in either clock", () => {
  assert.match(when(attrs.onset, false, "America/New_York"), /^Thu 4:00\sAM$/);
  assert.equal(when(attrs.ends, true, "America/New_York"), "Thu 19:00");
  assert.equal(when(null, true), "");
  assert.equal(when("nonsense", true), "");
  assert.equal(resolveH24({ time_format: "24" }, undefined, "en-US"), true);
  assert.equal(resolveH24({ time_format: "24" }, 12, "en-US"), false);
});

test("recommendation, alert times and the single row of controls", () => {
  const h = renderStorm("prep", attrs, { ...opts, soc: 43.2 });
  assert.match(h, /Prep recommended/);
  assert.match(h, /High Wind Warning<\/b> Thu 4:00\sAM to Thu 7:00\sPM/);
  assert.match(h, /Battery 43% · target 100%/);
  assert.match(h, /Prep: Off/); assert.match(h, /Auto: On/); assert.match(h, />Skip</);
  assert.equal((h.match(/<div class="row">/g) || []).length, 1);
  assert.match(h, /data-act="up"[^>]* disabled/);          // already at the maximum
  assert.doesNotMatch(h, /data-act="down"[^>]* disabled/);
});

test("the note follows the session", () => {
  assert.match(renderStorm("prep", { ...attrs, prep: true, until: "2026-11-26T22:00:00-05:00" }, opts), /Normal schedule returns <b>Thu 10:00\sPM/);
  assert.match(renderStorm("prep", { ...attrs, skipped: true }, opts), /This storm is skipped/);
  assert.match(renderStorm("clear", { ...attrs, automatic: false, alerts: [], headline: "" }, opts), /Nothing changes until/);
  assert.match(renderStorm("not_configured", { ...attrs, alerts: [], headline: "" }, opts), /Add weather zones/);
  assert.match(renderStorm("unknown", { ...attrs, alerts: [], headline: "" }, opts), /Weather data unavailable/);
  assert.match(renderStorm("watch", { ...attrs, source: "forecast", headline: "Forecast: wind to 30 mph sustained", alerts: [] }, opts), /wind to 30 mph/);
});

test("buttons call the integration's own entities", () => {
  assert.deepEqual(serviceFor("prep", attrs), ["switch", "turn_on", { entity_id: "switch.p" }]);
  assert.deepEqual(serviceFor("prep", { ...attrs, prep: true }), ["switch", "turn_off", { entity_id: "switch.p" }]);
  assert.deepEqual(serviceFor("auto", attrs), ["switch", "turn_off", { entity_id: "switch.a" }]);
  assert.deepEqual(serviceFor("skip", attrs), ["button", "press", { entity_id: "button.s" }]);
  assert.deepEqual(serviceFor("down", attrs), ["number", "set_value", { entity_id: "number.t", value: 95 }]);
  assert.deepEqual(serviceFor("up", { ...attrs, target_soc: 98 }), ["number", "set_value", { entity_id: "number.t", value: 100 }]);
  assert.equal(serviceFor("skip", { ...attrs, controls: {} }), null);
  assert.equal(serviceFor("other", attrs), null);
});

test("says when nothing will reach the inverter", () => {
  const warning = "Read-only mode: this is the plan only. Nothing is written to your inverter.";
  const on = renderStorm("prep", { ...attrs, prep: true, warning }, opts);
  assert.match(on, /role="alert"/);
  assert.match(on, /Nothing is written to your inverter/);
  assert.match(on, /The plan: charge to 100%/);
  assert.doesNotMatch(on, /Charging to/);
  assert.match(renderStorm("clear", { ...attrs, warning }, opts), /role="alert"/);        // before turning it on, too
  const live = renderStorm("prep", { ...attrs, prep: true, warning: "" }, opts);
  assert.doesNotMatch(live, /role="alert"/);
  assert.match(live, /Charging to 100%/);
  assert.ok(!renderStorm("prep", { ...attrs, warning: "<img src=x>" }, opts).includes("<img"));
});

test("weather text is escaped", () => {
  const h = renderStorm("prep", { ...attrs, source: "forecast", headline: "<img src=x>", alerts: [{ event: "<script>" }], target_soc: "<i>" }, opts);
  assert.ok(!h.includes("<img") && !h.includes("<script>") && !h.includes("<i>"));
});
