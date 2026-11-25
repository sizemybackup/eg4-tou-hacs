"""Storm prep: pure logic (no Home Assistant imports).

Two signals say whether a storm threatens grid power: active US National Weather Service alerts
for the user's zones, and (optionally) the hourly forecast of a weather entity. A "storm session"
then charges the battery to a storm target in every off-peak hour until the storm is over.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

PREP, WATCH, CLEAR, UNKNOWN, NOT_CONFIGURED = "prep", "watch", "clear", "unknown", "not_configured"
LEVELS = [PREP, WATCH, CLEAR, UNKNOWN, NOT_CONFIGURED]

PREP_EVENTS = frozenset({
    "High Wind Warning", "High Wind Watch", "Winter Storm Warning", "Winter Storm Watch", "Ice Storm Warning",
    "Blizzard Warning", "Hurricane Warning", "Hurricane Watch", "Tropical Storm Warning", "Tropical Storm Watch",
    "Hurricane Force Wind Warning", "Extreme Wind Warning",
})
WATCH_EVENTS = frozenset({
    "Wind Advisory", "Winter Weather Advisory", "Severe Thunderstorm Watch", "Severe Thunderstorm Warning",
    "Tornado Watch", "Tornado Warning",
})

LEAD = timedelta(hours=24)            # automatic mode starts at most this long before the storm
AFTER = timedelta(hours=3)            # how long after the storm the session stays on
OPEN_ENDED = timedelta(hours=36)      # length of a session with no known storm end
OFFPEAK_MARGIN = timedelta(hours=4)   # waiting for the evening off-peak needs this much time after it begins
# Forecast thresholds (sustained wind in mph, frozen precipitation in inches of water)
WIND_PREP, WIND_WATCH, FROZEN_PREP, FROZEN_WATCH = 35.0, 25.0, 0.5, 0.2
FORECAST_HOURS = 48
_ZONE = re.compile(r"^[A-Z]{2}[CZ]\d{3}$")


@dataclass(frozen=True)
class Signal:
    level: str
    headline: str = ""
    onset: datetime | None = None
    ends: datetime | None = None
    source: str = ""
    alerts: tuple[dict[str, Any], ...] = ()


def parse_zones(text: str) -> list[str] | None:
    """["ORZ101", "ORC007"] from "orz101, ORC007"; None if anything is not a zone code."""
    zones = [z for z in re.split(r"[\s,]+", text.strip().upper()) if z]
    return zones if all(_ZONE.match(z) for z in zones) else None


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else None


def summarize_alerts(data: Mapping[str, Any], now: datetime) -> Signal:
    """The grid-threatening alerts in an api.weather.gov /alerts/active response."""
    found: list[dict[str, Any]] = []
    for feature in data["features"]:
        p = feature["properties"]
        event = p.get("event")
        level = PREP if event in PREP_EVENTS else WATCH if event in WATCH_EVENTS else None
        if level is None or p.get("status") != "Actual" or p.get("messageType") == "Cancel":
            continue
        text = (p.get("description") or "").lower()
        if "has been replaced" in text or "has been cancelled" in text:
            continue
        onset = _when(p.get("onset") or p.get("effective"))
        ends = _when(p.get("ends") or p.get("expires"))
        if ends is not None and ends <= now:
            continue
        found.append({"event": event, "level": level, "onset": onset, "ends": ends})
    if not found:
        return Signal(CLEAR, source="nws")
    found.sort(key=lambda a: (a["level"] != PREP, a["onset"] or now))
    level = found[0]["level"]
    top = [a for a in found if a["level"] == level]
    return Signal(
        level, ", ".join(dict.fromkeys(a["event"] for a in found)),
        min((a["onset"] for a in top if a["onset"]), default=None),
        max((a["ends"] for a in top if a["ends"]), default=None), "nws",
        tuple({**a, "onset": a["onset"] and a["onset"].isoformat(), "ends": a["ends"] and a["ends"].isoformat()}
              for a in found))


def summarize_forecast(hours: Sequence[Mapping[str, Any]], now: datetime, wind_to_mph: float = 1.0,
                       precip_to_in: float = 1.0) -> Signal:
    """A second opinion from an hourly weather forecast over the next 48 hours."""
    seen = 0
    top_wind = frozen = 0.0
    first: datetime | None = None
    last: datetime | None = None
    for hour in hours:
        at = _when(hour.get("datetime"))
        if at is None or not now - timedelta(hours=1) <= at <= now + timedelta(hours=FORECAST_HOURS):
            continue
        seen += 1
        wind = float(hour.get("wind_speed") or 0) * wind_to_mph
        icy = hour.get("condition") in ("snowy", "snowy-rainy", "hail")
        if icy:
            frozen += float(hour.get("precipitation") or 0) * precip_to_in
        top_wind = max(top_wind, wind)
        if wind >= WIND_WATCH or icy:
            first = first or at
            last = at + timedelta(hours=1)
    if not seen:
        return Signal(UNKNOWN, source="forecast")
    if top_wind >= WIND_PREP or frozen >= FROZEN_PREP:
        level = PREP
    elif top_wind >= WIND_WATCH or frozen >= FROZEN_WATCH:
        level = WATCH
    else:
        return Signal(CLEAR, source="forecast")
    headline = f"Forecast: wind to {top_wind:.0f} mph sustained"
    if frozen:
        headline += f", {frozen:.1f} in frozen precipitation"
    return Signal(level, headline, first, last, "forecast")


def recommend(nws: Signal | None, forecast: Signal | None) -> Signal:
    """Combine the configured signals. An unavailable source is never reported as "clear"."""
    configured = [s for s in (nws, forecast) if s is not None]
    if not configured:
        return Signal(NOT_CONFIGURED)
    for level in (PREP, WATCH):
        matching = [s for s in configured if s.level == level]
        if matching:
            chosen = matching[0]   # the official alert wins a tie
            headline = ". ".join(s.headline for s in configured if s.level in (PREP, WATCH))
            return Signal(level, headline, chosen.onset, chosen.ends, chosen.source,
                          nws.alerts if nws else ())
    return Signal(CLEAR if all(s.level == CLEAR for s in configured) else UNKNOWN)


@dataclass(frozen=True)
class Session:
    """The persisted state of storm prep (timestamps in seconds; 0 means unset)."""

    prep: bool = False
    until: float = 0.0
    handled: float = 0.0   # storms starting at or before this were already prepared for, or skipped


def storm_end(rec: Signal, now: datetime) -> datetime | None:
    return rec.ends if rec.level in (PREP, WATCH) and rec.ends is not None and rec.ends > now else None


def _handled_mark(rec: Signal, session: Session, now: datetime) -> float:
    end = storm_end(rec, now)
    if end is not None:
        return max(session.handled, end.timestamp())
    if rec.level == PREP:   # no end given: remember at least that this one has been dealt with
        return max(session.handled, max(rec.onset or now, now).timestamp())
    return session.handled


def turn_on(session: Session, rec: Signal, now: datetime) -> Session:
    end = storm_end(rec, now)
    until = end + AFTER if end is not None else now + OPEN_ENDED
    return Session(True, until.timestamp(), _handled_mark(rec, session, now))


def turn_off(session: Session, rec: Signal, now: datetime) -> Session:
    """Also "skip this storm": automatic mode will not start it again."""
    return Session(False, 0.0, _handled_mark(rec, session, now))


def is_new(rec: Signal, handled: float, now: datetime) -> bool:
    return (rec.ends is None or rec.ends > now) and (rec.onset or now).timestamp() > handled


def wait_for_offpeak(now: datetime, onset: datetime, peaks: Sequence[Sequence[str]]) -> bool:
    """True between today's first peak and its last one, if the storm starts late enough that
    charging after the last peak still leaves plenty of time."""
    if not peaks:
        return False

    def today(hhmm: str) -> datetime:
        h, m = (int(x) for x in hhmm.split(":"))
        return datetime.combine(now.date(), time(h, m), now.tzinfo)

    ordered = sorted((str(a), str(b)) for a, b in peaks)
    return today(ordered[0][0]) <= now < today(ordered[-1][0]) and onset >= today(ordered[-1][1]) + OFFPEAK_MARGIN


def step(session: Session, rec: Signal, nws: Signal | None, now: datetime, *, auto: bool, grid_down: bool,
         peaks: Sequence[Sequence[str]]) -> tuple[Session, str | None]:
    """One periodic decision: (new session, "started" | "ended" | "extended" | None)."""
    if session.prep:
        end = storm_end(rec, now)
        if end is not None and (end + AFTER).timestamp() > session.until:
            return Session(True, (end + AFTER).timestamp(), max(session.handled, end.timestamp())), "extended"
        if now.timestamp() >= session.until and not grid_down:
            return turn_off(session, rec, now), "ended"
        return session, None
    if (auto and nws is not None and nws.level == PREP and is_new(nws, session.handled, now)
            and (nws.onset is None or (nws.onset - now <= LEAD and not wait_for_offpeak(now, nws.onset, peaks)))):
        return turn_on(session, nws, now), "started"
    return session, None
