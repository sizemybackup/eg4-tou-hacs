"""Full HA flow against the fake gateway: config flow, dry run, then live writes."""
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eg4_tou.const import DOMAIN
from custom_components.eg4_tou.modbus import pack_time
from tests.test_modbus import FakeGateway

import pytest


@pytest.fixture
async def gateway():
    g = FakeGateway({66: 100, 68: pack_time("02:00"), 69: pack_time("06:00"), 160: 30, 161: 90}, noise=True)
    g.port = await g.start()
    yield g
    g.server.close()


def entry_for(gw):
    return MockConfigEntry(domain=DOMAIN, unique_id="t", data={
        "host": "127.0.0.1", "port": gw.port, "unit": 1, "tariff": "bge_rl", "country": "US", "subdiv": "MD"})


async def test_config_flow_creates_entry(hass: HomeAssistant, gateway):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert r["type"] == "form"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1,
        "tariff": "bge_rl", "country": "US", "subdiv": "MD"})
    assert r["step_id"] == "mode"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"mode": "read_only"})
    assert r["type"] == "create_entry"
    assert r["options"] == {"enabled": True, "dry_run": True}


async def _to_mode(hass, gateway):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    return await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port, "unit": 1,
        "tariff": "bge_rl", "country": "US", "subdiv": "MD"})


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
    assert hass.states.get("sensor.eg4_tou_manager_controller_status").state == "read_only"


async def test_config_flow_unreachable(hass: HomeAssistant):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "host": "127.0.0.1", "port": 1, "unit": 1, "tariff": "bge_rl", "country": "US", "subdiv": ""})
    assert r["type"] == "form" and r["errors"] == {"base": "cannot_connect"}


async def test_default_is_off_and_never_writes(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert gateway.writes == []
    assert hass.states.get("sensor.eg4_tou_manager_controller_status").state == "off"
    assert int(hass.states.get("sensor.eg4_tou_manager_registers_out_of_sync").state) > 0


async def test_dry_run_does_not_write(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    hass.config_entries.async_update_entry(e, options={"enabled": True, "dry_run": True})
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert gateway.writes == []
    assert hass.states.get("sensor.eg4_tou_manager_controller_status").state == "read_only"


async def test_live_mode_converges_then_stays_quiet(hass: HomeAssistant, gateway):
    e = entry_for(gateway)
    e.add_to_hass(hass)
    hass.config_entries.async_update_entry(e, options={"enabled": True, "dry_run": False})
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert gateway.writes, "live mode should have written differences"
    assert hass.states.get("sensor.eg4_tou_manager_registers_out_of_sync").state == "0"
    n = len(gateway.writes)
    coordinator = hass.data[DOMAIN][e.entry_id]
    await coordinator.async_refresh()
    assert len(gateway.writes) == n, "second cycle must be idempotent"
    # end SOC lowered 90 -> target: start SOC (160) must be written before end SOC (161)
    regs = [r for r, _ in gateway.writes]
    assert regs.index(160) < regs.index(161)
