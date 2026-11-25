"""Every 10 minutes: read the inverter, compute today's plan, diff, and (only
when enabled and not in dry-run) write the differences. Idempotent."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

import holidays
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BACKEND_ENTITIES, CONF_BACKEND, CONF_COUNTRY, CONF_ENTITIES, CONF_HOST, CONF_PORT, CONF_SUBDIV, CONF_UNIT, DEFAULT_OPTIONS,
    DOMAIN, OPT_AC_KW, OPT_CHARGE_END, OPT_CHARGE_START, OPT_DRY_RUN, OPT_ENABLED,
    OPT_END_SOC_OFFPEAK, OPT_END_SOC_SUMMER, OPT_END_SOC_WINTER, OPT_HOLD_CUSTOM, OPT_POLL_MINUTES,
    OPT_SOC_GAP, OPT_STORM_KW, OPT_STORM_PREP, OPT_STORM_START_MAX, OPT_STORM_TARGET, OPT_STORM_UNTIL, OPT_TARIFFS,
    limits, write_groups,
)
from .drivers import GROUPS, Driver, DriverError, EntityDriver, ModbusDriver
from .modbus import Eg4Modbus, ModbusConfig, unpack_time
from .planner import Plan, Settings, Tariff, build_plan, peak_edit_info, validate_hold, week_schedule
from .storm_manager import StormManager
from .tariffs import active_of, base_of, effective_of

_LOGGER = logging.getLogger(__name__)

type TouConfigEntry = ConfigEntry[TouCoordinator]


@dataclass
class TouData:
    current: dict[int, int]
    plan: Plan
    pending: list[tuple[int, int, int | None]]
    mode: str                      # "off" | "read_only" | "live"
    last_write: str = "never"
    peaks: str = ""
    peak_list: list[list[str]] = field(default_factory=list)
    week: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    peak_edit: dict[str, Any] = field(default_factory=dict)
    managed: frozenset[int] = frozenset()   # the slots this integration may write (backend x write groups)


def settings_from_options(o: Mapping[str, Any]) -> Settings:
    (soc_lo, soc_hi), (kw_lo, kw_hi) = limits(o)
    return Settings(
        storm=bool(o.get(OPT_STORM_PREP, False)),
        storm_end_soc=min(max(int(o.get(OPT_STORM_TARGET, 100)), soc_lo), soc_hi),
        storm_kw=min(max(float(o.get(OPT_STORM_KW, 10.0)), kw_lo), kw_hi),
        storm_start_max=int(o.get(OPT_STORM_START_MAX, 90)),
        charge_window=(o[OPT_CHARGE_START], o[OPT_CHARGE_END]),
        end_soc={"summer": int(o[OPT_END_SOC_SUMMER]), "winter": int(o[OPT_END_SOC_WINTER]),
                 "offpeak": int(o[OPT_END_SOC_OFFPEAK])},
        ac_charge_kw=float(o[OPT_AC_KW]),
        start_soc_gap=int(o.get(OPT_SOC_GAP, DEFAULT_OPTIONS[OPT_SOC_GAP])),
        hold_custom={p: tuple((str(a), str(b)) for a, b in w) for p, w in (o.get(OPT_HOLD_CUSTOM) or {}).items()
                     if validate_hold(w) is None},
    )


def managed_slots(options: Mapping[str, Any], driver: Driver) -> frozenset[int]:
    """Slots the driver can reach whose group the user has not switched off."""
    groups = write_groups(options)
    wanted = {slot for g, slots in GROUPS.items() if groups.get(g, True) for slot in slots}
    return frozenset(wanted & driver.slots)


class TouCoordinator(DataUpdateCoordinator[TouData]):
    tariff_name: str
    base_tariff: Tariff    # the active tariff as defined (preset or custom)
    tariff: Tariff         # ... with the user's own on-peak hours applied
    _spec: dict[str, Any]
    storm: StormManager

    def __init__(self, hass: HomeAssistant, entry: TouConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(minutes=int(entry.options.get(OPT_POLL_MINUTES, 10))))
        self.entry = entry
        d = entry.data
        self.driver: Driver
        if d.get(CONF_BACKEND) == BACKEND_ENTITIES:
            self.driver = EntityDriver(hass, d.get(CONF_ENTITIES) or {})
        else:
            self.driver = ModbusDriver(Eg4Modbus(ModbusConfig(d[CONF_HOST], d.get(CONF_PORT, 502), d.get(CONF_UNIT, 1))))
        self.load_tariff()
        self._holidays: holidays.HolidayBase | None = None  # built off the event loop in async_load_holidays()
        self._last_write = "never"

    def load_tariff(self) -> None:
        """Pick the active tariff from the entry's options (called on every update, so switching or
        editing a tariff takes effect on the next refresh)."""
        self.tariff_name, self._spec = active_of(self.entry.options)
        self.base_tariff, self.tariff = base_of(self._spec), effective_of(self._spec)

    async def async_load_holidays(self) -> None:
        """The holidays library imports country modules lazily, which blocks; do it in a worker thread."""
        d = self.entry.data
        def build() -> holidays.HolidayBase:
            return holidays.country_holidays(d[CONF_COUNTRY], subdiv=d.get(CONF_SUBDIV) or None)

        self._holidays = await self.hass.async_add_executor_job(build)

    def _day_info(self, day: date) -> tuple[bool, str]:
        """(is this an all-off-peak day, label). Weekends, plus holidays from the tariff's own
        rule when it has one (BGE does), else from the country/state calendar if enabled."""
        t = self.tariff
        if t.holiday_fn is not None:
            holiday = t.holiday_fn(day)
        elif t.calendar_holidays and self._holidays is not None:
            holiday = self._holidays.get(day)
        else:
            holiday = None
        no_peak_day = not t.is_peak_weekday(day)
        offpeak = no_peak_day or holiday is not None
        label = f"Holiday: {holiday}" if holiday else ("Weekend" if day.weekday() >= 5 else "Off-peak day" if no_peak_day else "Workday")
        return offpeak, label

    def is_offpeak_day(self, day: date) -> bool:
        return self._day_info(day)[0]

    def _mode(self) -> str:
        o = self.entry.options
        if not o[OPT_ENABLED]:
            return "off"
        return "read_only" if o[OPT_DRY_RUN] else "live"

    async def _async_update_data(self) -> TouData:
        try:
            current = await self.driver.read()
        except DriverError as exc:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="read_failed",
                               translation_placeholders={"error": str(exc)}) from exc

        self.load_tariff()                                                  # the card can switch or edit tariffs at any time
        self.update_interval = timedelta(minutes=max(1, int(self.entry.options.get(OPT_POLL_MINUTES, 10))))
        managed = managed_slots(self.entry.options, self.driver)
        today = dt_util.now().date()
        offpeak, _ = self._day_info(today)
        plan = build_plan(today, self.tariff, settings_from_options(self.entry.options),
                          offpeak_day=offpeak, current_end_soc=current.get(161))
        pending = [p for p in plan.diff(current) if p[0] in managed]
        mode = self._mode()

        if pending and mode == "live":
            self._last_write = await self._apply(pending)
            try:
                current = await self.driver.read()
                pending = [p for p in plan.diff(current) if p[0] in managed]
            except DriverError:
                pass  # next cycle will re-check; the write result is already recorded
        peak_list = [] if offpeak else [list(p) for p in self.tariff.peaks_for(today)]
        peaks = ", ".join(f"{a}-{b}" for a, b in peak_list) if not offpeak else "none (off-peak day)"
        settings = settings_from_options(self.entry.options)
        facts = {"mode": mode, "out_of_sync": len(pending), "start_soc": plan.registers[160],
                 "end_soc": plan.registers[161], "ac_kw": plan.registers[66] / 10, "storm": plan.storm}
        until = float(self.entry.options.get(OPT_STORM_UNTIL, 0))
        storm_last = dt_util.as_local(dt_util.utc_from_timestamp(until)).date() if until else None
        week = week_schedule(today, self.tariff, settings, self._day_info, storm_last=storm_last)
        return TouData(current, plan, pending, mode, self._last_write, peaks, peak_list, week, facts,
                       peak_edit=self._peak_edit(today), managed=managed)

    def _peak_edit(self, today: date) -> dict[str, Any]:
        lib = self.entry.options.get(OPT_TARIFFS) or {}
        info = peak_edit_info(today, self.tariff, self.base_tariff, self._spec.get("peaks") or {})
        info.update({"tariff": self.tariff_name, "builtin": bool(self._spec.get("builtin")),
                     "tariffs": [{"name": n, "builtin": bool(s.get("builtin"))} for n, s in lib.items()]})
        return info

    async def _apply(self, pending: list[tuple[int, int, int | None]]) -> str:
        done: list[int] = []
        for reg, want, was in pending:
            try:
                await self.driver.write(reg, want)
            except DriverError as exc:
                msg = f"{dt_util.now():%H:%M} reg {reg} -> {want} failed: {exc}; retry next cycle"
                _LOGGER.warning(msg)
                return msg
            done.append(reg)
            _LOGGER.info("EG4 TOU: reg %s %s -> %s", reg, was, want)
        return f"{dt_util.now():%H:%M} wrote regs {done}"

    def describe(self, reg: int, value: int | None) -> str:
        if value is None:
            return "?"
        return unpack_time(value) if reg in range(68, 74) or reg in range(152, 158) else str(value)
