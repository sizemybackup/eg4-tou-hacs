"""Button: skip the coming storm (automatic mode will not start storm prep for it)."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TouConfigEntry, TouCoordinator
from .entity import TouEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(hass: HomeAssistant, entry: TouConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([SkipStormButton(entry.runtime_data)])


class SkipStormButton(TouEntity, ButtonEntity):
    _domain = "button"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, c: TouCoordinator) -> None:
        super().__init__(c, "storm_skip", "Skip this storm")

    async def async_press(self) -> None:
        await self.coordinator.storm.async_skip()
