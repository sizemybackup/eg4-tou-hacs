"""Full HA flow against the fake gateway: config flow, dry run, then live writes."""
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eg4_tou.const import DOMAIN
from custom_components.eg4_tou.modbus import pack_time
from tests.test_modbus import FakeGateway

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
async def gateway():
    g = FakeGateway({66: 100, 68: pack_time("02:00"), 69: pack_time("06:00"), 160: 30, 161: 90}, noise=True)
    g.port = await g.start()
    yield g
    g.server.close()


def entry_for(gw):
    return MockConfigEntry(domain=DOMAIN, unique_id="t", data={
        "host": "127.0.0.1", "port": gw.port, "unit": 1, "tariff": "bge_rl", "country": "US", "subdiv": "MD"})


@pytest.fixture(autouse=True)
def _no_real_network_scan():
    """Flow tests must never scan the real network; scan tests override this."""
    with patch("custom_components.eg4_tou.config_flow.async_scan", AsyncMock(return_value=[])):
        yield


async def _manual(hass):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert r["type"] == "form" and r["step_id"] == "user"
    return r


async def test_config_flow_creates_entry(hass: HomeAssistant, gateway):
    r = await _manual(hass)
    assert r["type"] == "form"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1, "country": "US", "subdiv": "MD"})
    assert r["step_id"] == "tariff"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"tariff": "bge_rl", "offpeak_weekends": True})
    assert r["step_id"] == "mode"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"mode": "read_only"})
    assert r["type"] == "create_entry"
    assert r["options"] == {"enabled": True, "dry_run": True}


async def _to_mode(hass, gateway):
    r = await _manual(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1, "country": "US", "subdiv": "MD"})
    return await hass.config_entries.flow.async_configure(r["flow_id"], {"tariff": "bge_rl", "offpeak_weekends": True})


async def test_read_write_requires_confirmation(hass: HomeAssistant, gateway):
    r = await _to_mode(hass, gateway)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"mode": "read_write"})
    assert r["step_id"] == "confirm"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"understood": False})
    assert r["type"] == "form" and r["errors"] == {"understood": "must_confirm"}
    assert gateway.writes == [], "nothing may be written before the confirmation is ticked"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"understood": True})
    assert r["type"] == "create_entry" and r["options"]["dry_run"] is False
    await hass.async_block_till_done()
    assert gateway.writes, "after confirming read-write, writes begin"


async def test_read_only_flow_never_writes(hass: HomeAssistant, gateway):
    r = await _to_mode(hass, gateway)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"mode": "read_only"})
    await hass.async_block_till_done()
    assert gateway.writes == []
    assert hass.states.get("sensor.eg4_tou_controller_status").state == "read_only"


