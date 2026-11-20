"""Switches: controller on/off and read-only mode (both stored in the entry options)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import OPT_DRY_RUN, OPT_ENABLED
from .coordinator import TouConfigEntry, TouCoordinator
from .entity import TouEntity

PARALLEL_UPDATES = 1  # a change re-plans and may write to the inverter: one at a time


async def async_setup_entry(hass: HomeAssistant, entry: TouConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([OptionSwitch(c, OPT_ENABLED, "Controller enabled"),
                        OptionSwitch(c, OPT_DRY_RUN, "Read-only mode")])


class OptionSwitch(TouEntity, SwitchEntity):
    # Config category: Home Assistant does not auto-expose these to Assist/Alexa/Google, so nobody can
    # say "turn off read-only mode" and start inverter writes by accident.
    _attr_entity_category = EntityCategory.CONFIG
    _domain = "switch"

    def __init__(self, c: TouCoordinator, key: str, name: str) -> None:
        super().__init__(c, key, name)
        self._key = key

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.entry.options[self._key])

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set_option(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set_option(self._key, False)
