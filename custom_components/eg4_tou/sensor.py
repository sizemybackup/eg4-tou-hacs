from __future__ import annotations

from homeassistant.components.sensor import SensorEntity

from .base import TouEntity
from .planner import rules_table, timeline
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ProfileSensor(c), PendingSensor(c), StatusSensor(c)])


class ProfileSensor(TouEntity, SensorEntity):
    _domain = "sensor"
    def __init__(self, c):
        super().__init__(c, "profile", "Active profile")

    @property
    def native_value(self):
        return self.coordinator.data.plan.profile

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data
        return {"peak_windows": d.peaks,
                "rules": rules_table(d.plan, d.current, d.peaks),
                "timeline": timeline(d.plan, d.current, d.peak_list),
                "week": d.week,
                "facts": d.facts,
                **{f"reg_{r}": self.coordinator.describe(r, v) for r, v in d.plan.registers.items()}}


class PendingSensor(TouEntity, SensorEntity):
    _domain = "sensor"
    _attr_icon = "mdi:file-compare"

    def __init__(self, c):
        super().__init__(c, "pending", "Registers out of sync")

    @property
    def native_value(self):
        return len(self.coordinator.data.pending)

    @property
    def extra_state_attributes(self):
        d, c = self.coordinator.data, self.coordinator
        return {"changes": [f"reg {r}: {c.describe(r, was)} -> {c.describe(r, want)}" for r, want, was in d.pending]}


class StatusSensor(TouEntity, SensorEntity):
    _domain = "sensor"
    _attr_icon = "mdi:information-outline"

    def __init__(self, c):
        super().__init__(c, "status", "Controller status")

    @property
    def native_value(self):
        return self.coordinator.data.mode

    @property
    def extra_state_attributes(self):
        return {"last_write": self.coordinator.data.last_write}
