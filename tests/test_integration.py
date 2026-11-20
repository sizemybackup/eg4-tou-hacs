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
    assert r["step_id"] == "dashboard"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"create_dashboard": False})
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
    assert r["step_id"] == "dashboard"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"create_dashboard": False})
    assert r["type"] == "create_entry" and r["options"]["dry_run"] is False
    await hass.async_block_till_done()
    assert gateway.writes, "after confirming read-write, writes begin"


async def test_read_only_flow_never_writes(hass: HomeAssistant, gateway):
    r = await _to_mode(hass, gateway)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"mode": "read_only"})
    assert r["step_id"] == "dashboard"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"create_dashboard": False})
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
    coordinator = e.runtime_data
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
    assert r["step_id"] == "dashboard"
    r = await f.async_configure(r["flow_id"], {"create_dashboard": False})
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
    assert r["step_id"] == "dashboard"
    r = await f.async_configure(r["flow_id"], {"create_dashboard": False})
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
    c = e.runtime_data
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
    assert r["step_id"] == "dashboard"
    r = await f.async_configure(r["flow_id"], {"create_dashboard": False})
    assert r["data"]["custom_tariff"]["offpeak_holidays"] is False
    assert r["data"]["custom_tariff"]["offpeak_weekends"] is True


# ---- security hardening ---------------------------------------------------------------------

def test_only_private_addresses_are_local():
    from custom_components.eg4_tou.scanner import is_local_address
    for ok in ("192.168.1.200", "10.0.0.5", "172.16.4.9", "127.0.0.1", "169.254.1.1", "fd12::1"):
        assert is_local_address(ok), ok
    for bad in ("8.8.8.8", "1.1.1.1", "203.0.113.7", "100.64.1.1", "172.32.0.1", "2001:4860:4860::8888", "gateway.local", "", "not an ip"):
        assert not is_local_address(bad), bad


def test_host_error_rules():
    from custom_components.eg4_tou.config_flow import host_error
    assert host_error("192.168.1.200") is None and host_error(" 10.1.2.3 ") is None
    assert host_error("gateway.local") is None and host_error("waveshare-1") is None
    assert host_error("8.8.8.8") == "public_address" and host_error("2001:4860:4860::8888") == "public_address"
    for bad in ("", "  ", "a b", "http://x", "host;rm -rf", "x" * 300, "-bad", "bad-", "a..b", "<script>"):
        assert host_error(bad) == "invalid_host", repr(bad)


async def test_scan_never_connects_to_a_public_address():
    from custom_components.eg4_tou.scanner import scan_hosts
    with patch("asyncio.open_connection", side_effect=AssertionError("must not connect")) as oc:
        assert await scan_hosts(["8.8.8.8", "1.1.1.1", "203.0.113.9"]) == []
        oc.assert_not_called()


async def test_candidate_hosts_skip_an_adapter_with_a_public_address(hass: HomeAssistant):
    from custom_components.eg4_tou.scanner import candidate_hosts
    adapters = [
        {"enabled": True, "ipv4": [{"address": "203.0.113.5", "network_prefix": 24}]},      # public: skipped
        {"enabled": True, "ipv4": [{"address": "192.168.1.10", "network_prefix": 24}]},
        {"enabled": True, "ipv4": [{"address": "127.0.0.1", "network_prefix": 8}]},          # loopback: skipped
    ]
    with patch("homeassistant.components.network.async_get_adapters", AsyncMock(return_value=adapters)):
        hosts = await candidate_hosts(hass)
    assert hosts and all(h.startswith("192.168.1.") for h in hosts) and "192.168.1.10" not in hosts
    assert not any(h.startswith("203.0.113.") for h in hosts)


async def test_setup_rejects_public_and_malformed_gateway_addresses(hass: HomeAssistant):
    r = await _manual(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "8.8.8.8", "port": 502, "unit": 1, "country": "US", "subdiv": ""})
    assert r["step_id"] == "user" and r["errors"] == {"host": "public_address"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "bad host!", "port": 502, "unit": 1, "country": "US", "subdiv": ""})
    assert r["errors"] == {"host": "invalid_host"}


