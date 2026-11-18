from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN
from .coordinator import TouCoordinator


class TouEntity(CoordinatorEntity[TouCoordinator]):
    _attr_has_entity_name = True
    _domain: str = ""   # set by each platform class

    def __init__(self, coordinator: TouCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        # Predictable ids (e.g. sensor.eg4_tou_active_profile) so a shared dashboard works
        # on every install, instead of area/device-name dependent ones.
        self.entity_id = f"{self._domain}.eg4_tou_{slugify(name)}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)}, name="EG4 TOU Manager",
            manufacturer="Community", model="EG4 12000XP local TOU",
        )

    def _set_option(self, key: str, value) -> None:
        c = self.coordinator
        c.hass.config_entries.async_update_entry(c.entry, options={**c.entry.options, key: value})
