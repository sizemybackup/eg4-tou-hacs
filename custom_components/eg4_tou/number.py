"""Numbers: charge limits per profile and AC charge power (stored in the entry options)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import AC_KW_RANGE, AC_KW_STEP, OPT_AC_KW, SOC_RANGE, OPT_END_SOC_OFFPEAK, OPT_END_SOC_SUMMER, OPT_END_SOC_WINTER
from .coordinator import TouConfigEntry, TouCoordinator
from .entity import TouEntity

PARALLEL_UPDATES = 1  # a change re-plans and may write to the inverter: one at a time


async def async_setup_entry(hass: HomeAssistant, entry: TouConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([
        OptionNumber(c, OPT_END_SOC_SUMMER, "Charge limit, summer weekday", *SOC_RANGE, 1, PERCENTAGE),
        OptionNumber(c, OPT_END_SOC_WINTER, "Charge limit, winter weekday", *SOC_RANGE, 1, PERCENTAGE),
        OptionNumber(c, OPT_END_SOC_OFFPEAK, "Charge limit, weekend / holiday", *SOC_RANGE, 1, PERCENTAGE),
        OptionNumber(c, OPT_AC_KW, "AC charge power", *AC_KW_RANGE, AC_KW_STEP, UnitOfPower.KILO_WATT),
    ])


class OptionNumber(TouEntity, NumberEntity):
    _domain = "number"
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, c: TouCoordinator, key: str, name: str,
                 lo: float, hi: float, step: float, unit: str) -> None:
        super().__init__(c, key, name)
        self._key = key
        self._attr_native_min_value, self._attr_native_max_value = lo, hi
        self._attr_native_step, self._attr_native_unit_of_measurement = step, unit

    @property
    def native_value(self) -> float:
        return float(self.coordinator.entry.options[self._key])

    async def async_set_native_value(self, value: float) -> None:
        self._set_option(self._key, int(value) if self._attr_native_step == 1 else float(value))