async def test_numbers_are_bounded_by_the_form(hass: HomeAssistant):
    r = await _manual(hass)
    for bad in ({"port": 0, "unit": 1}, {"port": 70000, "unit": 1}, {"port": 502, "unit": 0}, {"port": 502, "unit": 300}):
        with pytest.raises(Exception):
            await hass.config_entries.flow.async_configure(r["flow_id"], {
                "host": "192.168.1.5", "country": "US", "subdiv": "", **bad})


async def test_write_switches_are_config_entities_not_voice_exposed(hass: HomeAssistant, gateway):
    from homeassistant.helpers import entity_registry as er
    from homeassistant.const import EntityCategory
    e = entry_for(gateway)
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    reg = er.async_get(hass)
    for eid in ("switch.eg4_tou_read_only_mode", "switch.eg4_tou_controller_enabled"):
        assert reg.async_get(eid).entity_category is EntityCategory.CONFIG, eid


async def test_large_profile_attributes_are_not_recorded(hass: HomeAssistant, gateway):
    """The card data (rules, timeline, week, ...) must not bloat the recorder; only peak_windows is kept."""
    import json
    from custom_components.eg4_tou.sensor import ProfileSensor

    e = entry_for(gateway)
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.eg4_tou_active_profile").attributes
    recorded = set(attrs) - ProfileSensor._unrecorded_attributes
    assert recorded <= {"peak_windows", "friendly_name", "icon", "device_class", "options"}, recorded
    size = len(json.dumps({k: attrs[k] for k in ProfileSensor._unrecorded_attributes if k in attrs}, default=str))
    print(f"unrecorded attribute payload: {size} bytes")


async def test_entity_names_come_from_translations_and_enums(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.eg4_tou_read_only_mode").name == "EG4 TOU Manager Read-only mode"
    assert hass.states.get("number.eg4_tou_charge_limit_weekend_holiday").name == \
        "EG4 TOU Manager Charge limit, weekend / holiday"
    status = hass.states.get("sensor.eg4_tou_controller_status")
    assert status.attributes["device_class"] == "enum"
    assert status.attributes["options"] == ["off", "read_only", "live"] and status.state in status.attributes["options"]
    profile = hass.states.get("sensor.eg4_tou_active_profile")
    assert profile.state in profile.attributes["options"]


async def test_setup_rejects_unknown_country_and_region(hass: HomeAssistant, gateway):
    base = {"host": "127.0.0.1", "port": gateway.port, "unit": 1}
    r = await _manual(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**base, "country": "XX", "subdiv": ""})
    assert r["type"] == "form" and r["errors"] == {"country": "bad_country"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**base, "country": "US", "subdiv": "ZZ"})
    assert r["errors"] == {"subdiv": "bad_subdiv"}
    # case is normalised to the library's codes
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**base, "country": "us", "subdiv": "md"})
    assert r["step_id"] == "tariff"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"tariff": "bge_rl", "offpeak_weekends": True})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"mode": "read_only"})
    assert r["step_id"] == "dashboard"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"create_dashboard": False})
    assert r["data"]["country"] == "US" and r["data"]["subdiv"] == "MD"


