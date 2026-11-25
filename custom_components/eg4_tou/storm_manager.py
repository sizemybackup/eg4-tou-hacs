"""Storm prep inside Home Assistant: fetch the signals, run the session, tell the user.

Nothing here talks to the inverter. A session only flips the `storm_prep` option; the planner then
plans differently and the normal controller (with its read-only mode and write groups) applies it.
Network access is opt-in: the National Weather Service is asked only when zone codes are configured,
and only those codes are sent.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import DistanceConverter, SpeedConverter

from . import storm
from .const import (
    DOMAIN, OPT_STORM_AUTO, OPT_STORM_GRID, OPT_STORM_HANDLED, OPT_STORM_NOTIFIED, OPT_STORM_NOTIFY,
    OPT_STORM_PREP, OPT_STORM_TARGET, OPT_STORM_UNTIL, OPT_STORM_WEATHER, OPT_STORM_ZONES,
)

if TYPE_CHECKING:
    from .coordinator import TouConfigEntry, TouCoordinator

_LOGGER = logging.getLogger(__name__)

NWS_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = "eg4-tou-hacs (Home Assistant integration)"
INTERVAL = timedelta(minutes=10)
EVENT = "eg4_tou_storm"
ACTION_ON, ACTION_SKIP = "EG4_TOU_STORM_ON", "EG4_TOU_STORM_SKIP"


@dataclass
class StormData:
    recommendation: storm.Signal
    nws: storm.Signal | None
    forecast: storm.Signal | None


class StormManager(DataUpdateCoordinator[StormData]):
    def __init__(self, hass: HomeAssistant, entry: TouConfigEntry, owner: TouCoordinator) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} storm", update_interval=INTERVAL)
        self.entry = entry
        self.owner = owner
        self._sources = self._source_options()

    def _source_options(self) -> tuple[str, str]:
        o = self.entry.options
        return str(o.get(OPT_STORM_ZONES) or ""), str(o.get(OPT_STORM_WEATHER) or "")

    async def async_start(self) -> None:
        # a listener keeps the 10 minute timer running even if the user disables the sensor
        self.entry.async_on_unload(self.async_add_listener(lambda: None))
        self.entry.async_on_unload(self.hass.bus.async_listen("mobile_app_notification_action", self._on_action))
        await self.async_refresh()

    async def _fetch_nws(self, zones: str) -> storm.Signal | None:
        if not zones:
            return None
        try:
            async with asyncio.timeout(15):
                response = await async_get_clientsession(self.hass).get(
                    NWS_URL, params={"zone": zones},
                    headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
                response.raise_for_status()
                data = await response.json(content_type=None)
            return storm.summarize_alerts(data, dt_util.now())
        except (aiohttp.ClientError, TimeoutError, KeyError, TypeError, ValueError) as exc:
            _LOGGER.warning("Could not read National Weather Service alerts: %r", exc)
            return storm.Signal(storm.UNKNOWN, source="nws")

    async def _fetch_forecast(self, entity_id: str) -> storm.Signal | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        try:
            if state is None:
                raise ValueError("entity not found")
            reply = await self.hass.services.async_call(
                "weather", "get_forecasts", {"entity_id": entity_id, "type": "hourly"},
                blocking=True, return_response=True)
            hours: Any = (reply or {})[entity_id]
            wind = SpeedConverter.convert(1.0, state.attributes.get("wind_speed_unit") or "mph", "mph")
            rain = DistanceConverter.convert(1.0, state.attributes.get("precipitation_unit") or "in", "in")
            return storm.summarize_forecast(hours["forecast"], dt_util.now(), wind, rain)
        except (HomeAssistantError, KeyError, TypeError, ValueError) as exc:
            _LOGGER.warning("Could not read the forecast of %s: %r", entity_id, exc)
            return storm.Signal(storm.UNKNOWN, source="forecast")

    async def _async_update_data(self) -> StormData:
        zones, weather = self._sources = self._source_options()
        nws, forecast = await self._fetch_nws(zones), await self._fetch_forecast(weather)
        data = StormData(storm.recommend(nws, forecast), nws, forecast)
        await self._evaluate(data)
        return data

    async def async_options_updated(self) -> None:
        """New sources are fetched at once; any other change only re-runs the decision."""
        if self._source_options() != self._sources:
            await self.async_refresh()
            return
        if self.data is not None:
            await self._evaluate(self.data)
            self.async_update_listeners()

    def session(self) -> storm.Session:
        o = self.entry.options
        return storm.Session(bool(o[OPT_STORM_PREP]), float(o[OPT_STORM_UNTIL]), float(o[OPT_STORM_HANDLED]))

    def _save(self, session: storm.Session, **extra: Any) -> None:
        new = {**self.entry.options, OPT_STORM_PREP: session.prep, OPT_STORM_UNTIL: session.until,
               OPT_STORM_HANDLED: session.handled, **extra}
        if new != dict(self.entry.options):
            self.hass.config_entries.async_update_entry(self.entry, options=new)

    def _grid_down(self) -> bool:
        entity_id = self.entry.options.get(OPT_STORM_GRID)
        return bool(entity_id) and self.hass.states.is_state(str(entity_id), "on")

    async def _evaluate(self, data: StormData) -> None:
        if getattr(self.entry, "runtime_data", None) is not self.owner:
            return   # the entry is being reloaded: its new manager decides, not this one
        now = dt_util.now()
        today = now.date()
        peaks = [] if self.owner.is_offpeak_day(today) else self.owner.tariff.peaks_for(today)
        session, what = storm.step(self.session(), data.recommendation, data.nws, now,
                                   auto=bool(self.entry.options[OPT_STORM_AUTO]),
                                   grid_down=self._grid_down(), peaks=peaks)
        rec = data.recommendation
        notified = float(self.entry.options[OPT_STORM_NOTIFIED])
        if what is None and rec.level == storm.PREP and not session.prep \
                and storm.is_new(rec, max(session.handled, notified), now):
            what = "recommended"
            notified = (rec.ends or max(rec.onset or now, now)).timestamp()
        self._save(session, **{OPT_STORM_NOTIFIED: notified})
        if what and what != "extended":
            await self._tell(what, rec, session)

    async def async_turn_on(self) -> None:
        if not self.session().prep:
            await self._user_change(storm.turn_on, "started")

    async def async_turn_off(self) -> None:
        if self.session().prep:
            await self._user_change(storm.turn_off, "ended")

    async def async_skip(self) -> None:
        await self._user_change(storm.turn_off, "skipped")

    async def _user_change(self, change: Any, what: str) -> None:
        rec = self.data.recommendation
        session = change(self.session(), rec, dt_util.now())
        self._save(session)
        self.async_update_listeners()
        await self._tell(what, rec, session)

    @callback
    def _on_action(self, event: Event[Any]) -> None:
        action = event.data.get("action")
        if action == ACTION_ON:
            self.hass.async_create_task(self.async_turn_on())
        elif action == ACTION_SKIP:
            self.hass.async_create_task(self.async_skip())

    def _message(self, what: str, rec: storm.Signal, session: storm.Session) -> tuple[str, str, list[dict[str, str]]]:
        def stamp(ts: float) -> str:
            return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%a %H:%M")

        target = int(self.entry.options[OPT_STORM_TARGET])
        if what == "recommended":
            when = f" Starts {stamp(rec.onset.timestamp())}." if rec.onset and rec.onset > dt_util.now() else ""
            how = ("Storm prep will switch on by itself." if self.entry.options[OPT_STORM_AUTO] and rec.source == "nws"
                   else "Recommended: turn on storm prep.")
            return ("Storm coming: prep the battery", f"{rec.headline}.{when} {how} Target {target}%.",
                    [{"action": ACTION_ON, "title": "Prep now"}, {"action": ACTION_SKIP, "title": "Skip this storm"}])
        if what == "started":
            reason = f"{rec.headline}. " if rec.headline else ""
            return ("Storm prep on", f"{reason}Charging to {target}% in off-peak hours until {stamp(session.until)}.",
                    [{"action": ACTION_SKIP, "title": "Cancel"}])
        if what == "skipped":
            return "Storm prep skipped", "The battery stays on the normal schedule.", []
        return "Storm prep ended", "Back to the normal time-of-use schedule.", []

    async def _tell(self, what: str, rec: storm.Signal, session: storm.Session) -> None:
        """A notification in Home Assistant, an event for automations, and the optional phone message."""
        title, message, actions = self._message(what, rec, session)
        self.hass.bus.async_fire(EVENT, {"type": what, "entry_id": self.entry.entry_id, "level": rec.level,
                                         "headline": rec.headline})
        persistent_notification.async_create(self.hass, message, title=title,
                                             notification_id=f"eg4_tou_storm_{self.entry.entry_id}")
        target = str(self.entry.options.get(OPT_STORM_NOTIFY) or "")
        if not target:
            return
        domain, _, service = target.partition(".")
        try:
            await self.hass.services.async_call(domain, service, {
                "title": title, "message": message, "data": {"tag": "eg4_tou_storm", "actions": actions}},
                blocking=True)
        except Exception as exc:  # noqa: BLE001 - a failed phone message must never stop storm prep
            _LOGGER.warning("Could not send the storm message with %s: %s", target, exc)
