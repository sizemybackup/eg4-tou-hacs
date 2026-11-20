"""Sensors: today's profile (with the dashboard cards' data), out-of-sync count, controller status."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TouConfigEntry, TouCoordinator
from .entity import TouEntity
from .modbus import WRITE_LIMITS
from .planner import rules_table, timeline

PARALLEL_UPDATES = 0  # read-only; the coordinator does all inverter I/O


async def async_setup_entry(hass: HomeAssistant, entry: TouConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([ProfileSensor(c), PendingSensor(c), StatusSensor(c)])


class ProfileSensor(TouEntity, SensorEntity):
    _domain = "sensor"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["summer", "winter", "offpeak"]
    # These feed the dashboard cards from the live state only. Recording them would store several KB
    # in the history database on every update, and the recorder drops attributes over 16 KB anyway.
    # peak_windows stays recorded: it is short and useful in history.
    _unrecorded_attributes = frozenset(
        {"rules", "timeline", "week", "facts", *(f"reg_{r}" for r in WRITE_LIMITS)})

    def __init__(self, c: TouCoordinator) -> None:
        super().__init__(c, "profile", "Active profile")

    @property
    def native_value(self) -> str:
        return self.coordinator.data.plan.profile

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        return {"peak_windows": d.peaks,
                "rules": rules_table(d.plan, d.current, d.peaks),
                "timeline": timeline(d.plan, d.current, d.peak_list),
                "week": d.week,
                "facts": d.facts,
                **{f"reg_{r}": self.coordinator.describe(r, v) for r, v in d.plan.registers.items()}}


class PendingSensor(TouEntity, SensorEntity):
    _domain = "sensor"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c: TouCoordinator) -> None:
        super().__init__(c, "pending", "Registers out of sync")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.pending)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d, c = self.coordinator.data, self.coordinator
        return {"changes": [f"reg {r}: {c.describe(r, was)} -> {c.describe(r, want)}" for r, want, was in d.pending]}


class StatusSensor(TouEntity, SensorEntity):
    _domain = "sensor"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["off", "read_only", "live"]

    def __init__(self, c: TouCoordinator) -> None:
        super().__init__(c, "status", "Controller status")

    @property
    def native_value(self) -> str:
        return self.coordinator.data.mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_write": self.coordinator.data.last_write}
