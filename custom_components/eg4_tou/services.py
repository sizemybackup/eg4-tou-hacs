"""The `eg4_tou.update_settings` service: change several settings in one step.

The dashboard's timeline card uses it for "Edit > Save", so the charge window start and end,
the charge limits and the AC charge power change together (one re-plan, never a half-applied
window). Admin only, like every other way of changing the settings.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Coroutine
from typing import Any, cast

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from . import tariffs
from .const import (
    AC_KW_STEP, DOMAIN, OPT_AC_KW, OPT_CHARGE_END, OPT_CHARGE_START,
    OPT_END_SOC_OFFPEAK, OPT_END_SOC_SUMMER, OPT_END_SOC_WINTER, OPT_HOLD_CUSTOM, OPT_KW_MAX, OPT_POLL_MINUTES,
    OPT_SOC_GAP, OPT_SOC_MAX, OPT_SOC_MIN, OPT_WRITE_GROUPS, limits, write_groups,
)
from .drivers import GROUPS
from .planner import plan_intent, tariff_from_custom, validate_hold

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
    # wide bounds here; the exact limits are the soc_min / soc_max / ac_kw_max options (see validate)
    **{vol.Optional(k): vol.All(vol.Coerce(int), vol.Range(0, 100)) for k in _SOC_KEYS},
    vol.Optional(OPT_AC_KW): vol.All(vol.Coerce(float), vol.Range(0.1, 20)),
    vol.Optional(OPT_SOC_GAP): vol.All(vol.Coerce(int), vol.Range(0, 50)),
    vol.Optional(OPT_SOC_MIN): vol.All(vol.Coerce(int), vol.Range(0, 99)),
    vol.Optional(OPT_SOC_MAX): vol.All(vol.Coerce(int), vol.Range(1, 100)),
    vol.Optional(OPT_KW_MAX): vol.All(vol.Coerce(float), vol.Range(1, 20)),
    vol.Optional(OPT_POLL_MINUTES): vol.All(vol.Coerce(int), vol.Range(1, 120)),
    # {group: bool}: which kinds of setting this integration may write (charge_window, hold_windows, soc, power)
    vol.Optional("write_groups"): {vol.In(list(GROUPS)): cv.boolean},
    # {profile: [[start, end], ...]} replaces the derived hold windows; {profile: null} goes back to deriving them
    vol.Optional("hold"): {cv.string: vol.Any(None, [vol.All([_hhmm], vol.Length(2, 2))])},
    # {season: [[start, end], ...]} sets that season's on-peak windows; {season: null} goes back to the tariff's own
    vol.Optional("peaks"): {cv.string: vol.Any(None, [vol.All([_hhmm], vol.Length(2, 2))])},
})


def validate(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """The options after applying `patch`, or ServiceValidationError (nothing is changed)."""
    merged = {**current, **patch}
    if merged[OPT_CHARGE_START] >= merged[OPT_CHARGE_END]:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="charge_window_order",
                                     translation_placeholders={"start": merged[OPT_CHARGE_START],
                                                               "end": merged[OPT_CHARGE_END]})
    (lo, hi), (kw_lo, kw_hi) = limits(merged)
    if lo >= hi:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="limits_order")
    for key in _SOC_KEYS:
        if not lo <= int(merged[key]) <= hi:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="soc_out_of_range",
                                         translation_placeholders={"value": str(merged[key]), "lo": str(lo), "hi": str(hi)})
    if not kw_lo <= float(merged[OPT_AC_KW]) <= kw_hi:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="power_out_of_range",
                                     translation_placeholders={"value": str(merged[OPT_AC_KW]), "lo": str(kw_lo), "hi": str(kw_hi)})
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
    groups, hold = data.pop("write_groups", None), data.pop("hold", None)
    if groups:
        data[OPT_WRITE_GROUPS] = {**write_groups(entry.options), **groups}
    if hold:
        custom = dict(entry.options.get(OPT_HOLD_CUSTOM) or {})
        for profile, windows in hold.items():
            if windows is None:
                custom.pop(profile, None)
                continue
            if (err := validate_hold(windows)) is not None:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key=err)
            custom[profile] = [list(w) for w in windows]
        data[OPT_HOLD_CUSTOM] = custom
    merged = validate(dict(entry.options), data)
    if peaks is not None:
        merged = tariffs.edit_peaks(merged, peaks)    # the active tariff's own on-peak hours
    if merged != dict(entry.options):
        call.hass.config_entries.async_update_entry(entry, options=merged)


def _tariff_service(change: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]) -> Callable[[ServiceCall], Coroutine[Any, Any, None]]:
    async def handler(call: ServiceCall) -> None:
        data = dict(call.data)
        entry = _entry(call.hass, data.pop(ATTR_ENTRY_ID, None))
        new = change(dict(entry.options), data)
        if new != dict(entry.options):
            call.hass.config_entries.async_update_entry(entry, options=new)
    return handler


def _entry_field() -> dict[Any, Any]:
    return {vol.Optional(ATTR_ENTRY_ID): cv.string}


SET_TARIFF_SCHEMA = vol.Schema({**_entry_field(), vol.Required("name"): cv.string})
SAVE_AS_SCHEMA = vol.Schema({**_entry_field(), vol.Required("name"): cv.string,
                             vol.Optional("peaks"): {cv.string: vol.Any(None, [vol.All([_hhmm], vol.Length(2, 2))])}})


IMPORT_SCHEMA = vol.Schema({**_entry_field(), vol.Required("name"): cv.string, vol.Required("tariff"): dict})


def _import_tariff(options: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    try:
        tariff_from_custom(data["tariff"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="tariff_invalid",
                                     translation_placeholders={"error": str(exc)}) from exc
    return tariffs.add_custom(options, data["name"], data["tariff"])


async def _get_plan(call: ServiceCall) -> dict[str, Any]:
    """Today's plan as plain settings, for automations that apply it themselves."""
    entry = _entry(call.hass, call.data.get(ATTR_ENTRY_ID))
    c, d = entry.runtime_data, entry.runtime_data.data
    return {**plan_intent(d.plan), "mode": d.mode, "tariff": c.tariff_name,
            "managed": sorted(d.managed), "pending": [{"slot": r, "wanted": w, "now": n} for r, w, n in d.pending]}


def async_register(hass: HomeAssistant) -> None:
    # HA 2026 types expect probatio; voluptuous is a shim over it, so the schemas are passed untyped
    async_register_admin_service(hass, DOMAIN, SERVICE_UPDATE_SETTINGS, _update_settings, cast(Any, SCHEMA))
    async_register_admin_service(hass, DOMAIN, "set_tariff", _tariff_service(lambda o, d: tariffs.activate(o, d["name"])),
                                 cast(Any, SET_TARIFF_SCHEMA))
    async_register_admin_service(hass, DOMAIN, "save_tariff_as",
                                 _tariff_service(lambda o, d: tariffs.save_as(o, d["name"], d.get("peaks"))), cast(Any, SAVE_AS_SCHEMA))
    async_register_admin_service(hass, DOMAIN, "import_tariff", _tariff_service(_import_tariff), cast(Any, IMPORT_SCHEMA))
    hass.services.async_register(DOMAIN, "get_plan", _get_plan, schema=cast(Any, vol.Schema(_entry_field())),
                                 supports_response=SupportsResponse.ONLY)
    async_register_admin_service(hass, DOMAIN, "delete_tariff", _tariff_service(lambda o, d: tariffs.delete(o, d["name"])),
                                 cast(Any, SET_TARIFF_SCHEMA))
