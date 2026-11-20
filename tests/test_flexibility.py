"""Write groups, entity-backed driver, auto-detection, custom hold windows, richer tariffs, limits."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eg4_tou import detect
from custom_components.eg4_tou.const import DOMAIN
from custom_components.eg4_tou.drivers import EntityDriver, DriverError
from custom_components.eg4_tou.modbus import pack_time
from custom_components.eg4_tou.planner import Settings, build_plan, tariff_from_custom
from tests.test_integration import _call, _no_real_network_scan, _setup, gateway  # noqa: F401  (fixtures re-exported)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


# ---- write groups ---------------------------------------------------------------------------

async def test_a_switched_off_group_is_neither_compared_nor_written(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway, enabled=True, dry_run=False)
    gateway.regs[154] = pack_time("03:33")                    # something of the user's own in a hold slot
    gateway.writes.clear()
    await _call(hass, e, write_groups={"hold_windows": False})
    assert hass.states.get("switch.eg4_tou_manage_hold_windows").state == "off"
    assert hass.states.get("switch.eg4_tou_manage_charge_window").state == "on"
    await e.runtime_data.async_refresh()
    assert not [r for r, _ in gateway.writes if 152 <= r <= 157], "the hold windows are left alone"
    assert gateway.regs[154] == pack_time("03:33")
    rows = {r["rule"]: r for r in hass.states.get("sensor.eg4_tou_active_profile").attributes["rules"]}
    assert rows["Hold windows (AC First)"]["in_sync"] is None
    assert hass.states.get("sensor.eg4_tou_registers_out_of_sync").state == "0"
    await _call(hass, e, write_groups={"hold_windows": True})
    assert gateway.regs[154] != pack_time("03:33"), "switching it back on restores the plan"


async def test_group_switch_toggles_the_option(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.eg4_tou_manage_charge_limits"}, blocking=True)
    assert e.options["write_groups"]["soc"] is False
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.eg4_tou_manage_charge_limits"}, blocking=True)
    assert e.options["write_groups"]["soc"] is True


async def test_get_plan_returns_plain_settings(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    out = await hass.services.async_call(DOMAIN, "get_plan", {}, blocking=True, return_response=True)
    assert out["profile"] in ("winter", "summer", "offpeak") and out["mode"] == "off"
    assert set(out) >= {"charge_windows", "hold_windows", "start_soc", "end_soc", "ac_charge_kw", "managed", "pending", "tariff"}
    assert out["charge_windows"] == [["01:00", "05:00"]] and out["ac_charge_kw"] == 5.0
    attrs = hass.states.get("sensor.eg4_tou_active_profile").attributes
    assert attrs["plan"]["end_soc"] == out["end_soc"]
    assert attrs["timeline"]["managed"] == {"hold": True, "charge": True}
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "get_plan", {"entry_id": "nope"}, blocking=True, return_response=True)


# ---- custom hold windows --------------------------------------------------------------------

async def test_custom_hold_windows_replace_the_derived_ones(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway, enabled=True, dry_run=False)
    prof = hass.states.get("sensor.eg4_tou_active_profile").state
    await _call(hass, e, hold={prof: [["00:00", "06:00"]]})
    assert (gateway.regs.get(152, 0), gateway.regs[153]) == (pack_time("00:00"), pack_time("06:00"))
    assert gateway.regs.get(154, 0) == 0 == gateway.regs.get(155, 0), "unused slots are cleared"
    await _call(hass, e, hold={prof: None})
    assert e.options["hold_custom"] == {}
    for bad, key in (([["05:00", "04:00"]], "hold_order"), ([["00:00", "05:00"], ["04:00", "08:00"]], "hold_overlap"),
                     ([["00:00", "01:00"], ["02:00", "03:00"], ["04:00", "05:00"], ["06:00", "07:00"]], "too_many_hold")):
        with pytest.raises(ServiceValidationError) as exc:
            await _call(hass, e, hold={prof: bad})
        assert exc.value.translation_key == key


# ---- richer tariffs -------------------------------------------------------------------------

CUSTOM3 = {
    "seasons": {"spring": {"start": [3, 1], "end": [5, 31], "like": "summer"},
                "summer": {"start": [6, 1], "end": [9, 30]},
                "autumn": {"start": [10, 1], "end": [2, 28]}},
    "peaks": {"spring": [["16:00", "19:00"]], "summer": [["14:00", "20:00"]], "autumn": [["17:00", "21:00"]]},
    "peak_days": [0, 1, 2, 3, 4, 5],        # Saturday is a peak day too
}


def test_peak_days_and_three_seasons():
    t = tariff_from_custom(CUSTOM3)
    sat, sun, mon = date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6)
    assert t.is_peak_weekday(sat) and not t.is_peak_weekday(sun) and t.is_peak_weekday(mon)
    assert t.season_for(mon) == "spring" and t.like_for(mon) == "summer"
    assert t.like_for(date(2026, 11, 2)) == "winter"           # a season that is neither: behaves like winter
    s = Settings(end_soc={"summer": 80, "winter": 70, "offpeak": 50})
    assert build_plan(mon, t, s, offpeak_day=False).registers[161] == 80          # spring uses the summer limit
    assert build_plan(date(2026, 11, 2), t, s, offpeak_day=False).registers[161] == 70
    assert build_plan(mon, t, s, offpeak_day=False).profile == "spring"


@pytest.mark.parametrize("bad", [{"peak_days": []}, {"peak_days": [7]}, {"peak_days": ["mon"]},
                                 {"seasons": {"x": {"start": [1, 1], "end": [12, 31], "like": "spring"}}, "peaks": {"x": []}}])
def test_custom_tariff_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        tariff_from_custom({**CUSTOM3, **bad})


async def test_import_tariff_with_new_season_names_works_end_to_end(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    one = {"seasons": {"all-year": {"start": [1, 1], "end": [12, 31], "like": "winter"}},
           "peaks": {"all-year": [["17:00", "20:00"]]}, "peak_days": [0, 1, 2, 3, 4, 5, 6]}
    await hass.services.async_call(DOMAIN, "import_tariff", {"name": "Flat", "tariff": one}, blocking=True)
    await hass.async_block_till_done()
    assert e.options["active_tariff"] == "Flat"
    st = hass.states.get("sensor.eg4_tou_active_profile")
    assert st.state == "all-year", "a season name of its own is a valid profile"
    assert hass.states.get("select.eg4_tou_tariff").state == "Flat"
    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(DOMAIN, "import_tariff", {"name": "Bad", "tariff": {"seasons": {}}}, blocking=True)
    assert exc.value.translation_key == "tariff_invalid"


# ---- limits and timing ----------------------------------------------------------------------

async def test_limits_widen_the_sliders_and_the_checks(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    with pytest.raises(ServiceValidationError) as exc:
        await _call(hass, e, end_soc_winter=15)
    assert exc.value.translation_key == "soc_out_of_range"
    await _call(hass, e, soc_min=10, ac_kw_max=12.0, start_soc_gap=8, poll_minutes=3)
    await _call(hass, e, end_soc_winter=15, ac_charge_kw=12)
    assert hass.states.get("number.eg4_tou_ac_charge_power").attributes["max"] == 12.0
    assert hass.states.get("number.eg4_tou_charge_limit_winter_weekday").attributes["min"] == 10
    assert e.runtime_data.update_interval.total_seconds() == 180
    plan = hass.states.get("sensor.eg4_tou_active_profile").attributes["plan"]
    assert plan["end_soc"] - plan["start_soc"] == 8
    with pytest.raises(ServiceValidationError) as exc:
        await _call(hass, e, soc_min=60, soc_max=50)
    assert exc.value.translation_key == "limits_order"
    with pytest.raises(ServiceValidationError) as exc:
        await _call(hass, e, ac_kw_max=5)
    assert exc.value.translation_key == "power_out_of_range"


async def test_configure_limits_and_hold_steps(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    f = hass.config_entries.options
    r = await f.async_init(e.entry_id)
    r = await f.async_configure(r["flow_id"], {"next_step_id": "limits"})
    assert r["step_id"] == "limits"
    bad = await f.async_configure(r["flow_id"], {"poll_minutes": 5, "start_soc_gap": 5, "soc_min": 60, "soc_max": 50, "ac_kw_max": 10})
    assert bad["errors"] == {"base": "limits_order"}
    ok = await f.async_configure(r["flow_id"], {"poll_minutes": 5, "start_soc_gap": 3, "soc_min": 10, "soc_max": 100, "ac_kw_max": 12})
    assert ok["type"] == "create_entry" and e.options["start_soc_gap"] == 3 and e.options["ac_kw_max"] == 12
    r = await f.async_init(e.entry_id)
    r = await f.async_configure(r["flow_id"], {"next_step_id": "hold"})
    bad = await f.async_configure(r["flow_id"], {"hold_custom": {"winter": [["09:00", "08:00"]]}})
    assert bad["errors"] == {"base": "hold_order"}
    bad = await f.async_configure(r["flow_id"], {"hold_custom": {"winter": "nonsense"}})
    assert bad["errors"] == {"base": "bad_hold"}
    ok = await f.async_configure(r["flow_id"], {"hold_custom": {"winter": [["00:00", "07:00"]]}})
    assert ok["type"] == "create_entry" and e.options["hold_custom"] == {"winter": [["00:00", "07:00"]]}
    r = await f.async_init(e.entry_id)
    r = await f.async_configure(r["flow_id"], {"next_step_id": "hold"})
    ok = await f.async_configure(r["flow_id"], {})
    assert e.options["hold_custom"] == {}


# ---- detection ------------------------------------------------------------------------------

def _recs():
    base, recs = "eg4_15kxp_63134a0823_", []
    def add(eid, platform="eg4_web_monitor", device="inv1", **kw):
        recs.append({"entity_id": eid, "name": eid, "platform": platform, "device_id": device, **kw})
    for n in (1, 2, 3):
        for k in ("start", "end"):
            for what in ("ac_charge", "ac_first", "generator_charge"):
                add(f"time.{base}{what}_{k}_time_{n}")
    for x in ("ac_charge_end_battery_soc", "ac_charge_end_voltage", "ac_charge_power", "ac_charge_start_battery_soc", "ac_charge_start_voltage"):
        add(f"number.{base}{x}")
    add("number.basement_eg4_tou_manager_ac_charge_power", platform="eg4_tou", device="mine")
    return recs, base


def test_detect_picks_the_right_entity_for_every_role():
    recs, base = _recs()
    s = detect.suggest(recs)
    assert s and len(s.mapping) == 15
    m = {k: v.split(".")[1].replace(base, "") for k, v in s.mapping.items()}
    assert m["charge_1_start"] == "ac_charge_start_time_1" and m["hold_3_end"] == "ac_first_end_time_3"
    assert m["soc_start"] == "ac_charge_start_battery_soc" and m["soc_end"] == "ac_charge_end_battery_soc"
    assert m["power"] == "ac_charge_power", "not our own entity, not a voltage"


def test_detect_handles_unnumbered_windows_two_inverters_and_noise():
    recs = [{"entity_id": e, "name": e, "platform": "other", "device_id": "d"} for e in (
        "time.ac_charge_start_time", "time.ac_charge_end_time", "number.ac_charge_power",
        "number.ac_charge_start_soc", "number.ac_charge_end_soc", "time.ac_first_start", "time.ac_first_end")]
    s = detect.suggest(recs)
    assert s and s.mapping["charge_1_start"] == "time.ac_charge_start_time" and s.mapping["hold_1_end"] == "time.ac_first_end"
    two, base = _recs()
    other = [{**r, "device_id": "inv2", "entity_id": r["entity_id"].replace("63134a0823", "99999")} for r in two[:6]]
    assert all("63134a0823" in v for v in detect.suggest(two + other).mapping.values()), "one device, never a mix"
    assert detect.suggest([]) is None
    assert detect.suggest([{"entity_id": "sensor.temp", "name": "Temp", "platform": "x"}]) is None
    assert detect.suggest(recs[:2]) is None, "two stray matches are a coincidence"
    assert detect.suggest([{**r, "disabled": True} for r in recs]) is None


# ---- entity-backed driver -------------------------------------------------------------------

def _fake_inverter(hass: HomeAssistant, times: dict[str, str], numbers: dict[str, tuple[float, str | None]]):
    """States for the entities plus service handlers that behave like real time/number entities."""
    for eid, v in times.items():
        hass.states.async_set(eid, v)
    for eid, (v, unit) in numbers.items():
        hass.states.async_set(eid, str(v), {"unit_of_measurement": unit} if unit else {})
    calls: list[tuple[str, str, dict]] = []

    async def handler(call: ServiceCall) -> None:
        calls.append((call.domain, call.service, dict(call.data)))
        eid = call.data["entity_id"]
        new = call.data.get("time") or str(call.data.get("value"))
        hass.states.async_set(eid, new, hass.states.get(eid).attributes)

    for dom, svc in (("time", "set_value"), ("number", "set_value"), ("input_datetime", "set_datetime")):
        hass.services.async_register(dom, svc, handler)
    return calls


MAPPING = {"charge_1_start": "time.cs", "charge_1_end": "time.ce", "soc_start": "number.s0", "soc_end": "number.s1",
           "power": "number.pw", "hold_1_start": "input_datetime.h0", "hold_1_end": "input_datetime.h1"}


async def _entity_entry(hass, mapping=MAPPING, **options):
    e = MockConfigEntry(domain=DOMAIN, unique_id="entities:x", data={
        "backend": "entities", "entities": mapping, "tariff": "bge_rl", "country": "US", "subdiv": "MD"},
        options={"enabled": True, "dry_run": False, **options})
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    return e


async def test_entity_backend_writes_only_what_is_mapped(hass: HomeAssistant):
    calls = _fake_inverter(hass, {"time.cs": "02:00:00", "time.ce": "06:00:00", "input_datetime.h0": "00:00:00", "input_datetime.h1": "00:00:00"},
                           {"number.s0": (30, None), "number.s1": (90, None), "number.pw": (3000, "W")})
    e = await _entity_entry(hass)
    c = e.runtime_data
    assert c.last_update_success
    assert hass.states.get("time.cs").state == "01:00:00" and hass.states.get("time.ce").state == "05:00:00"
    assert hass.states.get("number.pw").state == "5000.0", "power is converted to the entity's own unit (W)"
    assert hass.states.get("number.s1").state in ("70", "70.0")
    assert {d for d, _, _ in calls} == {"time", "number", "input_datetime"}
    assert all(c_[2]["entity_id"] in MAPPING.values() for c_ in calls), "unmapped slots are never touched"
    assert hass.states.get("input_datetime.h0").state.endswith(":00")
    assert hass.states.get("sensor.eg4_tou_registers_out_of_sync").state == "0"
    assert c.data.managed == frozenset({68, 69, 152, 153, 160, 161, 66})
    assert hass.states.get("switch.eg4_tou_manage_hold_windows").state == "on"


async def test_entity_backend_read_only_never_calls_a_service(hass: HomeAssistant):
    calls = _fake_inverter(hass, {"time.cs": "02:00:00", "time.ce": "06:00:00"}, {"number.s0": (30, None), "number.s1": (90, None), "number.pw": (5, "kW")})
    await _entity_entry(hass, {k: v for k, v in MAPPING.items() if not k.startswith("hold")}, dry_run=True)
    assert calls == []
    assert int(hass.states.get("sensor.eg4_tou_registers_out_of_sync").state) > 0


async def test_entity_backend_unavailable_or_garbled_entity_is_a_failed_read(hass: HomeAssistant):
    _fake_inverter(hass, {"time.cs": "unavailable", "time.ce": "06:00:00"}, {"number.s0": (30, None), "number.s1": (90, None), "number.pw": (5, None)})
    e = await _entity_entry(hass, {k: v for k, v in MAPPING.items() if not k.startswith("hold")})
    assert e.state.name in ("SETUP_RETRY",)                     # first refresh failed: nothing written
    hass.states.async_set("time.cs", "banana")
    d = EntityDriver(hass, MAPPING)
    with pytest.raises(DriverError):
        await d.read()


async def test_entity_driver_write_failure_is_reported_and_retried(hass: HomeAssistant):
    _fake_inverter(hass, {"time.cs": "02:00:00", "time.ce": "06:00:00"}, {"number.s0": (30, None), "number.s1": (90, None), "number.pw": (5, "kW")})
    hass.services.async_register("time", "set_value", AsyncMock(side_effect=RuntimeError("boom")))
    e = await _entity_entry(hass, {k: v for k, v in MAPPING.items() if not k.startswith("hold")})
    assert "failed" in e.runtime_data.data.last_write and "boom" in e.runtime_data.data.last_write


async def test_entity_driver_refuses_values_outside_the_whitelist(hass: HomeAssistant):
    d = EntityDriver(hass, {"soc_end": "number.s1"})
    with pytest.raises(Exception):
        await d.write(161, 150)


# ---- the wizard's entity path ---------------------------------------------------------------

async def _register(hass):
    reg = er.async_get(hass)
    recs, _ = _recs()
    ids = []
    for r in recs[:-1]:
        dom, obj = r["entity_id"].split(".")
        reg.async_get_or_create(dom, "eg4_web_monitor", obj, suggested_object_id=obj, original_name=obj.replace("_", " "))
        ids.append(r["entity_id"])
    for i in ids:
        hass.states.async_set(i, "05:00:00" if i.startswith("time.") else "5")
    return ids


async def test_wizard_entity_path_preselects_detected_entities(hass: HomeAssistant):
    await _register(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert "Found 15 matching entities" in r["description_placeholders"]["scan_result"]
    bad = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "x", "port": 502, "unit": 1, "country": "ZZ", "subdiv": "", "use_entities": True})
    assert bad["errors"] == {"country": "bad_country"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "", "port": 502, "unit": 1, "country": "US", "subdiv": "MD", "use_entities": True})
    assert r["step_id"] == "entities"
    sugg = {str(k): k.description["suggested_value"] for k in r["data_schema"].schema if k.description}
    assert len(sugg) == 15 and sugg["power"].endswith("ac_charge_power")
    empty = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert empty["errors"] == {"base": "no_entities"}
    dup = await hass.config_entries.flow.async_configure(r["flow_id"], {"soc_end": sugg["power"], "power": sugg["power"]})
    assert dup["errors"] == {"base": "duplicate_entities"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], sugg)
    assert r["step_id"] == "tariff"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"tariff": "bge_rl", "offpeak_weekends": True})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"mode": "read_only"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"create_dashboard": False})
    assert r["type"] == "create_entry" and r["title"] == "EG4 TOU (Home Assistant entities)"
    assert r["data"]["backend"] == "entities" and r["data"]["entities"]["power"].endswith("ac_charge_power")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.eg4_tou_controller_status").state == "read_only"
    # the same entities again is the same setup
    r2 = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    r2 = await hass.config_entries.flow.async_configure(r2["flow_id"], {
        "host": "", "port": 502, "unit": 1, "country": "US", "subdiv": "", "use_entities": True})
    r2 = await hass.config_entries.flow.async_configure(r2["flow_id"], sugg)
    assert r2["type"] == "abort" and r2["reason"] == "already_configured"


async def test_wizard_entity_path_without_anything_detected(hass: HomeAssistant):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert "Found" not in r["description_placeholders"]["scan_result"]
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "", "port": 502, "unit": 1, "country": "US", "subdiv": "", "use_entities": True})
    assert "Nothing was detected" in r["description_placeholders"]["detected"]
    assert not any(k.description for k in r["data_schema"].schema)


async def test_reconfigure_entity_backend(hass: HomeAssistant):
    _fake_inverter(hass, {"time.cs": "01:00:00", "time.ce": "05:00:00"}, {"number.s0": (65, None), "number.s1": (70, None), "number.pw": (5, "kW")})
    e = await _entity_entry(hass, {k: v for k, v in MAPPING.items() if not k.startswith("hold")}, dry_run=True)
    r = await e.start_reconfigure_flow(hass)
    assert r["step_id"] == "reconfigure_entities"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"country": "US"})
    assert r["errors"] == {"base": "no_entities"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"charge_1_start": "time.cs", "charge_1_end": "time.ce", "country": "US"})
    assert r["type"] == "abort" and r["reason"] == "reconfigure_successful"
    assert e.data["entities"] == {"charge_1_start": "time.cs", "charge_1_end": "time.ce"}


# ---- the dashboard follows renamed entities ---------------------------------------------------

def test_dashboard_localize_swaps_only_the_ids_that_differ():
    from custom_components.eg4_tou.dashboard import id_map, localize

    class E:
        def __init__(self, eid, name): self.entity_id, self.original_name, self.domain = eid, name, eid.split(".")[0]
    m = id_map([E("number.basement_eg4_tou_manager_ac_charge_power", "AC charge power"),
                E("sensor.eg4_tou_active_profile", "Active profile"), E("sensor.x", None)])
    assert m == {"number.eg4_tou_ac_charge_power": "number.basement_eg4_tou_manager_ac_charge_power"}
    cfg = {"cards": [{"entity": "number.eg4_tou_ac_charge_power", "n": 3},
                     {"t": "{{ states('number.eg4_tou_ac_charge_power') }} {{ states('sensor.eg4_tou_active_profile') }}"}]}
    out = localize(cfg, m)
    assert out["cards"][0] == {"entity": "number.basement_eg4_tou_manager_ac_charge_power", "n": 3}
    assert "basement" in out["cards"][1]["t"] and "sensor.eg4_tou_active_profile" in out["cards"][1]["t"]
    assert localize(cfg, {}) == cfg


async def test_created_dashboard_uses_the_installs_own_entity_ids(hass: HomeAssistant, gateway):
    from tests.test_integration import _lovelace
    from custom_components.eg4_tou import dashboard as dash
    await _lovelace(hass)
    e = await _setup(hass, gateway)
    reg = er.async_get(hass)
    reg.async_update_entity("number.eg4_tou_ac_charge_power", new_entity_id="number.garage_ac_power")
    await hass.async_block_till_done()
    assert await dash.async_create_dashboard(hass, e) == dash.CREATED
    cfg = await dash._live_dashboards(hass)[dash.URL_PATH].async_load(False)
    text = str(cfg)
    assert "number.garage_ac_power" in text and "number.eg4_tou_ac_charge_power" not in text
