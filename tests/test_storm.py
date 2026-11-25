"""Storm prep: signals, the session rules, the planner override, and the whole flow in Home Assistant."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.eg4_tou import storm
from custom_components.eg4_tou.diagnostics import async_get_config_entry_diagnostics
from custom_components.eg4_tou.modbus import pack_time, unpack_time
from custom_components.eg4_tou.planner import BGE_RL, Settings, build_plan, week_schedule
from custom_components.eg4_tou.storm_manager import NWS_URL
from tests.test_integration import _no_real_network_scan, _setup, gateway  # noqa: F401  (fixtures re-exported)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 25, 14, 0, tzinfo=TZ)        # a winter workday afternoon
ONSET = datetime(2026, 11, 26, 4, 0, tzinfo=TZ)
ENDS = datetime(2026, 11, 26, 19, 0, tzinfo=TZ)


def alerts(*items: dict[str, Any]) -> dict[str, Any]:
    base = {"status": "Actual", "messageType": "Alert", "description": ""}
    return {"features": [{"properties": {**base, **i}} for i in items]}


def warning(event: str = "High Wind Warning", onset: datetime = ONSET, ends: datetime = ENDS) -> dict[str, Any]:
    return {"event": event, "onset": onset.isoformat(), "ends": ends.isoformat()}


# ---- signals ----------------------------------------------------------------

def test_zones():
    assert storm.parse_zones(" orz101, ORC007 ") == ["ORZ101", "ORC007"]
    assert storm.parse_zones("") == []
    assert storm.parse_zones("21001") is None
    assert storm.parse_zones("ORZ101; DROP") is None


def test_alerts_pick_the_strongest_level_and_skip_what_does_not_count():
    s = storm.summarize_alerts(alerts(
        warning("Wind Advisory", ONSET - timedelta(hours=2)), warning(),
        warning("Flood Watch"), {**warning("Blizzard Warning"), "status": "Test"},
        {**warning("Ice Storm Warning"), "messageType": "Cancel"},
        {**warning("Hurricane Warning"), "description": "This warning has been replaced."},
        warning("Winter Storm Warning", NOW - timedelta(days=2), NOW - timedelta(days=1))), NOW)
    assert (s.level, s.onset, s.ends) == ("prep", ONSET, ENDS)
    assert s.headline == "High Wind Warning, Wind Advisory"
    assert [a["event"] for a in s.alerts] == ["High Wind Warning", "Wind Advisory"]
    assert storm.summarize_alerts(alerts(warning("Wind Advisory")), NOW).level == "watch"
    assert storm.summarize_alerts(alerts(), NOW).level == "clear"
    open_ended = storm.summarize_alerts(alerts({"event": "High Wind Warning", "effective": "2026-11-26T04:00:00Z"}), NOW)
    assert open_ended.ends is None and open_ended.onset is not None
    assert storm.summarize_alerts(alerts({"event": "High Wind Warning", "onset": "2026-11-26T04:00:00"}), NOW).onset is None


def hours(wind: float = 5, condition: str = "cloudy", rain: float = 0, count: int = 3) -> list[dict[str, Any]]:
    return [{"datetime": (NOW + timedelta(hours=i + 1)).isoformat(), "wind_speed": wind,
             "condition": condition, "precipitation": rain} for i in range(count)]


def test_forecast_levels():
    assert storm.summarize_forecast(hours(), NOW).level == "clear"
    assert storm.summarize_forecast(hours(26), NOW).level == "watch"
    strong = storm.summarize_forecast(hours(20), NOW, wind_to_mph=2)
    assert strong.level == "prep" and "40 mph" in strong.headline
    assert strong.onset == NOW + timedelta(hours=1) and strong.ends == NOW + timedelta(hours=4)
    snow = storm.summarize_forecast(hours(5, "snowy", 0.2), NOW)
    assert snow.level == "prep" and "frozen" in snow.headline
    assert storm.summarize_forecast(hours(5, "snowy", 0.07), NOW).level == "watch"
    assert storm.summarize_forecast([], NOW).level == "unknown"
    assert storm.summarize_forecast([{"datetime": (NOW + timedelta(days=5)).isoformat()}, {}], NOW).level == "unknown"


def test_recommendation_never_calls_an_unavailable_source_clear():
    prep = storm.Signal("prep", "High Wind Warning", ONSET, ENDS, "nws")
    watch = storm.Signal("watch", "Forecast", NOW, ENDS, "forecast")
    clear, unknown = storm.Signal("clear"), storm.Signal("unknown")
    assert storm.recommend(None, None).level == "not_configured"
    both = storm.recommend(prep, watch)
    assert (both.level, both.source, both.headline) == ("prep", "nws", "High Wind Warning. Forecast")
    assert storm.recommend(clear, watch).source == "forecast"
    assert storm.recommend(clear, None).level == "clear"
    assert storm.recommend(unknown, clear).level == "unknown"


# ---- the session ------------------------------------------------------------

REC = storm.Signal("prep", "High Wind Warning", ONSET, ENDS, "nws")


def decide(session: storm.Session, now: datetime = NOW, rec: storm.Signal = REC, *, auto: bool = True,
           grid_down: bool = False) -> tuple[storm.Session, str | None]:
    return storm.step(session, rec, rec, now, auto=auto, grid_down=grid_down)


def test_automatic_start_is_up_to_a_day_ahead():
    session, what = decide(storm.Session())                                    # 14:00, storm at 04:00 tomorrow
    assert what == "started" and session.until == (ENDS + timedelta(hours=3)).timestamp()
    assert session.handled == ENDS.timestamp()
    assert decide(storm.Session(), NOW.replace(hour=3))[1] is None             # more than 24 h away


def test_automatic_start_needs_a_new_official_prep_level_storm():
    evening = NOW.replace(hour=18)
    assert decide(storm.Session(), evening, auto=False)[1] is None
    assert decide(storm.Session(handled=ENDS.timestamp()), evening)[1] is None       # skipped or done
    assert decide(storm.Session(), evening, storm.Signal("watch", "x", ONSET, ENDS))[1] is None
    assert storm.step(storm.Session(), REC, None, evening, auto=True, grid_down=False)[1] is None  # forecast only
    far = storm.Signal("prep", "x", NOW + timedelta(days=3), NOW + timedelta(days=4), "nws")
    assert decide(storm.Session(), evening, far)[1] is None


def test_session_extends_ends_and_waits_for_the_grid():
    on = storm.turn_on(storm.Session(), REC, NOW)
    later = storm.Signal("prep", "x", ONSET, ENDS + timedelta(hours=6), "nws")
    longer, what = decide(on, rec=later)
    assert what == "extended" and longer.until == (later.ends + timedelta(hours=3)).timestamp()
    after = ENDS + timedelta(hours=4)
    clear = storm.Signal("clear")
    assert decide(on, after - timedelta(hours=3), clear)[1] is None
    assert decide(on, after, clear, grid_down=True) == (on, None)
    done, what = decide(on, after, clear)
    assert what == "ended" and not done.prep and done.handled == ENDS.timestamp()


def test_manual_session_and_storms_without_an_end():
    manual = storm.turn_on(storm.Session(), storm.Signal("clear"), NOW)
    assert manual.until == (NOW + timedelta(hours=36)).timestamp() and manual.handled == 0
    endless = storm.Signal("prep", "x", ONSET, None, "nws")
    skipped = storm.turn_off(storm.Session(), endless, NOW)
    assert skipped.handled == ONSET.timestamp()
    assert decide(skipped, ONSET + timedelta(days=2), endless)[1] is None


# ---- the planner ------------------------------------------------------------

def charge(plan: Any) -> list[str]:
    return [unpack_time(plan.registers[i]) for i in range(68, 74)]


def test_storm_plan_charges_in_the_off_peak_window_closest_to_the_storm():
    day = date(2026, 11, 25)                                       # peaks 07-11 and 17-21
    ahead = build_plan(day, BGE_RL, Settings(storm=True, storm_onset=datetime(2026, 11, 26, 4)), offpeak_day=False)
    r = ahead.registers
    assert ahead.storm and (r[161], r[160], r[66]) == (100, 90, 100)
    assert charge(ahead) == ["00:00", "07:00", "21:00", "23:59", "00:00", "00:00"]
    assert [unpack_time(r[i]) for i in range(152, 158)] == ["00:00", "07:00", "11:00", "17:00", "21:00", "23:59"]
    for onset in (datetime(2026, 11, 26, 0, 30), datetime(2026, 11, 25, 15), datetime(2026, 11, 25, 9)):
        soon = build_plan(day, BGE_RL, Settings(storm=True, storm_onset=onset), offpeak_day=False)
        assert charge(soon) == ["00:00", "07:00", "11:00", "17:00", "21:00", "23:59"]     # mid-day is needed
    assert charge(build_plan(day, BGE_RL, Settings(storm=True), offpeak_day=False)) == charge(ahead)   # unknown
    one_peak = build_plan(date(2026, 7, 8), BGE_RL, Settings(storm=True), offpeak_day=False)
    assert charge(one_peak) == ["00:00", "10:00", "20:00", "23:59", "00:00", "00:00"]
    weekend = build_plan(date(2026, 11, 28), BGE_RL, Settings(storm=True, storm_end_soc=80), offpeak_day=True)
    assert (weekend.registers[68], weekend.registers[69], weekend.registers[160]) == (0, pack_time("23:59"), 78)


def test_storm_ignores_custom_hold_windows_and_stops_after_its_last_day():
    settings = Settings(storm=True, hold_custom={"winter": (("01:00", "02:00"),)})
    assert build_plan(date(2026, 11, 25), BGE_RL, settings, offpeak_day=False).registers[153] != pack_time("02:00")
    week = week_schedule(date(2026, 11, 25), BGE_RL, settings, lambda d: (False, "Workday"), days=3,
                         storm_last=date(2026, 11, 26))
    assert len(week[0]["charge"]) > 1 and len(week[1]["charge"]) > 1
    assert week[2]["charge"] == [["01:00", "05:00"]]


# ---- in Home Assistant ------------------------------------------------------

def live_warning() -> dict[str, Any]:
    now = dt_util.now()
    return alerts(warning(onset=now - timedelta(hours=1), ends=now + timedelta(hours=6)))


async def settle(hass: HomeAssistant) -> None:
    for _ in range(3):
        await hass.async_block_till_done()


def notify_recorder(hass: HomeAssistant) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def send(call: ServiceCall) -> None:
        sent.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_phone", send)
    return sent


async def test_nothing_is_asked_of_the_network_until_configured(hass: HomeAssistant, gateway, aioclient_mock):
    await _setup(hass, gateway)
    state = hass.states.get("sensor.eg4_tou_storm_recommendation")
    assert state.state == "not_configured" and state.attributes["prep"] is False
    assert aioclient_mock.call_count == 0


async def test_switch_charges_for_the_storm_and_restores(hass: HomeAssistant, gateway):
    await _setup(hass, gateway, enabled=True, dry_run=False)
    normal_end = gateway.regs[161]
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.eg4_tou_storm_prep"}, blocking=True)
    await settle(hass)
    assert (gateway.regs[161], gateway.regs[160], gateway.regs[66]) == (100, 90, 100)
    assert hass.states.get("switch.eg4_tou_storm_prep").state == "on"
    attrs = hass.states.get("sensor.eg4_tou_storm_recommendation").attributes
    assert attrs["until"] and attrs["controls"] == {
        "prep": "switch.eg4_tou_storm_prep", "automatic": "switch.eg4_tou_storm_prep_automatic",
        "skip": "button.eg4_tou_skip_this_storm", "target": "number.eg4_tou_storm_prep_charge_target"}
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.eg4_tou_storm_prep"}, blocking=True)
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.eg4_tou_storm_prep_charge_target", "value": 85}, blocking=True)
    await settle(hass)
    assert (gateway.regs[161], gateway.regs[160]) == (85, 83)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.eg4_tou_storm_prep"}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.eg4_tou_storm_prep"}, blocking=True)
    await settle(hass)
    assert gateway.regs[161] == normal_end and gateway.regs[66] == 50


async def test_read_only_mode_shows_the_storm_plan_without_writing(hass: HomeAssistant, gateway):
    await _setup(hass, gateway, enabled=True, dry_run=True)
    gateway.writes.clear()
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.eg4_tou_storm_prep"}, blocking=True)
    await settle(hass)
    assert gateway.writes == []
    attrs = hass.states.get("sensor.eg4_tou_active_profile").attributes
    assert attrs["plan"]["storm"] is True and attrs["timeline"]["storm"] is True
    assert "Grid charge windows (storm prep)" in [r["rule"] for r in attrs["rules"]]


async def test_warning_starts_it_automatically_and_tells_the_phone(hass: HomeAssistant, gateway, aioclient_mock):
    aioclient_mock.get(NWS_URL, json=live_warning())
    sent = notify_recorder(hass)
    events = async_capture_events(hass, "eg4_tou_storm")
    entry = await _setup(hass, gateway, storm_zones="ORZ101", storm_auto=True, storm_notify="notify.mobile_app_phone")
    await settle(hass)
    assert entry.options["storm_prep"] is True
    assert [e.data["type"] for e in events] == ["started"]
    assert sent[-1]["data"]["actions"][0]["action"] == "EG4_TOU_STORM_SKIP"
    state = hass.states.get("sensor.eg4_tou_storm_recommendation")
    assert state.state == "prep" and state.attributes["alerts"][0]["event"] == "High Wind Warning"
    hass.bus.async_fire("mobile_app_notification_action", {"action": "EG4_TOU_STORM_SKIP"})
    await settle(hass)
    assert entry.options["storm_prep"] is False
    await entry.runtime_data.storm.async_refresh()
    await settle(hass)
    assert entry.options["storm_prep"] is False, "a skipped storm is not started again"
    assert hass.states.get("sensor.eg4_tou_storm_recommendation").attributes["skipped"] is True
    out = await async_get_config_entry_diagnostics(hass, entry)
    assert out["options"]["storm_zones"] == "**REDACTED**"


async def test_manual_mode_recommends_once_and_the_phone_button_starts_it(hass: HomeAssistant, gateway, aioclient_mock):
    aioclient_mock.get(NWS_URL, json=live_warning())
    sent = notify_recorder(hass)
    entry = await _setup(hass, gateway, storm_zones="ORZ101", storm_notify="notify.mobile_app_phone")
    await entry.runtime_data.storm.async_refresh()
    await settle(hass)
    assert [s["title"] for s in sent] == ["Storm coming: prep the battery"]
    assert entry.options["storm_prep"] is False
    hass.bus.async_fire("mobile_app_notification_action", {"action": "SOMETHING_ELSE"})
    hass.bus.async_fire("mobile_app_notification_action", {"action": "EG4_TOU_STORM_ON"})
    await settle(hass)
    assert entry.options["storm_prep"] is True
    await hass.services.async_call("button", "press", {"entity_id": "button.eg4_tou_skip_this_storm"}, blocking=True)
    await settle(hass)
    assert entry.options["storm_prep"] is False and sent[-1]["title"] == "Storm prep skipped"


async def test_session_ends_by_itself_once_the_grid_is_up(hass: HomeAssistant, gateway):
    hass.states.async_set("binary_sensor.grid_down", "on")
    past = (dt_util.now() - timedelta(minutes=1)).timestamp()
    entry = await _setup(hass, gateway, storm_prep=True, storm_until=past, storm_grid_down="binary_sensor.grid_down",
                         storm_notify="notify.missing")
    assert entry.options["storm_prep"] is True
    hass.states.async_set("binary_sensor.grid_down", "off")
    await entry.runtime_data.storm.async_refresh()
    await settle(hass)
    assert entry.options["storm_prep"] is False   # and the missing notify action did not get in the way


async def test_weather_service_failure_reads_unavailable(hass: HomeAssistant, gateway, aioclient_mock):
    aioclient_mock.get(NWS_URL, status=500)
    await _setup(hass, gateway, storm_zones="ORZ101", storm_auto=True)
    assert hass.states.get("sensor.eg4_tou_storm_recommendation").state == "unknown"


async def test_weather_entity_is_a_second_opinion_in_its_own_units(hass: HomeAssistant, gateway):
    async def forecasts(call: ServiceCall) -> dict[str, Any]:
        when = (dt_util.now() + timedelta(hours=2)).isoformat()
        return {"weather.home": {"forecast": [{"datetime": when, "wind_speed": 70, "condition": "rainy"}]}}

    hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)
    hass.states.async_set("weather.home", "rainy", {"wind_speed_unit": "km/h", "precipitation_unit": "mm"})
    entry = await _setup(hass, gateway, storm_weather="weather.home", storm_auto=True)
    state = hass.states.get("sensor.eg4_tou_storm_recommendation")
    assert state.state == "prep" and state.attributes["source"] == "forecast"      # 70 km/h is 43 mph
    assert entry.options["storm_prep"] is False, "the forecast alone never starts it"
    hass.config_entries.async_update_entry(entry, options={**entry.options, "storm_weather": "weather.gone"})
    await settle(hass)
    assert hass.states.get("sensor.eg4_tou_storm_recommendation").state == "unknown"


async def test_configure_step_validates_and_saves(hass: HomeAssistant, gateway, aioclient_mock):
    aioclient_mock.get(NWS_URL, json=alerts())
    entry = await _setup(hass, gateway)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "storm"})
    assert r["step_id"] == "storm"
    base = {"storm_charge_kw": 8, "storm_start_soc_max": 90}
    r = await hass.config_entries.options.async_configure(r["flow_id"],
                                                          {**base, "storm_zones": "Maryland", "storm_notify": "phone"})
    assert r["errors"] == {"storm_zones": "storm_zones_invalid", "storm_notify": "storm_notify_invalid"}
    r = await hass.config_entries.options.async_configure(r["flow_id"], {**base, "storm_zones": "orz101 orc007"})
    assert r["type"] == "create_entry"
    await settle(hass)
    assert entry.options["storm_zones"] == "ORZ101,ORC007" and entry.options["storm_charge_kw"] == 8
    assert hass.states.get("sensor.eg4_tou_storm_recommendation").state == "clear"


async def test_a_manager_left_over_from_a_reload_decides_nothing(hass: HomeAssistant, gateway):
    entry = await _setup(hass, gateway)
    old = entry.runtime_data.storm
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "storm_prep": True, "storm_until": 1.0})
    await old.async_options_updated()
    assert entry.runtime_data.storm is not old
