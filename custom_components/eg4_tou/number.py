from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode

from .base import TouEntity
from .const import (DOMAIN, OPT_AC_KW, OPT_END_SOC_OFFPEAK, OPT_END_SOC_SUMMER, OPT_END_SOC_WINTER)


async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        OptionNumber(c, OPT_END_SOC_SUMMER, "Charge limit, summer weekday", 20, 100, 1, "%"),
        OptionNumber(c, OPT_END_SOC_WINTER, "Charge limit, winter weekday", 20, 100, 1, "%"),
        OptionNumber(c, OPT_END_SOC_OFFPEAK, "Charge limit, weekend / holiday", 20, 100, 1, "%"),
        OptionNumber(c, OPT_AC_KW, "AC charge power", 1.0, 10.0, 0.5, "kW"),
    ])


class OptionNumber(TouEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, c, key, name, lo, hi, step, unit):
        super().__init__(c, key, name)
        self._key = key
        self._attr_native_min_value, self._attr_native_max_value = lo, hi
        self._attr_native_step, self._attr_native_unit_of_measurement = step, unit

    @property
    def native_value(self):
        return self.coordinator.entry.options[self._key]

    async def async_set_native_value(self, value: float) -> None:
        self._set_option(self._key, int(value) if self._attr_native_step == 1 else float(value))
