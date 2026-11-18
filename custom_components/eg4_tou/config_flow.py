"""Setup: gateway connection -> tariff (preset or custom peak windows) -> mode.

The tariff steps are a mixin shared with the options flow, so users can change
their schedule later from the integration's Configure button.
"""
from __future__ import annotations

import calendar
from datetime import date

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (CONF_COUNTRY, CONF_CUSTOM, CONF_HOST, CONF_PORT, CONF_SUBDIV, CONF_TARIFF, CONF_UNIT,
                    DOMAIN, OPT_DRY_RUN, OPT_ENABLED)
from .modbus import Eg4Modbus, ModbusConfig, ModbusError
from .scanner import async_scan
from .planner import MAX_PEAKS_PER_DAY, PRESETS, tariff_from_custom, validate_peaks, winter_dates

TARIFF_CHOICES = {
    **{k: t.name for k, t in PRESETS.items()},
    "seasonal": "Custom: separate summer and winter schedules",
    "single": "Custom: one schedule all year",
}
_TIME = selector.TimeSelector()


def _hhmm(value: str) -> str:
    return value[:5]


def _peak_schema(start: str, end: str, allow_more: bool) -> vol.Schema:
    fields = {
        vol.Required("start", default=start): _TIME,
        vol.Required("end", default=end): _TIME,
    }
    if allow_more:
        fields[vol.Required("add_another", default=False)] = bool
    return vol.Schema(fields)


class _TariffSteps:
    """Mixin. Subclasses implement _tariff_done(tariff_key, custom_or_None)."""

    _kind: str
    _todo: list[str]
    _custom: dict
    _peaks: dict[str, list[tuple[str, str]]]
    _cur_peaks: list[tuple[str, str]]

    def _begin_tariff(self):
        self._custom, self._peaks, self._cur_peaks, self._todo = {}, {}, [], []
        return self.async_step_tariff()

    async def async_step_tariff(self, user_input=None):
        if user_input is not None:
            choice = user_input[CONF_TARIFF]
            if choice in PRESETS:
                return await self._tariff_done(choice, None)
            self._kind = choice
            self._custom["offpeak_weekends"] = user_input["offpeak_weekends"]
            if choice == "seasonal":
                return await self.async_step_summer_dates()
            return await self.async_step_single_kind()
        schema = vol.Schema({
            vol.Required(CONF_TARIFF, default="bge_rl"): vol.In(TARIFF_CHOICES),
            vol.Required("offpeak_weekends", default=True): bool,
        })
        return self.async_show_form(step_id="tariff", data_schema=schema)

    async def async_step_summer_dates(self, user_input=None):
        errors = {}
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
            options=[{"value": str(i), "label": calendar.month_name[i]} for i in range(1, 13)],
            mode=selector.SelectSelectorMode.DROPDOWN))
        day = selector.NumberSelector(selector.NumberSelectorConfig(
            min=1, max=31, step=1, mode=selector.NumberSelectorMode.BOX))
        schema = vol.Schema({
            vol.Required("start_month", default="6"): month, vol.Required("start_day", default=1): day,
            vol.Required("end_month", default="9"): month, vol.Required("end_day", default=30): day,
        })
        return self.async_show_form(step_id="summer_dates", data_schema=schema, errors=errors)

    async def async_step_single_kind(self, user_input=None):
        if user_input is not None:
            name = user_input["kind"]
            self._custom["seasons"] = {name: {"start": [1, 1], "end": [12, 31]}}
            self._todo = [name]
            return await self.async_step_peak()
        schema = vol.Schema({vol.Required("kind", default="winter"): vol.In({
            "summer": "Summer-style (uses the 'summer weekday' charge limit)",
            "winter": "Winter-style (uses the 'winter weekday' charge limit)"})})
        return self.async_show_form(step_id="single_kind", data_schema=schema)

    async def async_step_peak(self, user_input=None):
        """On-peak window(s) for the season at the front of self._todo."""
        season = self._todo[0]
        errors = {}
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
        return self.async_show_form(
            step_id="peak", data_schema=_peak_schema(default_start, default_end, n < MAX_PEAKS_PER_DAY),
            description_placeholders={"season": season, "n": str(n), "max": str(MAX_PEAKS_PER_DAY)},
            errors=errors)


