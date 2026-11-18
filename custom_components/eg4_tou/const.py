DOMAIN = "eg4_tou"
PLATFORMS = ["sensor", "switch", "number", "time"]
SCAN_INTERVAL_MIN = 10

CONF_HOST = "host"
CONF_PORT = "port"
CONF_UNIT = "unit"
CONF_TARIFF = "tariff"
CONF_COUNTRY = "country"
CONF_SUBDIV = "subdiv"

# Mutable settings, stored in entry.options
OPT_ENABLED = "enabled"
OPT_DRY_RUN = "dry_run"
OPT_CHARGE_START = "charge_start"
OPT_CHARGE_END = "charge_end"
OPT_END_SOC_SUMMER = "end_soc_summer"
OPT_END_SOC_WINTER = "end_soc_winter"
OPT_END_SOC_OFFPEAK = "end_soc_offpeak"
OPT_AC_KW = "ac_charge_kw"

DEFAULT_OPTIONS = {
    OPT_ENABLED: False,   # never write until the user turns it on
    OPT_DRY_RUN: True,    # and even then, plan-only until they leave dry run
    OPT_CHARGE_START: "01:00",
    OPT_CHARGE_END: "05:00",
    OPT_END_SOC_SUMMER: 80,
    OPT_END_SOC_WINTER: 70,
    OPT_END_SOC_OFFPEAK: 50,
    OPT_AC_KW: 5.0,
}
