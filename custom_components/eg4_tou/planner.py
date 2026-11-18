"""Pure planning logic: tariff + settings + date -> the register values we want.

No Home Assistant imports here, so it is unit-testable and easy to explain in a
video. The planner only decides; the coordinator reads, diffs and writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from .modbus import POWER_REG, pack_time

END_OF_DAY = "23:59"
MAX_PEAKS_PER_DAY = 2  # AC First has 3 windows, which covers at most 2 peaks


@dataclass(frozen=True)
class Season:
    name: str
    start: tuple[int, int]  # (month, day) inclusive
    end: tuple[int, int]    # (month, day) inclusive; may wrap the new year

    def contains(self, day: date) -> bool:
        md = (day.month, day.day)
        if self.start <= self.end:
            return self.start <= md <= self.end
        return md >= self.start or md <= self.end


@dataclass(frozen=True)
class Tariff:
    """Weekday on-peak windows per season, as (start, end) 'HH:MM' strings.

    Weekends and holidays are treated as all off-peak. `shift_fn` optionally
    says whether a date falls in a period where the shift season's peaks move
    `shift_hours` later (BGE does this around daylight saving time).
    """

    name: str
    seasons: tuple[Season, ...]
    peaks: dict[str, tuple[tuple[str, str], ...]]
    shift_season: str | None = None
    shift_hours: int = 0
    shift_fn: Callable[[date], bool] | None = None
    offpeak_weekends: bool = True  # weekends and holidays are all off-peak

    def season_for(self, day: date) -> str:
        for season in self.seasons:
            if season.contains(day):
                return season.name
        raise ValueError(f"no season covers {day}")

    def in_shift(self, day: date) -> bool:
        return bool(self.shift_fn and self.shift_fn(day))

    def peaks_for(self, day: date) -> tuple[tuple[str, str], ...]:
        season = self.season_for(day)
        windows = self.peaks.get(season, ())
        if self.shift_hours and season == self.shift_season and self.in_shift(day):
            windows = tuple((_shift(a, self.shift_hours), _shift(b, self.shift_hours)) for a, b in windows)
        return windows


def _shift(hhmm: str, hours: int) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h + hours) % 24:02d}:{m:02d}"


@dataclass(frozen=True)
class Settings:
    charge_window: tuple[str, str] = ("01:00", "05:00")
    end_soc: dict[str, int] = field(
        default_factory=lambda: {"summer": 80, "winter": 70, "offpeak": 50}
    )
    start_soc_gap: int = 5      # AC charge starts this many % below the end SOC
    ac_charge_kw: float = 5.0


def ac_first_windows(peaks: tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
    """Complement of the peak windows within the day, padded to 3 windows.

    AC First holds the battery (grid carries the house) outside the peak, so the
    stored energy is spent only on peak hours. No peaks -> hold all day.
    """
    if len(peaks) > MAX_PEAKS_PER_DAY:
        raise ValueError(f"at most {MAX_PEAKS_PER_DAY} on-peak windows per day are supported")
    windows: list[tuple[str, str]] = []
    cursor = "00:00"
    for start, end in sorted(peaks):
        if start > cursor:
            windows.append((cursor, start))
        cursor = end
    if cursor < END_OF_DAY:
        windows.append((cursor, END_OF_DAY))
    windows += [("00:00", "00:00")] * (3 - len(windows))
    return windows


@dataclass(frozen=True)
class Plan:
    profile: str
    registers: dict[int, int]   # reg -> raw value
    write_order: list[int]

    def diff(self, current: dict[int, int]) -> list[tuple[int, int, int | None]]:
        """(reg, wanted, current) for registers that need writing, in a safe order."""
        out = []
        for reg in self.write_order:
            if current.get(reg) != self.registers[reg]:
                out.append((reg, self.registers[reg], current.get(reg)))
        return out


def build_plan(
    day: date,
    tariff: Tariff,
    settings: Settings,
    *,
    offpeak_day: bool,
    current_end_soc: int | None = None,
) -> Plan:
    """Compute the desired register map for `day`.

    offpeak_day: True for weekends and holidays (caller decides, e.g. from the
    holidays library). current_end_soc is used only to order the SOC writes so
    the inverter never holds an end SOC below its start SOC mid-update.
    """
    season = tariff.season_for(day)
    profile = "offpeak" if offpeak_day else season
    peaks = () if offpeak_day else tariff.peaks_for(day)
    first = ac_first_windows(peaks)

    end = int(settings.end_soc.get(profile, settings.end_soc.get("offpeak", 50)))
    start = max(0, end - settings.start_soc_gap)
    c1, c2 = settings.charge_window

    regs: dict[int, int] = {
        POWER_REG: int(round(settings.ac_charge_kw * 10)),
        68: pack_time(c1),
        69: pack_time(c2),
        70: pack_time("00:00"),
        71: pack_time("00:00"),
        72: pack_time("00:00"),
        73: pack_time("00:00"),
        160: start,
        161: end,
    }
    for i, (a, b) in enumerate(first):
        regs[152 + 2 * i] = pack_time(a)
        regs[153 + 2 * i] = pack_time(b)

    soc_order = [160, 161] if current_end_soc is None or end < current_end_soc else [161, 160]
    order = [POWER_REG, *range(68, 74), *range(152, 158), *soc_order]
    return Plan(profile=profile, registers=regs, write_order=order)


def next_day(day: date) -> date:
    return day + timedelta(days=1)


# ---- Custom tariffs -----------------------------------------------------

def validate_peaks(peaks: list[tuple[str, str]]) -> str | None:
    """Return an error key, or None if the on-peak windows are usable."""
    if len(peaks) > MAX_PEAKS_PER_DAY:
        return "too_many_peaks"
    for start, end in peaks:
        if not start < end:
            return "peak_order"          # end must be after start, same day
    ordered = sorted(peaks)
    for (_, e1), (s2, _) in zip(ordered, ordered[1:]):
        if s2 < e1:
            return "peak_overlap"
    return None


def winter_dates(summer_start: tuple[int, int], summer_end: tuple[int, int]):
    """The rest of the year: from the day after summer ends to the day before
    it starts. Returns None if summer would cover the whole year."""
    a = date(2024, *summer_start)  # leap year so Feb 29 is a valid input
    b = date(2024, *summer_end)
    w_start, w_end = b + timedelta(days=1), a - timedelta(days=1)
    if (w_start.month, w_start.day) == (a.month, a.day) or (w_end.month, w_end.day) == (b.month, b.day):
        return None
    return (w_start.month, w_start.day), (w_end.month, w_end.day)


def tariff_from_custom(c: dict) -> Tariff:
    """Build a Tariff from the dict the setup flow stores.

    {"seasons": {"summer": {"start": [6,1], "end": [9,30]}, "winter": {...}},
     "peaks": {"summer": [["10:00","20:00"]], "winter": [[...], [...]]},
     "offpeak_weekends": true}
    """
    seasons = tuple(
        Season(name, tuple(v["start"]), tuple(v["end"])) for name, v in c["seasons"].items()
    )
    peaks = {n: tuple((a, b) for a, b in w) for n, w in c["peaks"].items()}
    for n, w in peaks.items():
        err = validate_peaks(list(w))
        if err:
            raise ValueError(f"{n}: {err}")
    return Tariff(name="Custom", seasons=seasons, peaks=peaks,
                  offpeak_weekends=bool(c.get("offpeak_weekends", True)))


# ---- Presets -------------------------------------------------------------

def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _last_sunday(year: int, month: int) -> date:
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _bge_shift(day: date) -> bool:
    """2nd Sunday of March up to (not incl.) the 1st Sunday of April, and the
    last Sunday of October up to (not incl.) the 1st Sunday of November.
    Whether the end Sundays are inclusive is an assumption carried over from
    the author's own setup; confirm against your tariff sheet."""
    y = day.year
    return (_nth_sunday(y, 3, 2) <= day < _nth_sunday(y, 4, 1)
            or _last_sunday(y, 10) <= day < _nth_sunday(y, 11, 1))


BGE_RL = Tariff(
    name="BGE Schedule RL (Maryland)",
    seasons=(
        Season("summer", (6, 1), (9, 30)),
        Season("winter", (10, 1), (5, 31)),
    ),
    peaks={
        "summer": (("10:00", "20:00"),),
        "winter": (("07:00", "11:00"), ("17:00", "21:00")),
    },
    shift_season="winter",
    shift_hours=1,
    shift_fn=lambda d: _bge_shift(d),
)

PRESETS: dict[str, Tariff] = {"bge_rl": BGE_RL}
