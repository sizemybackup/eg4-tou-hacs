"""The tariff library: named tariffs the user can create, switch between and delete.

Stored in the config entry's options as {name: spec}, where a spec is
  {"base": "bge_rl"}                      a ready-made preset
  {"custom": {...}}                       a tariff entered in the Configure wizard
plus optionally "peaks": {season: [[start, end], ...]} (the user's own on-peak hours for a season, layered
over the base: holidays and daylight-saving rules of a preset still apply) and "builtin": true (the shipped
example, which cannot be changed or deleted). One tariff is active.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from homeassistant.exceptions import ServiceValidationError

from .const import CONF_CUSTOM, CONF_TARIFF, DOMAIN, OPT_ACTIVE, OPT_TARIFFS
from .planner import PRESETS, Tariff, tariff_from_custom, tariff_with_overrides, validate_peaks

EXAMPLE_KEY = "bge_rl"
EXAMPLE_NAME = "BGE Schedule RL (example)"
DEFAULT_CUSTOM_NAME = "My tariff"
MAX_NAME = 40


def initial_library(data: Mapping[str, Any]) -> dict[str, Any]:
    """Options for a new or upgraded entry, from how it was set up: the example, plus the user's
    custom tariff (named "My tariff") when they entered one."""
    lib: dict[str, Any] = {EXAMPLE_NAME: {"base": EXAMPLE_KEY, "builtin": True}}
    active = EXAMPLE_NAME
    if data.get(CONF_TARIFF) == "custom" and data.get(CONF_CUSTOM):
        lib[DEFAULT_CUSTOM_NAME] = {"custom": copy.deepcopy(data[CONF_CUSTOM])}
        active = DEFAULT_CUSTOM_NAME
    return {OPT_TARIFFS: lib, OPT_ACTIVE: active}


def base_of(spec: Mapping[str, Any]) -> Tariff:
    return tariff_from_custom(spec["custom"]) if spec.get("custom") else PRESETS[spec.get("base", EXAMPLE_KEY)]


def effective_of(spec: Mapping[str, Any]) -> Tariff:
    return tariff_with_overrides(base_of(spec), spec.get("peaks") or {})


def active_of(options: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """(name, spec) of the active tariff; falls back to the first one if the stored name is gone."""
    lib = options.get(OPT_TARIFFS) or initial_library({})[OPT_TARIFFS]
    name = options.get(OPT_ACTIVE)
    if name not in lib:
        name = next(iter(lib))
    return str(name), dict(lib[name])


def _err(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders)


def _library(options: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(options.get(OPT_TARIFFS) or initial_library({})[OPT_TARIFFS]))


def activate(options: Mapping[str, Any], name: str) -> dict[str, Any]:
    if name not in _library(options):
        raise _err("tariff_not_found", name=name)
    return {**options, OPT_TARIFFS: _library(options), OPT_ACTIVE: name}


def clean_name(name: str, lib: Mapping[str, Any]) -> str:
    name = " ".join(str(name).split())
    if not name or len(name) > MAX_NAME:
        raise _err("tariff_name_invalid", max=str(MAX_NAME))
    if name in lib:
        raise _err("tariff_name_taken", name=name)
    return name


def merge_peaks(current: Any, patch: Mapping[str, Any], base: Tariff) -> dict[str, Any]:
    """A spec's peak overrides after applying `patch` ({season: windows | None}). A season set back to the
    base tariff's own windows (or to None) stops being an override."""
    out = {k: [list(w) for w in v] for k, v in (current or {}).items()}
    known = {s.name for s in base.seasons}
    for season, windows in patch.items():
        if season not in known:
            raise _err("unknown_season", season=season)
        if windows is None:
            out.pop(season, None)
            continue
        wl = sorted((str(a), str(b)) for a, b in windows)
        err = validate_peaks(wl)
        if err:
            raise _err(err)
        if wl == sorted(base.peaks.get(season, ())):
            out.pop(season, None)
        else:
            out[season] = [list(w) for w in wl]
    return out


def edit_peaks(options: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a peak edit to the active tariff (the built-in example is read-only: save it as a new one)."""
    name, spec = active_of(options)
    if spec.get("builtin"):
        raise _err("tariff_builtin", name=name)
    lib = _library(options)
    lib[name]["peaks"] = merge_peaks(spec.get("peaks"), patch, base_of(spec))
    return {**options, OPT_TARIFFS: lib, OPT_ACTIVE: name}


def save_as(options: Mapping[str, Any], name: str, peaks: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A copy of the active tariff (with `peaks` applied) under a new name, which becomes the active one."""
    lib = _library(options)
    name = clean_name(name, lib)
    _, spec = active_of(options)
    new = copy.deepcopy({k: v for k, v in spec.items() if k != "builtin"})
    if peaks:
        new["peaks"] = merge_peaks(new.get("peaks"), peaks, base_of(new))
    lib[name] = new
    return {**options, OPT_TARIFFS: lib, OPT_ACTIVE: name}


def add_custom(options: Mapping[str, Any], name: str, custom: dict[str, Any]) -> dict[str, Any]:
    """A tariff from the Configure wizard. An existing custom tariff of that name is replaced."""
    lib = _library(options)
    name = " ".join(str(name).split())
    if not name or len(name) > MAX_NAME:
        raise _err("tariff_name_invalid", max=str(MAX_NAME))
    if lib.get(name, {}).get("builtin"):
        raise _err("tariff_builtin", name=name)
    lib[name] = {"custom": copy.deepcopy(custom)}
    return {**options, OPT_TARIFFS: lib, OPT_ACTIVE: name}


def delete(options: Mapping[str, Any], name: str) -> dict[str, Any]:
    lib = _library(options)
    if name not in lib:
        raise _err("tariff_not_found", name=name)
    if lib[name].get("builtin"):
        raise _err("tariff_builtin", name=name)
    del lib[name]
    active = options.get(OPT_ACTIVE)
    if active == name or active not in lib:
        active = EXAMPLE_NAME if EXAMPLE_NAME in lib else next(iter(lib))
    return {**options, OPT_TARIFFS: lib, OPT_ACTIVE: active}