async def test_config_flow_unreachable(hass: HomeAssistant):
    r = await _manual(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": 1, "unit": 1, "country": "US", "subdiv": ""})
    assert r["type"] == "form" and r["errors"] == {"base": "cannot_connect"}


async def test_default_is_off_and_never_writes(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert gateway.writes == []
    assert hass.states.get("sensor.eg4_tou_controller_status").state == "off"
    assert int(hass.states.get("sensor.eg4_tou_registers_out_of_sync").state) > 0


async def test_dry_run_does_not_write(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    hass.config_entries.async_update_entry(e, options={"enabled": True, "dry_run": True})
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert gateway.writes == []
    assert hass.states.get("sensor.eg4_tou_controller_status").state == "read_only"


async def test_live_mode_converges_then_stays_quiet(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    hass.config_entries.async_update_entry(e, options={"enabled": True, "dry_run": False})
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert gateway.writes, "live mode should have written differences"
    assert hass.states.get("sensor.eg4_tou_registers_out_of_sync").state == "0"
    n = len(gateway.writes)
    coordinator = hass.data[DOMAIN][e.entry_id]
    await coordinator.async_refresh()
    assert len(gateway.writes) == n, "second cycle must be idempotent"
    # end SOC lowered 90 -> target: start SOC (160) must be written before end SOC (161)
    regs = [r for r, _ in gateway.writes]
    assert regs.index(160) < regs.index(161)


async def _custom_start(hass, gateway, kind):
    r = await _manual(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1, "country": "US", "subdiv": "MD"})
    return await hass.config_entries.flow.async_configure(r["flow_id"], {"tariff": kind, "offpeak_weekends": True})


async def test_custom_seasonal_flow_builds_tariff(hass: HomeAssistant, gateway):
    f = hass.config_entries.flow
    r = await _custom_start(hass, gateway, "seasonal")
    assert r["step_id"] == "summer_dates"
    r = await f.async_configure(r["flow_id"], {"start_month": "5", "start_day": 15, "end_month": "9", "end_day": 15})
    assert r["step_id"] == "peak" and r["description_placeholders"]["season"] == "summer"
    r = await f.async_configure(r["flow_id"], {"start": "14:00:00", "end": "19:00:00", "add_another": False})
    assert r["step_id"] == "peak" and r["description_placeholders"]["season"] == "winter"
    r = await f.async_configure(r["flow_id"], {"start": "06:00:00", "end": "09:00:00", "add_another": True})
    assert r["description_placeholders"]["n"] == "2"
    r = await f.async_configure(r["flow_id"], {"start": "17:00:00", "end": "20:00:00"})
    assert r["step_id"] == "mode"
    r = await f.async_configure(r["flow_id"], {"mode": "read_only"})
    c = r["data"]["custom_tariff"]
    assert r["data"]["tariff"] == "custom"
    assert c["seasons"]["winter"] == {"start": [9, 16], "end": [5, 14]}
    assert c["peaks"] == {"summer": [["14:00", "19:00"]], "winter": [["06:00", "09:00"], ["17:00", "20:00"]]}


async def test_custom_single_flow(hass: HomeAssistant, gateway):
    f = hass.config_entries.flow
    r = await _custom_start(hass, gateway, "single")
    assert r["step_id"] == "single_kind"
    r = await f.async_configure(r["flow_id"], {"kind": "summer"})
    r = await f.async_configure(r["flow_id"], {"start": "16:00:00", "end": "21:00:00", "add_another": False})
    r = await f.async_configure(r["flow_id"], {"mode": "read_only"})
    assert r["data"]["custom_tariff"]["peaks"] == {"summer": [["16:00", "21:00"]]}


async def test_peak_validation_errors(hass: HomeAssistant, gateway):
    f = hass.config_entries.flow
    r = await _custom_start(hass, gateway, "single")
    r = await f.async_configure(r["flow_id"], {"kind": "winter"})
    r = await f.async_configure(r["flow_id"], {"start": "20:00:00", "end": "17:00:00", "add_another": False})
    assert r["errors"] == {"base": "peak_order"}
    r = await f.async_configure(r["flow_id"], {"start": "07:00:00", "end": "11:00:00", "add_another": True})
    r = await f.async_configure(r["flow_id"], {"start": "10:00:00", "end": "12:00:00"})
    assert r["errors"] == {"base": "peak_overlap"}


async def test_summer_cannot_be_whole_year(hass: HomeAssistant, gateway):
    r = await _custom_start(hass, gateway, "seasonal")
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"start_month": "1", "start_day": 1, "end_month": "12", "end_day": 31})
    assert r["errors"] == {"base": "season_full_year"}


async def test_custom_tariff_drives_the_inverter_plan(hass: HomeAssistant, gateway):
    e = MockConfigEntry(domain=DOMAIN, unique_id="c", data={
        "host": "127.0.0.1", "port": gateway.port, "unit": 1, "tariff": "custom", "country": "US", "subdiv": "",
        "custom_tariff": {"offpeak_weekends": False,
                          "seasons": {"winter": {"start": [1, 1], "end": [12, 31]}},
                          "peaks": {"winter": [["16:00", "20:00"]]}}},
        options={"enabled": True, "dry_run": False})
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert gateway.regs.get(152, 0) == pack_time("00:00") and gateway.regs[153] == pack_time("16:00")
    assert gateway.regs[154] == pack_time("20:00") and gateway.regs[155] == pack_time("23:59")


async def test_options_flow_changes_tariff(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    f = hass.config_entries.options
    r = await f.async_init(e.entry_id)
    assert r["step_id"] == "tariff"
    r = await f.async_configure(r["flow_id"], {"tariff": "single", "offpeak_weekends": True})
    r = await f.async_configure(r["flow_id"], {"kind": "winter"})
    r = await f.async_configure(r["flow_id"], {"start": "18:00:00", "end": "22:00:00", "add_another": False})
    assert r["type"] == "create_entry"
    assert e.data["tariff"] == "custom" and e.data["custom_tariff"]["peaks"] == {"winter": [["18:00", "22:00"]]}


from custom_components.eg4_tou.scanner import Found, scan_hosts


def _default(r, key):
    return [k for k in r["data_schema"].schema if k == key][0].default()


async def test_single_screen_prefills_discovered_gateway(hass: HomeAssistant, gateway):
    found = [Found("127.0.0.1", True), Found("127.0.0.2", False)]
    with patch("custom_components.eg4_tou.config_flow.async_scan", AsyncMock(return_value=found)):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert r["step_id"] == "user"
    assert _default(r, "host") == "127.0.0.1" and _default(r, "port") == 502
    note = r["description_placeholders"]["scan_result"]
    assert "Detected by the network scan" in note and "pre-filled from this scan" in note
    assert "`127.0.0.1`: looks like an EG4 inverter" in note and "`127.0.0.2`: port 502 open, not confirmed" in note
    # accept the prefilled gateway on this same screen -> straight to the tariff step
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1, "country": "US", "subdiv": "MD"})
    assert r["step_id"] == "tariff"


