"""Sensors: today's profile (with the dashboard cards' data), out-of-sync count, controller status."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import storm
from .const import DOMAIN, EDITABLE, OPT_STORM_AUTO, OPT_STORM_PREP, OPT_STORM_TARGET, limits
from .coordinator import TouConfigEntry, TouCoordinator
from .entity import TouEntity
from .modbus import WRITE_LIMITS
from .planner import plan_intent, rules_table, timeline

PARALLEL_UPDATES = 0  # read-only; the coordinator does all inverter I/O


async def async_setup_entry(hass: HomeAssistant, entry: TouConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([ProfileSensor(c), PendingSensor(c), StatusSensor(c), StormSensor(c)])


class ProfileSensor(TouEntity, SensorEntity):
    _domain = "sensor"
    _attr_device_class = SensorDeviceClass.ENUM
    # These feed the dashboard cards from the live state only. Recording them would store several KB
    # in the history database on every update, and the recorder drops attributes over 16 KB anyway.
    # peak_windows stays recorded: it is short and useful in history.
    _unrecorded_attributes = frozenset(
        {"rules", "timeline", "week", "facts", "settings", "entry_id", "peak_edit", "plan", "managed",
         *(f"reg_{r}" for r in WRITE_LIMITS)})

    def __init__(self, c: TouCoordinator) -> None:
        super().__init__(c, "profile", "Active profile")
        self._attr_options = self._profiles()

    def _profiles(self) -> list[str]:
        """Every profile name a day can have: the season names of the active tariff, plus the two fixed ones."""
        return sorted({"summer", "winter", "offpeak", *(s.name for s in self.coordinator.tariff.seasons)})

    def _handle_coordinator_update(self) -> None:
        self._attr_options = self._profiles()   # before the state is written: a tariff switch can add seasons
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> str:
        return self.coordinator.data.plan.profile

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        return {"peak_windows": d.peaks,
                "rules": rules_table(d.plan, d.current, d.peaks, d.managed),
                "plan": plan_intent(d.plan),
                "managed": sorted(d.managed),
                "timeline": timeline(d.plan, d.current, d.peak_list, d.managed),
                "week": d.week,
                "facts": d.facts,
                "peak_edit": d.peak_edit,
                "entry_id": self.coordinator.entry.entry_id,
                "settings": {k: self.coordinator.entry.options[k] for k in EDITABLE},
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


class StormSensor(TouEntity, SensorEntity):
    """The storm recommendation, with what the dashboard shows about it."""

    _domain = "sensor"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = storm.LEVELS
    _unrecorded_attributes = frozenset({"alerts"})

    def __init__(self, c: TouCoordinator) -> None:
        super().__init__(c, "storm", "Storm recommendation")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.storm.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return True   # weather and storm state do not depend on reaching the inverter

    @property
    def native_value(self) -> str:
        return self.coordinator.storm.data.recommendation.level

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rec = self.coordinator.storm.data.recommendation
        session = self.coordinator.storm.session()
        options = self.coordinator.entry.options
        now = dt_util.now()

        def iso(ts: float) -> str | None:
            return dt_util.as_local(dt_util.utc_from_timestamp(ts)).isoformat() if ts else None

        registry = er.async_get(self.hass)
        entry_id = self.coordinator.entry.entry_id
        controls = {name: registry.async_get_entity_id(platform, DOMAIN, f"{entry_id}_{key}")
                    for name, platform, key in (("prep", "switch", OPT_STORM_PREP), ("automatic", "switch", OPT_STORM_AUTO),
                                                ("skip", "button", "storm_skip"), ("target", "number", OPT_STORM_TARGET))}
        soc_range, _ = limits(options)

        return {"controls": controls, "target_range": list(soc_range), "headline": rec.headline, "source": rec.source,
                "onset": rec.onset.isoformat() if rec.onset else None,
                "ends": rec.ends.isoformat() if rec.ends else None,
                "alerts": list(rec.alerts),
                "prep": session.prep, "automatic": bool(options[OPT_STORM_AUTO]),
                "target_soc": int(options[OPT_STORM_TARGET]),
                "until": iso(session.until),
                "skipped": not session.prep and rec.level == storm.PREP
                and not storm.is_new(rec, session.handled, now)}
