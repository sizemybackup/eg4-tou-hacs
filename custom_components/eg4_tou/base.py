from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN
from .coordinator import TouCoordinator


class TouEntity(CoordinatorEntity[TouCoordinator]):
    _attr_has_entity_name = True
    _domain: str = ""   # set by each platform class

    def __init__(self, coordinator: TouCoordinator, key: str, object_name: str) -> None:
        """key: option key, also the translation key for the entity's name (translations/en.json).
        object_name: the English name the entity id is built from."""
        super().__init__(coordinator)
        # Deliberate exception to HA's usual naming: predictable ids (e.g. sensor.eg4_tou_active_profile)
        # so the shared dashboard YAML works on every install, whatever the device or area is called.
        # If the id is already taken (a second entry), the entity registry adds a _2 suffix.
        self.entity_id = f"{self._domain}.eg4_tou_{slugify(object_name)}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)}, name="EG4 TOU Manager",
            manufacturer="Community", model="EG4 12000XP local TOU",
        )

    def _set_option(self, key: str, value) -> None:
        c = self.coordinator
        c.hass.config_entries.async_update_entry(c.entry, options={**c.entry.options, key: value})