async def test_custom_address_still_works_when_scan_finds_something_else(hass: HomeAssistant, gateway):
    with patch("custom_components.eg4_tou.config_flow.async_scan", AsyncMock(return_value=[Found("127.0.0.2", False)])):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1, "country": "US", "subdiv": ""})  # typed, not discovered
    assert r["step_id"] == "tariff"


async def test_nothing_found_shows_message_and_plain_field(hass: HomeAssistant):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert "did not detect a gateway" in r["description_placeholders"]["scan_result"]
    assert _default(r, "host") == ""


async def test_scan_again_keeps_typed_values_and_refreshes(hass: HomeAssistant):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch("custom_components.eg4_tou.config_flow.async_scan", AsyncMock(return_value=[Found("127.0.0.1", True)])):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {
            "host": "", "port": 5020, "unit": 3, "country": "CA", "subdiv": "ON", "rescan": True})
    assert r["step_id"] == "user" and r["errors"] == {}
    assert _default(r, "host") == "127.0.0.1"          # filled from the new scan
    assert (_default(r, "port"), _default(r, "unit"), _default(r, "country")) == (5020, 3, "CA")  # kept


async def test_unreachable_gateway_error_on_same_screen(hass: HomeAssistant):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": 1, "unit": 1, "country": "US", "subdiv": ""})
    assert r["step_id"] == "user" and r["errors"] == {"base": "cannot_connect"}


async def test_scan_hosts_identifies_eg4_and_ignores_closed(gateway):
    found = await scan_hosts(["127.0.0.1"], port=gateway.port)
    assert [(f.host, f.looks_like_eg4) for f in found] == [("127.0.0.1", True)]
    assert await scan_hosts(["127.0.0.1"], port=1) == []  # nothing listening


async def test_scan_flags_non_eg4_modbus_device(gateway):
    gateway.regs[160] = 40000  # a different device: values not 0..100
    found = await scan_hosts(["127.0.0.1"], port=gateway.port)
    assert found[0].looks_like_eg4 is False


