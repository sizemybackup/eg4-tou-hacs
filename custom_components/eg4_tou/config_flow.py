"""Setup: gateway connection -> tariff (preset or custom peak windows) -> mode.

The tariff steps are a mixin shared with the options flow, so users can change
their schedule later from the integration's Configure button.
"""
from __future__ import annotations

import calendar
import ipaddress
import re
from collections.abc import Mapping
from datetime import date
from typing import Any, cast

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (CONF_COUNTRY, CONF_CREATE_DASHBOARD, CONF_CUSTOM, CONF_HOST, CONF_PORT, CONF_SUBDIV, CONF_TARIFF, CONF_UNIT,
                    DOMAIN, OPT_DRY_RUN, OPT_ENABLED, OPT_PEAKS)
from .modbus import Eg4Modbus, ModbusConfig, ModbusError
from .scanner import Found, async_scan, is_local_address
from .planner import MAX_PEAKS_PER_DAY, PRESETS, tariff_from_custom, validate_peaks, winter_dates

TARIFF_CHOICES = {
    **{k: t.name for k, t in PRESETS.items()},
    "seasonal": "Custom: separate summer and winter schedules",
    "single": "Custom: one schedule all year",
}
_TIME = selector.TimeSelector()
GUIDE_URL = "https://sizemybackup.com/eg4-12000xp-home-assistant-local-guide.html"


def _hhmm(value: str) -> str:
    return value[:5]


def _peak_schema(start: str, end: str, allow_more: bool) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required("start", default=start): _TIME,
        vol.Required("end", default=end): _TIME,
    }
    if allow_more:
        fields[vol.Required("add_another", default=False)] = bool
    return vol.Schema(fields)


_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


def host_error(host: str) -> str | None:
    """Error key for a gateway address, or None. A host name is allowed; an IP literal must be
    private/loopback/link-local, because Modbus has no authentication and must not face the internet."""
    host = host.strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None if _HOSTNAME.match(host) else "invalid_host"
    return None if is_local_address(host) else "public_address"


def holiday_region(country: str, subdiv: str) -> tuple[str, str, dict[str, str]]:
    """Normalise country/subdivision against the holidays library: (country, subdiv, errors).
    Blocking (the library imports lazily): run it in the executor."""
    import holidays

    country, subdiv = country.strip().upper(), subdiv.strip()
    supported = holidays.list_supported_countries()
    if country not in supported:
        return country, subdiv, {CONF_COUNTRY: "bad_country"}
    if subdiv:
        match = {s.upper(): s for s in supported[country]}.get(subdiv.upper())
        if match is None:
            return country, subdiv, {CONF_SUBDIV: "bad_subdiv"}
        subdiv = match
    return country, subdiv, {}


CONNECTION_KEYS = (CONF_HOST, CONF_PORT, CONF_UNIT, CONF_COUNTRY, CONF_SUBDIV)


