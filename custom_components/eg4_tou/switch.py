from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .base import TouEntity
from .const import DOMAIN, OPT_DRY_RUN, OPT_ENABLED


async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OptionSwitch(c, OPT_ENABLED, "Controller enabled", "mdi:power"),
                        OptionSwitch(c, OPT_DRY_RUN, "Read-only mode", "mdi:test-tube")])


class OptionSwitch(TouEntity, SwitchEntity):
    # Config category: Home Assistant does not auto-expose these to Assist/Alexa/Google, so nobody can
    # say "turn off read-only mode" and start inverter writes by accident.
    _attr_entity_category = EntityCategory.CONFIG
    _domain = "switch"
    def __init__(self, c, key, name, icon):
        super().__init__(c, key, name)
        self._key, self._attr_icon = key, icon

    @property
    def is_on(self):
        return bool(self.coordinator.entry.options[self._key])

    async def async_turn_on(self, **kw):
        self._set_option(self._key, True)

    async def async_turn_off(self, **kw):
        self._set_option(self._key, False)