async def test_reconfigure_changes_gateway_and_keeps_settings(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    hass.config_entries.async_update_entry(e, title="EG4 TOU (127.0.0.1)", options={"enabled": True, "dry_run": True})
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()

    other = FakeGateway({160: 20, 161: 80})
    other.port = await other.start()
    try:
        r = await e.start_reconfigure_flow(hass)
        assert r["type"] == "form" and r["step_id"] == "reconfigure"
        new = {"host": "127.0.0.1", "port": other.port, "unit": 1, "country": "US", "subdiv": "MD"}
        bad = await hass.config_entries.flow.async_configure(r["flow_id"], {**new, "port": 1})
        assert bad["errors"] == {"base": "cannot_connect"}
        assert e.data["port"] == gateway.port, "nothing saved after a failed probe"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], new)
        assert r["type"] == "abort" and r["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert e.data["port"] == other.port and e.data["tariff"] == "bge_rl"
        assert e.unique_id == f"127.0.0.1:{other.port}:1"
        assert e.options["dry_run"] is True
        assert e.runtime_data.client._cfg.port == other.port, "reloaded with the new gateway"
        assert other.writes == []
    finally:
        other.server.close()


async def test_reconfigure_refuses_a_gateway_another_entry_uses(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, unique_id="127.0.0.1:1502:1", data={}).add_to_hass(hass)
    r = await e.start_reconfigure_flow(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": 1502, "unit": 1, "country": "US", "subdiv": "MD"})
    assert r["errors"] == {"base": "already_configured"}


async def test_diagnostics_redact_the_gateway(hass: HomeAssistant, gateway):
    from custom_components.eg4_tou.diagnostics import async_get_config_entry_diagnostics

    e = entry_for(gateway)
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    d = await async_get_config_entry_diagnostics(hass, e)
    assert d["config"]["host"] == "**REDACTED**"
    assert "127.0.0.1" not in str(d)
    assert d["mode"] == "off" and d["registers"]["161"]["inverter"] == "90"


async def _setup(hass, gateway, **options):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(e, options=options)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    return e


async def test_unreachable_inverter_makes_entities_unavailable_then_recovers(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    c = e.runtime_data
    c.client._cfg.attempts, c.client._cfg.pause = 1, 0
    real_port = c.client._cfg.port
    c.client._cfg.port = 1                                    # nothing listens there
    await c.async_refresh()
    assert hass.states.get("sensor.eg4_tou_controller_status").state == "unavailable"
    assert hass.states.get("switch.eg4_tou_read_only_mode").state == "unavailable"
    assert c.last_exception.translation_key == "read_failed"   # message comes from strings (exceptions)
    c.client._cfg.port = real_port
    await c.async_refresh()
    assert hass.states.get("sensor.eg4_tou_controller_status").state == "off"


async def test_refused_write_is_reported_and_retried(hass: HomeAssistant, gateway):
    from custom_components.eg4_tou.modbus import ModbusError
    e = await _setup(hass, gateway, enabled=True, dry_run=False)
    c = e.runtime_data
    gateway.regs[66] = 30                    # someone changed the inverter: one register to put back
    with patch.object(c.client, "write", AsyncMock(side_effect=ModbusError("modbus exception fc=6 code=2"))):
        await c.async_refresh()
    status = hass.states.get("sensor.eg4_tou_controller_status")
    assert "failed" in status.attributes["last_write"] and "retry next cycle" in status.attributes["last_write"]


async def test_write_ok_but_reread_fails_keeps_the_write_result(hass: HomeAssistant, gateway):
    from custom_components.eg4_tou.modbus import ModbusError
    e = await _setup(hass, gateway, enabled=True, dry_run=True)
    c = e.runtime_data
    real_read = c.client.read
    calls = {"n": 0}

    async def read_then_fail(start, count=1):
        calls["n"] += 1
        if calls["n"] > 3:          # the 3 initial block reads succeed, the re-read after writing fails
            raise ModbusError("gateway went away")
        return await real_read(start, count)

    hass.config_entries.async_update_entry(e, options={**e.options, "dry_run": False})
    await hass.async_block_till_done()
    calls["n"] = 0
    gateway.regs[66] = 30                    # drift, so this cycle writes
    with patch.object(c.client, "read", read_then_fail):
        await c.async_refresh()
    assert c.data.last_write.endswith("wrote regs [66]") and gateway.regs[66] != 30
    assert c.describe(66, None) == "?"


async def test_controls_store_their_settings(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.eg4_tou_controller_enabled"}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.eg4_tou_read_only_mode"}, blocking=True)
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.eg4_tou_charge_limit_winter_weekday", "value": 65}, blocking=True)
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.eg4_tou_ac_charge_power", "value": 7.5}, blocking=True)
    await hass.services.async_call("time", "set_value",
                                   {"entity_id": "time.eg4_tou_charge_start", "time": "02:30:00"}, blocking=True)
    await hass.async_block_till_done()
    o = e.options
    assert o["enabled"] is True and o["dry_run"] is False
    assert o["end_soc_winter"] == 65 and isinstance(o["end_soc_winter"], int)
    assert o["ac_charge_kw"] == 7.5 and o["charge_start"] == "02:30"
    assert hass.states.get("time.eg4_tou_charge_start").state == "02:30:00"


async def test_diagnostics_before_first_data(hass: HomeAssistant, gateway):
    from custom_components.eg4_tou.diagnostics import async_get_config_entry_diagnostics
    e = await _setup(hass, gateway)
    e.runtime_data.data = None
    d = await async_get_config_entry_diagnostics(hass, e)
    assert "registers" not in d and d["config"]["host"] == "**REDACTED**"


async def test_invalid_summer_date_is_rejected(hass: HomeAssistant, gateway):
    r = await _custom_start(hass, gateway, "seasonal")
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "start_month": "2", "start_day": 30, "end_month": "9", "end_day": 30})
    assert r["step_id"] == "summer_dates" and r["errors"] == {"base": "bad_date"}


