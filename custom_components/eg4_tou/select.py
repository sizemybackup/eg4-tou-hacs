"""Select: which tariff from the library is in use (also changeable from the timeline card)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import tariffs
from .const import OPT_TARIFFS
from .coordinator import TouConfigEntry, TouCoordinator
from .entity import TouEntity

PARALLEL_UPDATES = 1  # a change re-plans and may write to the inverter: one at a time


async def async_setup_entry(hass: HomeAssistant, entry: TouConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([TariffSelect(entry.runtime_data)])


class TariffSelect(TouEntity, SelectEntity):
    _domain = "select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, c: TouCoordinator) -> None:
        super().__init__(c, "tariff", "Tariff")

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.entry.options.get(OPT_TARIFFS) or {})

    @property
    def current_option(self) -> str:
        return self.coordinator.tariff_name

    async def async_select_option(self, option: str) -> None:
        c = self.coordinator
        c.hass.config_entries.async_update_entry(c.entry, options=tariffs.activate(dict(c.entry.options), option))
