"""Times: the overnight AC charge window (stored in the entry options)."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import OPT_CHARGE_END, OPT_CHARGE_START
from .coordinator import TouConfigEntry, TouCoordinator
from .entity import TouEntity

PARALLEL_UPDATES = 1  # a change re-plans and may write to the inverter: one at a time


async def async_setup_entry(hass: HomeAssistant, entry: TouConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([OptionTime(c, OPT_CHARGE_START, "Charge start"),
                        OptionTime(c, OPT_CHARGE_END, "Charge end")])


class OptionTime(TouEntity, TimeEntity):
    _domain = "time"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, c: TouCoordinator, key: str, name: str) -> None:
        super().__init__(c, key, name)
        self._key = key

    @property
    def native_value(self) -> time:
        h, m = (int(x) for x in self.coordinator.entry.options[self._key].split(":"))
        return time(h, m)

    async def async_set_value(self, value: time) -> None:
        self._set_option(self._key, f"{value.hour:02d}:{value.minute:02d}")
