from collections.abc import Mapping
from typing import Any

DOMAIN = "eg4_tou"
PLATFORMS = ["sensor", "switch", "number", "time", "select", "button"]
SCAN_INTERVAL_MIN = 10

CONF_HOST = "host"
CONF_PORT = "port"
CONF_UNIT = "unit"
CONF_TARIFF = "tariff"
CONF_CUSTOM = "custom_tariff"
CONF_COUNTRY = "country"
CONF_SUBDIV = "subdiv"
CONF_BACKEND = "backend"          # "modbus" (gateway, the default) or "entities" (existing HA entities)
CONF_ENTITIES = "entities"        # {role: entity_id} when the backend is "entities"
BACKEND_MODBUS, BACKEND_ENTITIES = "modbus", "entities"
CONF_CREATE_DASHBOARD = "create_dashboard"   # wizard choice, consumed once at first setup
CONF_DASHBOARD_URL = "dashboard_url"          # set when we created the dashboard, so we can remove it

# Mutable settings, stored in entry.options
OPT_ENABLED = "enabled"
OPT_DRY_RUN = "dry_run"
OPT_CHARGE_START = "charge_start"
OPT_CHARGE_END = "charge_end"
OPT_END_SOC_SUMMER = "end_soc_summer"
OPT_END_SOC_WINTER = "end_soc_winter"
OPT_END_SOC_OFFPEAK = "end_soc_offpeak"
OPT_AC_KW = "ac_charge_kw"
OPT_WRITE_GROUPS = "write_groups"  # {group: bool}: which kinds of setting the integration may write
OPT_HOLD_CUSTOM = "hold_custom"    # {profile: [[start, end], ...]}: hold windows that replace the derived ones
OPT_POLL_MINUTES = "poll_minutes"
OPT_SOC_GAP = "start_soc_gap"      # AC charge starts this many % below the end SOC
OPT_SOC_MIN = "soc_min"
OPT_SOC_MAX = "soc_max"
OPT_KW_MAX = "ac_kw_max"
OPT_TARIFFS = "tariffs"          # the tariff library: {name: {"base": preset key | "custom": {...}, "peaks": {season: windows}}}
OPT_ACTIVE = "active_tariff"     # name of the tariff in use

# Storm prep: settings the user chooses ...
OPT_STORM_ZONES = "storm_zones"        # NWS zone codes, "MDZ508,MDC025"; empty = never ask the weather service
OPT_STORM_WEATHER = "storm_weather"    # optional weather entity with an hourly forecast
OPT_STORM_NOTIFY = "storm_notify"      # optional notify action, e.g. notify.mobile_app_phone
OPT_STORM_GRID = "storm_grid_down"     # optional binary sensor that is on while the grid is down
OPT_STORM_AUTO = "storm_auto"          # start by itself for NWS warnings and watches
OPT_STORM_TARGET = "storm_target_soc"
OPT_STORM_KW = "storm_charge_kw"
OPT_STORM_START_MAX = "storm_start_soc_max"   # the 12000XP/15kXP accept at most 90 as the charge start SOC
# ... and the session's own state
OPT_STORM_PREP = "storm_prep"
OPT_STORM_UNTIL = "storm_until"
OPT_STORM_HANDLED = "storm_handled"
OPT_STORM_NOTIFIED = "storm_notified"

DEFAULT_OPTIONS: dict[str, Any] = {
    OPT_STORM_ZONES: "", OPT_STORM_WEATHER: "", OPT_STORM_NOTIFY: "", OPT_STORM_GRID: "",
    OPT_STORM_AUTO: False, OPT_STORM_TARGET: 100, OPT_STORM_KW: 10.0, OPT_STORM_START_MAX: 90,
    OPT_STORM_PREP: False, OPT_STORM_UNTIL: 0.0, OPT_STORM_HANDLED: 0.0, OPT_STORM_NOTIFIED: 0.0,
    OPT_ENABLED: False,   # never write until the user turns it on
    OPT_DRY_RUN: True,    # and even then, plan-only until they leave dry run
    OPT_CHARGE_START: "01:00",
    OPT_CHARGE_END: "05:00",
    OPT_END_SOC_SUMMER: 80,
    OPT_END_SOC_WINTER: 70,
    OPT_END_SOC_OFFPEAK: 50,
    OPT_AC_KW: 5.0,
    OPT_WRITE_GROUPS: {"charge_window": True, "hold_windows": True, "soc": True, "power": True},
    OPT_HOLD_CUSTOM: {},
    OPT_POLL_MINUTES: SCAN_INTERVAL_MIN,
    OPT_SOC_GAP: 5,
    OPT_SOC_MIN: 20,
    OPT_SOC_MAX: 100,
    OPT_KW_MAX: 10.0,
}

# Allowed ranges for the numeric settings (shared by the number entities and the update_settings service)
SOC_RANGE = (20, 100)             # defaults: the real limits are the soc_min / soc_max / ac_kw_max options
AC_KW_RANGE = (1.0, 10.0)
AC_KW_STEP = 0.5


def limits(options: "Mapping[str, Any]") -> tuple[tuple[int, int], tuple[float, float]]:
    """((soc_min, soc_max), (kw_min, kw_max)) from the entry options, falling back to the defaults."""
    return ((int(options.get(OPT_SOC_MIN, SOC_RANGE[0])), int(options.get(OPT_SOC_MAX, SOC_RANGE[1]))),
            (AC_KW_RANGE[0], float(options.get(OPT_KW_MAX, AC_KW_RANGE[1]))))
# The settings the timeline card's Edit mode shows (and update_settings accepts)
EDITABLE = (OPT_CHARGE_START, OPT_CHARGE_END, OPT_END_SOC_SUMMER, OPT_END_SOC_WINTER, OPT_END_SOC_OFFPEAK, OPT_AC_KW)


def write_groups(options: Mapping[str, Any]) -> dict[str, bool]:
    """Which kinds of setting the integration may write (a group missing from the options is on)."""
    return {**DEFAULT_OPTIONS[OPT_WRITE_GROUPS], **(options.get(OPT_WRITE_GROUPS) or {})}