async def test_reconfigure_keeps_a_renamed_title(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    hass.config_entries.async_update_entry(e, title="Garage inverter")
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    r = await e.start_reconfigure_flow(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1, "country": "US", "subdiv": ""})
    assert r["reason"] == "reconfigure_successful"
    assert e.title == "Garage inverter" and e.data["subdiv"] == ""


async def test_scan_edge_cases(hass: HomeAssistant, gateway):
    from custom_components.eg4_tou import scanner
    # network integration missing: no candidates, the form falls back to manual entry
    with patch("homeassistant.components.network.async_get_adapters", AsyncMock(side_effect=RuntimeError)):
        assert await scanner.candidate_hosts(hass) == []
    # disabled adapters are skipped, and async_scan scans what is left
    adapters = [{"enabled": False, "ipv4": [{"address": "192.168.5.10", "network_prefix": 24}]}]
    with patch("homeassistant.components.network.async_get_adapters", AsyncMock(return_value=adapters)):
        assert await scanner.async_scan(hass) == []
    # a port that opens but never answers Modbus is reported as "not confirmed as EG4"
    gateway.drop_first = 99
    assert await scanner._identify("127.0.0.1", gateway.port, 1) == scanner.Found("127.0.0.1", False)


# ---- the dashboard the wizard can create --------------------------------------------------

async def _finish_wizard(hass, gateway, create: bool):
    r = await _to_mode(hass, gateway)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"mode": "read_only"})
    assert r["step_id"] == "dashboard"
    return await hass.config_entries.flow.async_configure(r["flow_id"], {"create_dashboard": create})


async def _lovelace(hass):
    from homeassistant.setup import async_setup_component
    assert await async_setup_component(hass, "lovelace", {})
    await hass.async_block_till_done()


async def test_wizard_creates_the_dashboard_and_removal_deletes_it(hass: HomeAssistant, gateway):
    await _lovelace(hass)
    r = await _finish_wizard(hass, gateway, True)
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data["create_dashboard"] is False and entry.data["dashboard_url"] == "eg4-tou"
    from custom_components.eg4_tou import dashboard as dash
    coll = dash._collection(hass)
    item = dash._find_item(coll)
    assert item["title"] == "EG4 TOU" and item["show_in_sidebar"] is True and item["require_admin"] is False
    cfg = await dash._live_dashboards(hass)[dash.URL_PATH].async_load(False)
    assert cfg["title"] == "EG4 TOU" and cfg["views"][0]["path"] == "schedule"
    # a reload does not create it again
    await hass.config_entries.async_reload(entry.entry_id)
    assert len([i for i in coll.async_items() if i["url_path"] == "eg4-tou"]) == 1
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert dash._find_item(coll) is None and dash.URL_PATH not in dash._live_dashboards(hass)


async def test_wizard_without_the_box_ticked_creates_nothing(hass: HomeAssistant, gateway):
    await _lovelace(hass)
    r = await _finish_wizard(hass, gateway, False)
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    from custom_components.eg4_tou import dashboard as dash
    assert dash._find_item(dash._collection(hass)) is None
    assert "dashboard_url" not in hass.config_entries.async_entries(DOMAIN)[0].data


async def test_an_existing_dashboard_is_left_alone_and_survives_removal(hass: HomeAssistant, gateway):
    await _lovelace(hass)
    from custom_components.eg4_tou import dashboard as dash
    coll = dash._collection(hass)
    await coll.async_create_item({"url_path": "eg4-tou", "title": "Mine", "icon": "mdi:home",
                                  "show_in_sidebar": True, "require_admin": False, "allow_single_word": True})
    await dash._live_dashboards(hass)["eg4-tou"].async_save({"views": [{"title": "Mine"}]})
    r = await _finish_wizard(hass, gateway, True)
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert "dashboard_url" not in entry.data
    assert (await dash._live_dashboards(hass)["eg4-tou"].async_load(False))["views"] == [{"title": "Mine"}]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert dash._find_item(coll)["title"] == "Mine", "we only remove a dashboard we created"