async def test_predictable_entity_ids_and_rules_attribute(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    for eid in ("sensor.eg4_tou_active_profile", "sensor.eg4_tou_registers_out_of_sync",
                "sensor.eg4_tou_controller_status", "switch.eg4_tou_controller_enabled",
                "switch.eg4_tou_read_only_mode", "number.eg4_tou_ac_charge_power",
                "number.eg4_tou_charge_limit_winter_weekday", "time.eg4_tou_charge_start"):
        assert hass.states.get(eid), eid
    rules = hass.states.get("sensor.eg4_tou_active_profile").attributes["rules"]
    assert {r["rule"] for r in rules} >= {"Hold windows (AC First)", "AC charge power"}


async def test_timeline_card_is_served_and_registered(hass: HomeAssistant, hass_client, gateway):
    from homeassistant.components.frontend import add_extra_js_url  # noqa: F401  (import check)
    e = entry_for(gateway)
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()
    for name, tag in (("eg4-tou-timeline-card.js", "eg4-tou-timeline-card"), ("eg4-tou-rules-card.js", "eg4-tou-rules-card")):
        r = await client.get(f"/eg4_tou/{name}")
        assert r.status == 200
        assert f'customElements.define("{tag}"' in await r.text()
    tl = hass.states.get("sensor.eg4_tou_active_profile").attributes["timeline"]
    assert tl["peaks"] and tl["hold"]["plan"] and tl["charge"]["plan"]
    urls = hass.data["frontend_extra_module_url"].urls
    assert any(u.startswith("/eg4_tou/eg4-tou-timeline-card.js?v=") for u in urls)
    assert any(u.startswith("/eg4_tou/eg4-tou-rules-card.js?v=") for u in urls)
    a = hass.states.get("sensor.eg4_tou_active_profile").attributes
    assert len(a["week"]) == 8 and a["week"][0]["peaks"] is not None
    assert a["facts"]["mode"] == "off" and a["facts"]["start_soc"] == 65


async def test_abandoned_attempt_does_not_block_a_new_one(hass: HomeAssistant, gateway):
    """Close the wizard half-way, open it again: the second attempt must still work."""
    first = await _manual(hass)
    r1 = await hass.config_entries.flow.async_configure(first["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1, "country": "US", "subdiv": ""})
    assert r1["step_id"] == "tariff"                       # attempt 1 is now abandoned mid-way
    second = await _manual(hass)
    r2 = await hass.config_entries.flow.async_configure(second["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1, "country": "US", "subdiv": ""})
    assert r2["type"] == "form" and r2["step_id"] == "tariff"


async def test_bge_preset_uses_bge_holidays_and_custom_can_opt_out(hass: HomeAssistant, gateway):
    from datetime import date
    e = entry_for(gateway)                                   # BGE preset, country US / subdiv MD
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    c = hass.data[DOMAIN][e.entry_id]
    assert c._day_info(date(2026, 11, 11)) == (False, "Workday")           # Veterans Day: BGE bills peak
    assert c._day_info(date(2026, 11, 27)) == (False, "Workday")           # day after Thanksgiving
    assert c._day_info(date(2026, 11, 26)) == (True, "Holiday: Thanksgiving")
    assert c._day_info(date(2026, 11, 28)) == (True, "Weekend")
    assert c._day_info(date(2027, 3, 26))[1] == "Holiday: Good Friday"


async def test_custom_flow_stores_the_holiday_choice(hass: HomeAssistant, gateway):
    f = hass.config_entries.flow
    r = await _manual(hass)
    r = await f.async_configure(r["flow_id"], {"host": "127.0.0.1", "port": gateway.port, "unit": 1, "country": "US", "subdiv": "MD"})
    r = await f.async_configure(r["flow_id"], {"tariff": "single", "offpeak_weekends": True, "offpeak_holidays": False})
    r = await f.async_configure(r["flow_id"], {"kind": "winter"})
    r = await f.async_configure(r["flow_id"], {"start": "16:00:00", "end": "20:00:00", "add_another": False})
    r = await f.async_configure(r["flow_id"], {"mode": "read_only"})
    assert r["data"]["custom_tariff"]["offpeak_holidays"] is False
    assert r["data"]["custom_tariff"]["offpeak_weekends"] is True