class Eg4TouConfigFlow(_TariffSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return Eg4TouOptionsFlow()

    async def async_step_user(self, user_input=None):
        """One screen: auto-detected gateways pre-filled, everything adjustable.

        The network is scanned (read-only) when the screen first opens, or when
        the user ticks "scan again". The gateway field is a combo box: pick a
        discovered gateway or type any address.
        """
        errors: dict[str, str] = {}
        rescan = bool(user_input and user_input.get("rescan"))
        if user_input is not None and not rescan:
            # raise_on_progress=False: an abandoned earlier attempt must not block a fresh one
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}:{user_input[CONF_UNIT]}",
                                           raise_on_progress=False)
            self._abort_if_unique_id_configured()
            client = Eg4Modbus(ModbusConfig(user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_UNIT]))
            try:
                await client.read(160, 2)  # read-only probe: AC charge start/end SOC
            except ModbusError:
                errors["base"] = "cannot_connect"
            else:
                self._data = {k: v for k, v in user_input.items() if k != "rescan"}
                return await self._begin_tariff()

        if user_input is None or rescan:
            self._found = {f.host: f for f in await async_scan(self.hass)}
        d = user_input or {}
        found = self._found
        def _what(f):
            return "looks like an EG4 inverter" if f.looks_like_eg4 else "port 502 open, not confirmed as EG4"

        if found:
            host_field = selector.SelectSelector(selector.SelectSelectorConfig(
                options=[{"value": h, "label": f"{h}  (detected by scan: {_what(f)})"} for h, f in found.items()],
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
            vol.Required(CONF_PORT, default=d.get(CONF_PORT, 502)): int,
            vol.Required(CONF_UNIT, default=d.get(CONF_UNIT, 1)): int,
            vol.Required(CONF_COUNTRY, default=d.get(CONF_COUNTRY) or self.hass.config.country or "US"): str,
            vol.Optional(CONF_SUBDIV, default=d.get(CONF_SUBDIV, "")): str,
            vol.Optional("rescan", default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors,
                                    description_placeholders={"scan_result": note})

    async def _tariff_done(self, key, custom):
        self._data[CONF_TARIFF] = key
        if custom:
            self._data[CONF_CUSTOM] = custom
        return await self.async_step_mode()

    async def async_step_mode(self, user_input=None):
        if user_input is not None:
            if user_input["mode"] == "read_write":
                return await self.async_step_confirm()
            return self._finish(read_only=True)
        schema = vol.Schema({vol.Required("mode", default="read_only"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {"value": "read_only", "label": "Read-only (recommended): show the plan, never write"},
                    {"value": "read_write", "label": "Read-write: apply the plan to the inverter"},
                ],
                mode=selector.SelectSelectorMode.LIST))})
        return self.async_show_form(step_id="mode", data_schema=schema)

    async def async_step_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input["understood"]:
                return self._finish(read_only=False)
            errors["understood"] = "must_confirm"
        schema = vol.Schema({vol.Required("understood", default=False): bool})
        return self.async_show_form(step_id="confirm", data_schema=schema, errors=errors)

    def _finish(self, read_only: bool):
        return self.async_create_entry(
            title=f"EG4 TOU ({self._data[CONF_HOST]})", data=self._data,
            options={OPT_ENABLED: True, OPT_DRY_RUN: read_only})


class Eg4TouOptionsFlow(_TariffSteps, config_entries.OptionsFlow):
    """Configure button: re-run the tariff steps and reload."""

    async def async_step_init(self, user_input=None):
        return await self._begin_tariff()

    async def _tariff_done(self, key, custom):
        data = {k: v for k, v in self.config_entry.data.items() if k != CONF_CUSTOM}
        data[CONF_TARIFF] = key
        if custom:
            data[CONF_CUSTOM] = custom
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.async_create_entry(data=dict(self.config_entry.options))
