"""Switches: controller on/off and read-only mode (both stored in the entry options)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import OPT_DRY_RUN, OPT_ENABLED, OPT_STORM_AUTO, OPT_STORM_PREP, OPT_WRITE_GROUPS, write_groups
from .drivers import GROUPS
from .coordinator import TouConfigEntry, TouCoordinator
from .entity import TouEntity

PARALLEL_UPDATES = 1  # a change re-plans and may write to the inverter: one at a time


async def async_setup_entry(hass: HomeAssistant, entry: TouConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([OptionSwitch(c, OPT_ENABLED, "Controller enabled"),
                        OptionSwitch(c, OPT_DRY_RUN, "Read-only mode"),
                        *(GroupSwitch(c, g, GROUP_NAMES[g]) for g in GROUPS),
                        StormSwitch(c, OPT_STORM_PREP, "Storm prep"),
                        OptionSwitch(c, OPT_STORM_AUTO, "Storm prep automatic")])


GROUP_NAMES = {"charge_window": "Manage charge window", "hold_windows": "Manage hold windows",
               "soc": "Manage charge limits", "power": "Manage charge power"}


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


class StormSwitch(OptionSwitch):
    """Storm prep now. Goes through the storm manager, which also sets when it ends."""

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.storm.async_turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.storm.async_turn_off()


class GroupSwitch(OptionSwitch):
    """Whether the integration may write one kind of setting. Off means "leave it to me": the plan is
    still shown, but nothing of that kind is compared or written."""

    def __init__(self, c: TouCoordinator, group: str, name: str) -> None:
        super().__init__(c, f"write_{group}", name)
        self._group = group

    @property
    def is_on(self) -> bool:
        return bool(write_groups(self.coordinator.entry.options).get(self._group, True))

    @property
    def available(self) -> bool:
        return super().available and bool(set(GROUPS[self._group]) & self.coordinator.driver.slots)

    def _set(self, on: bool) -> None:
        self._set_option(OPT_WRITE_GROUPS, {**write_groups(self.coordinator.entry.options), self._group: on})

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)