async def test_without_lovelace_the_setup_still_works_and_explains(hass: HomeAssistant, gateway):
    with patch("homeassistant.components.persistent_notification.async_create") as note, \
            patch("custom_components.eg4_tou.dashboard._collection", return_value=None):
        r = await _finish_wizard(hass, gateway, True)
        assert r["type"] == "create_entry"
        await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state.value == "loaded" and entry.data["create_dashboard"] is False
    assert note.call_args.kwargs["notification_id"] == "eg4_tou_dashboard"


async def test_dashboard_save_failure_leaves_nothing_behind(hass: HomeAssistant, gateway):
    await _lovelace(hass)
    from custom_components.eg4_tou import dashboard as dash
    with patch("custom_components.eg4_tou.dashboard.load_yaml_dict", side_effect=OSError("boom")):
        assert await dash.async_create_dashboard(hass) == dash.UNAVAILABLE
    assert dash._find_item(dash._collection(hass)) is None

    async def failing_save(self, config):
        raise OSError("disk full")
    from homeassistant.components.lovelace.dashboard import LovelaceStorage
    with patch.object(LovelaceStorage, "async_save", failing_save):
        assert await dash.async_create_dashboard(hass) == dash.UNAVAILABLE
    assert dash._find_item(dash._collection(hass)) is None


def test_the_shipped_dashboard_is_valid_yaml_with_both_cards():
    from custom_components.eg4_tou.dashboard import DASHBOARD_FILE
    from homeassistant.util.yaml import load_yaml_dict
    cfg = load_yaml_dict(str(DASHBOARD_FILE))
    text = DASHBOARD_FILE.read_text()
    assert cfg["title"] == "EG4 TOU" and cfg["views"][0]["path"] == "schedule"
    assert "custom:eg4-tou-rules-card" in text and "custom:eg4-tou-timeline-card" in text


async def test_dashboard_helpers_cope_with_other_home_assistant_shapes(hass: HomeAssistant):
    from custom_components.eg4_tou import dashboard as dash
    # no websocket handler at all, or one without a collection behind it
    hass.data["websocket_api"] = {}
    assert dash._collection(hass) is None
    hass.data["websocket_api"] = {"lovelace/dashboards/create": (lambda *a: None, None)}
    assert dash._collection(hass) is None
    # a handler wrapped by a decorator (as require_admin does) still leads to the collection
    class Owner:
        storage_collection = "coll"
        def ws_create_item(self): ...
    wrapped = lambda *a: None  # noqa: E731
    wrapped.__wrapped__ = Owner().ws_create_item
    hass.data["websocket_api"] = {"lovelace/dashboards/create": (wrapped, None)}
    assert dash._collection(hass) == "coll"
    hass.data["websocket_api"] = {}
    # old Home Assistant keeps the live dashboards in a dict, new ones in a dataclass
    hass.data["lovelace"] = {"dashboards": {"x": 1}}
    assert dash._live_dashboards(hass) == {"x": 1}
    hass.data["lovelace"] = None
    assert dash._live_dashboards(hass) is None
    assert await dash.async_create_dashboard(hass) == dash.UNAVAILABLE
    await dash.async_remove_dashboard(hass)      # nothing to remove, must not raise


async def test_dashboard_removal_failure_is_only_logged(hass: HomeAssistant, caplog):
    await _lovelace(hass)
    from custom_components.eg4_tou import dashboard as dash
    assert await dash.async_create_dashboard(hass) == dash.CREATED
    coll = dash._collection(hass)
    with patch.object(type(coll), "async_delete_item", side_effect=OSError("boom")):
        await dash.async_remove_dashboard(hass)
    assert "Could not remove the EG4 TOU dashboard" in caplog.text
    assert await dash.async_create_dashboard(hass) == dash.EXISTS
    await dash.async_remove_dashboard(hass)
    assert dash._find_item(coll) is None


