"""EG4 Local TOU Manager: local Modbus time-of-use scheduling for the EG4 12000XP."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

# Imported at module level on purpose: Home Assistant's loader imports this module in a worker
# thread, so the `holidays` library's file reads at import time do not block the event loop.
from homeassistant.helpers import config_validation as cv

from .coordinator import TouCoordinator
from .const import DEFAULT_OPTIONS, DOMAIN, PLATFORMS

# Set up from the UI only; any `eg4_tou:` key in configuration.yaml is rejected with a clear message.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

FRONTEND_DIR = Path(__file__).parent / "frontend"
CARD_FILES = ("eg4-tou-timeline-card.js", "eg4-tou-rules-card.js")

if TYPE_CHECKING:  # keep HA imports out of module scope so the pure logic is testable
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup(hass: "HomeAssistant", config: dict) -> bool:
    """Serve the dashboard cards and load them on every dashboard page (once per HA run)."""
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.loader import async_get_integration

    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(f"/eg4_tou/{name}", str(FRONTEND_DIR / name), cache_headers=False) for name in CARD_FILES])
    for name in CARD_FILES:
        add_extra_js_url(hass, f"/eg4_tou/{name}?v={version}")
    return True


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    missing = {k: v for k, v in DEFAULT_OPTIONS.items() if k not in entry.options}
    if missing:
        hass.config_entries.async_update_entry(entry, options={**entry.options, **missing})
    coordinator = TouCoordinator(hass, entry)
    await coordinator.async_load_holidays()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    await hass.data[DOMAIN][entry.entry_id].async_request_refresh()


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
