"""The `eg4_tou.update_settings` service: change several settings in one step.

The dashboard's timeline card uses it for "Edit > Save", so the charge window start and end,
the charge limits and the AC charge power change together (one re-plan, never a half-applied
window). Admin only, like every other way of changing the settings.
"""
from __future__ import annotations

import re
from typing import Any, cast

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import (
    AC_KW_RANGE, AC_KW_STEP, DOMAIN, OPT_AC_KW, OPT_CHARGE_END, OPT_CHARGE_START,
    OPT_END_SOC_OFFPEAK, OPT_END_SOC_SUMMER, OPT_END_SOC_WINTER, SOC_RANGE,
)

SERVICE_UPDATE_SETTINGS = "update_settings"
ATTR_ENTRY_ID = "entry_id"
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SOC_KEYS = (OPT_END_SOC_SUMMER, OPT_END_SOC_WINTER, OPT_END_SOC_OFFPEAK)


def _hhmm(value: Any) -> str:
    text = str(value)[:5]   # accept "01:00:00" as well
    if not _HHMM.match(text):
        raise vol.Invalid("use a time like 01:00")
    return text


SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTRY_ID): cv.string,
    vol.Optional(OPT_CHARGE_START): _hhmm,
    vol.Optional(OPT_CHARGE_END): _hhmm,
    **{vol.Optional(k): vol.All(vol.Coerce(int), vol.Range(*SOC_RANGE)) for k in _SOC_KEYS},
    vol.Optional(OPT_AC_KW): vol.All(vol.Coerce(float), vol.Range(*AC_KW_RANGE)),
})


def validate(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """The options after applying `patch`, or ServiceValidationError (nothing is changed)."""
    merged = {**current, **patch}
    if merged[OPT_CHARGE_START] >= merged[OPT_CHARGE_END]:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="charge_window_order",
                                     translation_placeholders={"start": merged[OPT_CHARGE_START],
                                                               "end": merged[OPT_CHARGE_END]})
    if OPT_AC_KW in patch:
        # the AC power register is in 0.1 kW steps, the entity in 0.5 kW steps
        merged[OPT_AC_KW] = round(round(merged[OPT_AC_KW] / AC_KW_STEP) * AC_KW_STEP, 1)
    return merged


def _entry(hass: HomeAssistant, entry_id: str | None) -> Any:
    entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)]
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    if len(entries) != 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_found")
    return entries[0]


async def _update_settings(call: ServiceCall) -> None:
    data = dict(call.data)
    entry = _entry(call.hass, data.pop(ATTR_ENTRY_ID, None))
    if not data:
        return
    merged = validate(dict(entry.options), data)
    if merged != dict(entry.options):
        call.hass.config_entries.async_update_entry(entry, options=merged)


def async_register(hass: HomeAssistant) -> None:
    async_register_admin_service(hass, DOMAIN, SERVICE_UPDATE_SETTINGS, _update_settings,
                                 cast(Any, SCHEMA))  # HA 2026 types expect probatio; voluptuous is a shim over it
