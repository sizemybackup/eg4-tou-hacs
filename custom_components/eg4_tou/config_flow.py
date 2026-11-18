"""Setup form: where the gateway is, which tariff, which country for holidays.
Everything else (SOC limits, charge window, power) lives in entities."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import (CONF_COUNTRY, CONF_HOST, CONF_PORT, CONF_SUBDIV, CONF_TARIFF, CONF_UNIT, DOMAIN)
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
                return self.async_create_entry(title=f"EG4 TOU ({user_input[CONF_HOST]})", data=user_input)

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=502): int,
            vol.Required(CONF_UNIT, default=1): int,
            vol.Required(CONF_TARIFF, default="bge_rl"): vol.In({k: t.name for k, t in PRESETS.items()}),
            vol.Required(CONF_COUNTRY, default=self.hass.config.country or "US"): str,
            vol.Optional(CONF_SUBDIV, default=""): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
