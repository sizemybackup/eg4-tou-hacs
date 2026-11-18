"""Setup form: where the gateway is, which tariff, which country for holidays.
Everything else (SOC limits, charge window, power) lives in entities."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import (CONF_COUNTRY, CONF_HOST, CONF_PORT, CONF_SUBDIV, CONF_TARIFF, CONF_UNIT, DOMAIN,
                    OPT_DRY_RUN, OPT_ENABLED)
from .modbus import Eg4Modbus, ModbusConfig, ModbusError
from .planner import PRESETS


class Eg4TouConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}:{user_input[CONF_UNIT]}")
            self._abort_if_unique_id_configured()
            client = Eg4Modbus(ModbusConfig(user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_UNIT]))
            try:
                await client.read(160, 2)  # read-only probe: AC charge start/end SOC
            except ModbusError:
                errors["base"] = "cannot_connect"
            else:
                self._data = user_input
                return await self.async_step_mode()

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=502): int,
            vol.Required(CONF_UNIT, default=1): int,
            vol.Required(CONF_TARIFF, default="bge_rl"): vol.In({k: t.name for k, t in PRESETS.items()}),
            vol.Required(CONF_COUNTRY, default=self.hass.config.country or "US"): str,
            vol.Optional(CONF_SUBDIV, default=""): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_mode(self, user_input=None):
        if user_input is not None:
            if user_input["mode"] == "read_write":
                return await self.async_step_confirm()
            return self._finish(read_only=True)
        schema = vol.Schema({vol.Required("mode", default="read_only"): vol.In(["read_only", "read_write"])})
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
