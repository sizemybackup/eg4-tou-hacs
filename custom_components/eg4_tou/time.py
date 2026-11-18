from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity

from .base import TouEntity
from .const import DOMAIN, OPT_CHARGE_END, OPT_CHARGE_START


async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OptionTime(c, OPT_CHARGE_START, "Overnight charge start"),
                        OptionTime(c, OPT_CHARGE_END, "Overnight charge end")])


class OptionTime(TouEntity, TimeEntity):
    def __init__(self, c, key, name):
        super().__init__(c, key, name)
        self._key = key

    @property
    def native_value(self):
        h, m = (int(x) for x in self.coordinator.entry.options[self._key].split(":"))
        return time(h, m)

    async def async_set_value(self, value: time) -> None:
        self._set_option(self._key, f"{value.hour:02d}:{value.minute:02d}")
