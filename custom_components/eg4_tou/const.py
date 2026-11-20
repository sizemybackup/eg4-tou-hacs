DOMAIN = "eg4_tou"
PLATFORMS = ["sensor", "switch", "number", "time"]
SCAN_INTERVAL_MIN = 10

CONF_HOST = "host"
CONF_PORT = "port"
CONF_UNIT = "unit"
CONF_TARIFF = "tariff"
CONF_CUSTOM = "custom_tariff"
CONF_COUNTRY = "country"
CONF_SUBDIV = "subdiv"
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
OPT_PEAKS = "peak_overrides"   # {season: [[start, end], ...]} set from the timeline card; layered over the tariff

DEFAULT_OPTIONS = {
    OPT_ENABLED: False,   # never write until the user turns it on
    OPT_DRY_RUN: True,    # and even then, plan-only until they leave dry run
    OPT_CHARGE_START: "01:00",
    OPT_CHARGE_END: "05:00",
    OPT_END_SOC_SUMMER: 80,
    OPT_END_SOC_WINTER: 70,
    OPT_END_SOC_OFFPEAK: 50,
    OPT_AC_KW: 5.0,
    OPT_PEAKS: {},
}

# Allowed ranges for the numeric settings (shared by the number entities and the update_settings service)
SOC_RANGE = (20, 100)
AC_KW_RANGE = (1.0, 10.0)
AC_KW_STEP = 0.5
# The settings the timeline card's Edit mode shows (and update_settings accepts)
EDITABLE = (OPT_CHARGE_START, OPT_CHARGE_END, OPT_END_SOC_SUMMER, OPT_END_SOC_WINTER, OPT_END_SOC_OFFPEAK, OPT_AC_KW)
