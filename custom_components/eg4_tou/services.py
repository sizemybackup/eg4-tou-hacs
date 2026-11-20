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

from .planner import validate_peaks

from .const import (
    AC_KW_RANGE, AC_KW_STEP, DOMAIN, OPT_AC_KW, OPT_CHARGE_END, OPT_CHARGE_START,
    OPT_END_SOC_OFFPEAK, OPT_END_SOC_SUMMER, OPT_END_SOC_WINTER, OPT_PEAKS, SOC_RANGE,
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
    # {season: [[start, end], ...]} sets that season's on-peak windows; {season: null} goes back to the tariff's own
    vol.Optional("peaks"): {cv.string: vol.Any(None, [vol.All([_hhmm], vol.Length(2, 2))])},
})


def merge_peaks(current: Any, patch: dict[str, Any], seasons: dict[str, Any]) -> dict[str, Any]:
    """The stored peak overrides after applying `patch`. `seasons` maps each season name to the
    tariff's own windows; a season set back to them (or to null) stops being an override."""
    out = {k: [list(w) for w in v] for k, v in (current or {}).items()}
    for season, windows in patch.items():
        if season not in seasons:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_season",
                                         translation_placeholders={"season": season})
        if windows is None:
            out.pop(season, None)
            continue
        wl = sorted((str(a), str(b)) for a, b in windows)
        err = validate_peaks(wl)
        if err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key=err)
        if wl == sorted(tuple(w) for w in seasons[season]):
            out.pop(season, None)
        else:
            out[season] = [list(w) for w in wl]
    return out


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
    peaks = data.pop("peaks", None)
    merged = validate(dict(entry.options), data)
    if peaks is not None:
        c = entry.runtime_data
        merged[OPT_PEAKS] = merge_peaks(entry.options.get(OPT_PEAKS), peaks,
                                        {s.name: c.base_tariff.peaks.get(s.name, ()) for s in c.base_tariff.seasons})
    if merged != dict(entry.options):
        call.hass.config_entries.async_update_entry(entry, options=merged)


def async_register(hass: HomeAssistant) -> None:
    async_register_admin_service(hass, DOMAIN, SERVICE_UPDATE_SETTINGS, _update_settings,
                                 cast(Any, SCHEMA))  # HA 2026 types expect probatio; voluptuous is a shim over it
