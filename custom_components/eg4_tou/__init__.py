"""EG4 Local TOU Manager: local Modbus time-of-use scheduling for the EG4 12000XP."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .const import DEFAULT_OPTIONS, DOMAIN, PLATFORMS

if TYPE_CHECKING:  # keep HA imports out of module scope so the pure logic is testable
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from .coordinator import TouCoordinator

    missing = {k: v for k, v in DEFAULT_OPTIONS.items() if k not in entry.options}
    if missing:
        hass.config_entries.async_update_entry(entry, options={**entry.options, **missing})
    coordinator = TouCoordinator(hass, entry)
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