async def _clean_connection(hass: HomeAssistant, user_input: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Normalise and check the gateway and holiday-region fields (no network access)."""
    host = str(user_input[CONF_HOST]).strip()
    data: dict[str, Any] = {CONF_HOST: host, CONF_PORT: int(user_input[CONF_PORT]), CONF_UNIT: int(user_input[CONF_UNIT])}
    errors: dict[str, str] = {}
    if err := host_error(host):
        errors[CONF_HOST] = err
    country, subdiv, region_errors = await hass.async_add_executor_job(
        holiday_region, str(user_input.get(CONF_COUNTRY) or ""), str(user_input.get(CONF_SUBDIV) or ""))
    errors.update(region_errors)
    data.update({CONF_COUNTRY: country, CONF_SUBDIV: subdiv})
    return data, errors


def _title(data: Mapping[str, Any]) -> str:
    return f"EG4 TOU ({data[CONF_HOST]})"


def _unique_id(data: Mapping[str, Any]) -> str:
    return f"{data[CONF_HOST]}:{data[CONF_PORT]}:{data[CONF_UNIT]}"


async def _probe(data: Mapping[str, Any]) -> bool:
    """Read-only probe: AC charge start/end SOC."""
    try:
        await Eg4Modbus(ModbusConfig(data[CONF_HOST], data[CONF_PORT], data[CONF_UNIT])).read(160, 2)
    except ModbusError:
        return False
    return True


def _port_unit_fields(d: Mapping[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(CONF_PORT, default=int(d.get(CONF_PORT, 502))): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX)),
        vol.Required(CONF_UNIT, default=int(d.get(CONF_UNIT, 1))): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=247, step=1, mode=selector.NumberSelectorMode.BOX)),
    }


def _show(flow: object, step_id: str, schema: vol.Schema, errors: dict[str, str] | None = None,
          placeholders: Mapping[str, str] | None = None) -> ConfigFlowResult:
    """async_show_form for both flow kinds. The schema is passed untyped: Home Assistant's type hints
    expect voluptuous or, from 2026, probatio (a voluptuous-compatible drop-in), depending on version."""
    handler = cast("config_entries.ConfigFlow | config_entries.OptionsFlow", flow)
    return handler.async_show_form(step_id=step_id, data_schema=cast(Any, schema), errors=errors,
                                   description_placeholders=placeholders)


def _dashboard_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_CREATE_DASHBOARD, default=True): bool})


class _TariffSteps:
    """Mixin for the config and options flows. Subclasses implement _tariff_done()."""

    _kind: str
    _todo: list[str]
    _custom: dict[str, Any]
    _peaks: dict[str, list[tuple[str, str]]]
    _cur_peaks: list[tuple[str, str]]

    async def _tariff_done(self, key: str, custom: dict[str, Any] | None) -> ConfigFlowResult:
        raise NotImplementedError

    async def _begin_tariff(self) -> ConfigFlowResult:
        self._custom, self._peaks, self._cur_peaks, self._todo = {}, {}, [], []
        return await self.async_step_tariff()

    async def async_step_tariff(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            choice = user_input[CONF_TARIFF]
            if choice in PRESETS:
                return await self._tariff_done(choice, None)
            self._kind = choice
            self._custom["offpeak_weekends"] = user_input["offpeak_weekends"]
            self._custom["offpeak_holidays"] = user_input.get("offpeak_holidays", True)
            if choice == "seasonal":
                return await self.async_step_summer_dates()
            return await self.async_step_single_kind()
        schema = vol.Schema({
            vol.Required(CONF_TARIFF, default="bge_rl"): vol.In(TARIFF_CHOICES),
            vol.Required("offpeak_weekends", default=True): bool,
            vol.Required("offpeak_holidays", default=True): bool,
        })
        return _show(self, "tariff", schema)

    async def async_step_summer_dates(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                a = (int(user_input["start_month"]), int(user_input["start_day"]))
                b = (int(user_input["end_month"]), int(user_input["end_day"]))
                date(2024, *a), date(2024, *b)
                w = winter_dates(a, b)
            except ValueError:
                errors["base"] = "bad_date"
            else:
                if w is None:
                    errors["base"] = "season_full_year"
                else:
                    self._custom["seasons"] = {
                        "summer": {"start": list(a), "end": list(b)},
                        "winter": {"start": list(w[0]), "end": list(w[1])},
                    }
                    self._todo = ["summer", "winter"]
                    return await self.async_step_peak()
        month = selector.SelectSelector(selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=str(i), label=calendar.month_name[i]) for i in range(1, 13)],
            mode=selector.SelectSelectorMode.DROPDOWN))
        day = selector.NumberSelector(selector.NumberSelectorConfig(
            min=1, max=31, step=1, mode=selector.NumberSelectorMode.BOX))
        schema = vol.Schema({
            vol.Required("start_month", default="6"): month, vol.Required("start_day", default=1): day,
            vol.Required("end_month", default="9"): month, vol.Required("end_day", default=30): day,
        })
        return _show(self, "summer_dates", schema, errors)

    async def async_step_single_kind(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            name = user_input["kind"]
            self._custom["seasons"] = {name: {"start": [1, 1], "end": [12, 31]}}
            self._todo = [name]
            return await self.async_step_peak()
        schema = vol.Schema({vol.Required("kind", default="winter"): vol.In({
            "summer": "Summer-style (uses the 'summer weekday' charge limit)",
            "winter": "Winter-style (uses the 'winter weekday' charge limit)"})})
        return _show(self, "single_kind", schema)

    async def async_step_peak(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """On-peak window(s) for the season at the front of self._todo."""
        season = self._todo[0]
        errors: dict[str, str] = {}
        n = len(self._cur_peaks) + 1
        if user_input is not None:
            self._cur_peaks.append((_hhmm(user_input["start"]), _hhmm(user_input["end"])))
            err = validate_peaks(self._cur_peaks)
            if err:
                self._cur_peaks.pop()
                errors["base"] = err
            elif user_input.get("add_another") and len(self._cur_peaks) < MAX_PEAKS_PER_DAY:
                return await self.async_step_peak()
            else:
                self._peaks[season] = self._cur_peaks
                self._cur_peaks = []
                self._todo.pop(0)
                if self._todo:
                    return await self.async_step_peak()
                self._custom["peaks"] = {k: [list(w) for w in v] for k, v in self._peaks.items()}
                tariff_from_custom(self._custom)  # final sanity check
                return await self._tariff_done("custom", self._custom)
        default_start, default_end = ("16:00:00", "20:00:00") if n == 1 else ("06:00:00", "09:00:00")
        return _show(self, "peak", _peak_schema(default_start, default_end, n < MAX_PEAKS_PER_DAY), errors,
                     {"season": season, "n": str(n), "max": str(MAX_PEAKS_PER_DAY)})


class Eg4TouConfigFlow(_TariffSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _data: dict[str, Any]
    _found: dict[str, Found]
    _read_only: bool

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> Eg4TouOptionsFlow:
        return Eg4TouOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One screen: auto-detected gateways pre-filled, everything adjustable.

        The network is scanned (read-only) when the screen first opens, or when
        the user ticks "scan again". The gateway field is a combo box: pick a
        discovered gateway or type any address.
        """
        errors: dict[str, str] = {}
        rescan = bool(user_input and user_input.get("rescan"))
        if user_input is not None and not rescan:
            # raise_on_progress=False: an abandoned earlier attempt must not block a fresh one
            data, errors = await _clean_connection(self.hass, user_input)
            if not errors:
                await self.async_set_unique_id(_unique_id(data), raise_on_progress=False)
                self._abort_if_unique_id_configured()
                if await _probe(data):
                    self._data = data
                    return await self._begin_tariff()
                errors["base"] = "cannot_connect"

        if user_input is None or rescan:
            self._found = {f.host: f for f in await async_scan(self.hass)}
        d = user_input or {}
        found = self._found
        def _what(f: Found) -> str:
            return "looks like an EG4 inverter" if f.looks_like_eg4 else "port 502 open, not confirmed as EG4"

        host_field: selector.SelectSelector | type[str]
        if found:
            host_field = selector.SelectSelector(selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=h, label=f"{h}  (detected by scan: {_what(f)})")
                         for h, f in found.items()],
                custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN))
            note = ("**Detected by the network scan:**\n"
                    + "\n".join(f"- `{h}`: {_what(f)}" for h, f in found.items())
                    + "\n\nThe Gateway field below is **pre-filled from this scan**. Change it if it is not "
                      "your gateway, or type your own address.")
        else:
            host_field = str
            note = ("**The scan did not detect a gateway.** Type its address below, and check that it is powered, "
                    "on this network and set to Modbus port 502.")
        schema = vol.Schema({
            vol.Required(CONF_HOST, default=d.get(CONF_HOST) or next(iter(found), "")): host_field,
            **_port_unit_fields(d),
            vol.Required(CONF_COUNTRY, default=d.get(CONF_COUNTRY) or self.hass.config.country or "US"): str,
            vol.Optional(CONF_SUBDIV, default=d.get(CONF_SUBDIV, "")): str,
            vol.Optional("rescan", default=False): bool,
        })
        return _show(self, "user", schema, errors, {"scan_result": note, "guide_url": GUIDE_URL})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the gateway address/port/unit or the holiday region without re-adding the integration.
        Tariff, mode and charge settings are kept."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await _clean_connection(self.hass, user_input)
            if not errors:
                uid = _unique_id(data)
                if any(e.unique_id == uid for e in self._async_current_entries() if e.entry_id != entry.entry_id):
                    errors["base"] = "already_configured"
                elif not await _probe(data):
                    errors["base"] = "cannot_connect"
                else:
                    # keep a title the user renamed; refresh the default one that shows the address
                    if entry.title == _title(entry.data):
                        return self.async_update_reload_and_abort(
                            entry, unique_id=uid, data_updates=data, title=_title(data))
                    return self.async_update_reload_and_abort(entry, unique_id=uid, data_updates=data)
        d = {**entry.data, **(user_input or {})}
        schema = vol.Schema({
            vol.Required(CONF_HOST, default=d[CONF_HOST]): str,
            **_port_unit_fields(d),
            vol.Required(CONF_COUNTRY, default=d.get(CONF_COUNTRY) or "US"): str,
            vol.Optional(CONF_SUBDIV, default=d.get(CONF_SUBDIV) or ""): str,
        })
        return _show(self, "reconfigure", schema, errors)

    async def _tariff_done(self, key: str, custom: dict[str, Any] | None) -> ConfigFlowResult:
        self._data[CONF_TARIFF] = key
        if custom:
            self._data[CONF_CUSTOM] = custom
        return await self.async_step_mode()

    async def async_step_mode(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input["mode"] == "read_write":
                return await self.async_step_confirm()
            self._read_only = True
            return await self.async_step_dashboard()
        schema = vol.Schema({vol.Required("mode", default="read_only"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="read_only",
                                              label="Read-only (recommended): show the plan, never write"),
                    selector.SelectOptionDict(value="read_write", label="Read-write: apply the plan to the inverter"),
                ],
                mode=selector.SelectSelectorMode.LIST))})
        return _show(self, "mode", schema)

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input["understood"]:
                self._read_only = False
                return await self.async_step_dashboard()
            errors["understood"] = "must_confirm"
        schema = vol.Schema({vol.Required("understood", default=False): bool})
        return _show(self, "confirm", schema, errors)

    async def async_step_dashboard(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Last step: offer the ready-made dashboard (created when the entry loads)."""
        if user_input is None:
            return _show(self, "dashboard", _dashboard_schema())
        self._data[CONF_CREATE_DASHBOARD] = bool(user_input[CONF_CREATE_DASHBOARD])
        return self.async_create_entry(
            title=_title(self._data), data=self._data,
            options={OPT_ENABLED: True, OPT_DRY_RUN: self._read_only})


class Eg4TouOptionsFlow(_TariffSteps, config_entries.OptionsFlow):
    """Configure button: re-run the tariff steps and reload."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._begin_tariff()

    async def _tariff_done(self, key: str, custom: dict[str, Any] | None) -> ConfigFlowResult:
        data = {k: v for k, v in self.config_entry.data.items() if k != CONF_CUSTOM}
        data[CONF_TARIFF] = key
        if custom:
            data[CONF_CUSTOM] = custom
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.async_create_entry(data={**self.config_entry.options, OPT_PEAKS: {}})  # a new tariff drops card-made peak edits
