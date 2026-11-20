"""EG4 Local TOU Manager: local Modbus time-of-use scheduling for the EG4 12000XP."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

# Imported at module level on purpose: Home Assistant's loader imports this module in a worker
# thread, so the `holidays` library's file reads at import time do not block the event loop.
from homeassistant.helpers import config_validation as cv

from .coordinator import TouCoordinator
from .const import CONF_CREATE_DASHBOARD, CONF_DASHBOARD_URL, DEFAULT_OPTIONS, DOMAIN, PLATFORMS
from . import services
from .dashboard import CREATED, UNAVAILABLE, URL_PATH, async_create_dashboard, async_remove_dashboard

# Set up from the UI only; any `eg4_tou:` key in configuration.yaml is rejected with a clear message.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

FRONTEND_DIR = Path(__file__).parent / "frontend"
CARD_FILES = ("eg4-tou-timeline-card.js", "eg4-tou-rules-card.js")

if TYPE_CHECKING:  # keep HA imports out of module scope so the pure logic is testable
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType


async def async_setup(hass: "HomeAssistant", config: "ConfigType") -> bool:
    """Serve the dashboard cards and load them on every dashboard page (once per HA run)."""
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.loader import async_get_integration

    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(f"/eg4_tou/{name}", str(FRONTEND_DIR / name), cache_headers=False) for name in CARD_FILES])
    for name in CARD_FILES:
        add_extra_js_url(hass, f"/eg4_tou/{name}?v={version}")
    services.async_register(hass)
    return True


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    missing = {k: v for k, v in DEFAULT_OPTIONS.items() if k not in entry.options}
    if missing:
        hass.config_entries.async_update_entry(entry, options={**entry.options, **missing})
    coordinator = TouCoordinator(hass, entry)
    await coordinator.async_load_holidays()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _maybe_create_dashboard(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    await entry.runtime_data.async_refresh()   # now, not debounced: a second quick save must show up too


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _maybe_create_dashboard(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    """Create the dashboard the wizard offered, once. Failing to do so never fails the setup."""
    if not entry.data.get(CONF_CREATE_DASHBOARD):
        return
    result = await async_create_dashboard(hass)
    data = {**entry.data, CONF_CREATE_DASHBOARD: False}
    if result == CREATED:
        data[CONF_DASHBOARD_URL] = URL_PATH
    elif result == UNAVAILABLE:
        from homeassistant.components import persistent_notification
        persistent_notification.async_create(
            hass, "The EG4 TOU dashboard could not be created automatically. Add it by hand: copy "
            "`custom_components/eg4_tou/dashboard.yaml` from the integration's GitHub page into a new "
            "dashboard's raw configuration editor. See the README for the steps.",
            title="EG4 Local TOU Manager", notification_id="eg4_tou_dashboard")
    hass.config_entries.async_update_entry(entry, data=data)


async def async_remove_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    """Remove the dashboard we created when the last entry goes."""
    others = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]
    if entry.data.get(CONF_DASHBOARD_URL) and not others:
        await async_remove_dashboard(hass)