# ---- update_settings service (the timeline card's Edit > Save) ---------------------------------
from homeassistant.exceptions import ServiceValidationError, Unauthorized  # noqa: E402
from homeassistant.core import Context  # noqa: E402


async def _call(hass, entry=None, **data):
    if entry is not None:
        data.setdefault("entry_id", entry.entry_id)
    await hass.services.async_call(DOMAIN, "update_settings", data, blocking=True)
    await hass.async_block_till_done()


async def test_sensor_publishes_settings_and_entry_id_for_the_card(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    a = hass.states.get("sensor.eg4_tou_active_profile").attributes
    assert a["entry_id"] == e.entry_id
    assert a["settings"] == {"charge_start": "01:00", "charge_end": "05:00", "end_soc_summer": 80,
                             "end_soc_winter": 70, "end_soc_offpeak": 50, "ac_charge_kw": 5.0}


async def test_update_settings_changes_everything_at_once_and_replans(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway, enabled=True, dry_run=False)
    await _call(hass, e, charge_start="02:30", charge_end="06:15:00", end_soc_winter=65, end_soc_summer=75,
                end_soc_offpeak=45, ac_charge_kw=7.5)
    o = e.options
    assert (o["charge_start"], o["charge_end"]) == ("02:30", "06:15")
    assert (o["end_soc_winter"], o["end_soc_summer"], o["end_soc_offpeak"], o["ac_charge_kw"]) == (65, 75, 45, 7.5)
    assert gateway.regs[68] == pack_time("02:30") and gateway.regs[69] == pack_time("06:15")
    assert gateway.regs[66] == 75
    s = hass.states.get("sensor.eg4_tou_active_profile").attributes["settings"]
    assert s["charge_start"] == "02:30" and s["ac_charge_kw"] == 7.5


async def test_update_settings_partial_and_empty_leave_the_rest_alone(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    before = dict(e.options)
    await _call(hass, e)                                   # nothing to change
    assert dict(e.options) == before
    await _call(hass, e, end_soc_winter=55)
    assert dict(e.options) == {**before, "end_soc_winter": 55}


async def test_update_settings_rejects_a_bad_window_and_changes_nothing(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    before = dict(e.options)
    with pytest.raises(ServiceValidationError):
        await _call(hass, e, charge_start="06:00", charge_end="05:00")
    with pytest.raises(ServiceValidationError):            # one end moved past the other stored one
        await _call(hass, e, charge_start="05:30")
    assert dict(e.options) == before


@pytest.mark.parametrize("bad", [{"charge_start": "25:00"}, {"charge_end": "noon"}, {"end_soc_winter": 10},
                                 {"end_soc_summer": 101}, {"ac_charge_kw": 11}])
async def test_update_settings_validates_ranges(hass: HomeAssistant, gateway, bad):
    e = await _setup(hass, gateway)
    with pytest.raises(Exception):
        await _call(hass, e, **bad)


async def test_update_settings_finds_the_only_entry_and_rejects_unknown_ones(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    await _call(hass, end_soc_offpeak=40)                  # no entry_id: the single entry
    assert e.options["end_soc_offpeak"] == 40
    with pytest.raises(ServiceValidationError):
        await _call(hass, entry_id="nope", end_soc_offpeak=41)


async def test_update_settings_is_admin_only(hass: HomeAssistant, gateway, hass_read_only_user):
    e = await _setup(hass, gateway)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "update_settings", {"entry_id": e.entry_id, "end_soc_winter": 60},
                                       blocking=True, context=Context(user_id=hass_read_only_user.id))
    assert e.options["end_soc_winter"] == 70


# ---- editable on-peak windows (peak overrides) -------------------------------------------------
from datetime import date as _date  # noqa: E402
from custom_components.eg4_tou.planner import BGE_RL, peak_edit_info, tariff_with_overrides  # noqa: E402
from custom_components.eg4_tou.services import merge_peaks  # noqa: E402

WINTER = [["07:00", "11:00"], ["17:00", "21:00"]]


def test_tariff_overrides_replace_only_the_named_season_and_ignore_bad_ones():
    t = tariff_with_overrides(BGE_RL, {"winter": [["18:00", "22:00"]], "summer": [["20:00", "10:00"]], "spring": [["01:00", "02:00"]]})
    assert t.peaks["winter"] == (("18:00", "22:00"),)
    assert t.peaks["summer"] == BGE_RL.peaks["summer"]      # invalid window ignored
    assert "spring" not in t.peaks                           # unknown season ignored
    assert t.holiday_fn is BGE_RL.holiday_fn and t.shift_fn is BGE_RL.shift_fn
    assert tariff_with_overrides(BGE_RL, {}) .peaks == BGE_RL.peaks


def test_peak_edit_info_reports_season_default_override_and_shift():
    t = tariff_with_overrides(BGE_RL, {"winter": [["18:00", "22:00"]]})
    info = peak_edit_info(_date(2026, 11, 20), t, BGE_RL, {"winter": [["18:00", "22:00"]]})
    assert info == {"season": "winter", "peaks": [["18:00", "22:00"]], "default": WINTER, "overridden": True,
                    "shift_hours": 0, "max_windows": 2}
    assert peak_edit_info(_date(2026, 3, 16), BGE_RL, BGE_RL, {})["shift_hours"] == 1     # shift week
    assert peak_edit_info(_date(2026, 7, 15), BGE_RL, BGE_RL, {})["overridden"] is False


def test_merge_peaks_rules():
    seasons = {"summer": BGE_RL.peaks["summer"], "winter": BGE_RL.peaks["winter"]}
    assert merge_peaks({}, {"winter": [["17:00", "21:00"], ["06:00", "10:00"]]}, seasons) == {"winter": [["06:00", "10:00"], ["17:00", "21:00"]]}
    assert merge_peaks({"winter": [["06:00", "10:00"]]}, {"winter": None}, seasons) == {}
    assert merge_peaks({"summer": [["09:00", "19:00"]]}, {"winter": WINTER}, seasons) == {"summer": [["09:00", "19:00"]]}   # same as tariff: no override
    assert merge_peaks({}, {"winter": []}, seasons) == {"winter": []}                                                   # no on-peak at all
    for bad, key in (([["10:00", "10:00"]], "peak_order"), ([["06:00", "10:00"], ["09:00", "12:00"]], "peak_overlap"),
                     ([["01:00", "02:00"], ["03:00", "04:00"], ["05:00", "06:00"]], "too_many_peaks")):
        with pytest.raises(ServiceValidationError) as e:
            merge_peaks({}, {"winter": bad}, seasons)
        assert e.value.translation_key == key
    with pytest.raises(ServiceValidationError):
        merge_peaks({}, {"spring": WINTER}, seasons)


async def test_card_can_edit_peaks_and_the_plan_follows(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway, enabled=True, dry_run=False)
    a = hass.states.get("sensor.eg4_tou_active_profile").attributes
    season = a["peak_edit"]["season"]
    assert a["peak_edit"]["peaks"] == a["peak_edit"]["default"] and not a["peak_edit"]["overridden"]
    await _call(hass, e, peaks={season: [["06:30", "09:30"]]})
    a = hass.states.get("sensor.eg4_tou_active_profile").attributes
    assert a["peak_edit"]["overridden"] and a["peak_edit"]["peaks"] == [["06:30", "09:30"]]
    if a["timeline"]["peaks"]:                                   # a weekday: hold windows are the complement
        assert a["timeline"]["peaks"] == [["06:30", "09:30"]]
        assert gateway.regs.get(152, 0) == pack_time("00:00") and gateway.regs[153] == pack_time("06:30")
    await _call(hass, e, peaks={season: None})                   # back to the tariff
    a = hass.states.get("sensor.eg4_tou_active_profile").attributes
    assert not a["peak_edit"]["overridden"] and a["peak_edit"]["peaks"] == a["peak_edit"]["default"]
    with pytest.raises(ServiceValidationError):
        await _call(hass, e, peaks={season: [["10:00", "09:00"]]})


async def test_configure_clears_peak_overrides(hass: HomeAssistant, gateway):
    e = await _setup(hass, gateway)
    await _call(hass, e, peaks={"winter": [["06:30", "09:30"]]})
    assert e.options["peak_overrides"]
    r = await hass.config_entries.options.async_init(e.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"tariff": "bge_rl", "offpeak_weekends": True, "offpeak_holidays": True})
    assert r["type"] == "create_entry" and e.options["peak_overrides"] == {}
