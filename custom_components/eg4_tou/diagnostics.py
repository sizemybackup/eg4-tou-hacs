"""Download diagnostics (device page, three dots) for bug reports: settings, the plan and what the
inverter currently holds. The gateway address is redacted."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_HOST

TO_REDACT = {CONF_HOST}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    c = entry.runtime_data
    d = c.data
    out: dict[str, Any] = {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_update_success": c.last_update_success,
        "last_exception": repr(c.last_exception) if c.last_exception else None,
    }
    if d is None:
        return out
    out.update({
        "mode": d.mode,
        "last_write": d.last_write,
        "profile": d.plan.profile,
        "peak_windows": d.peaks,
        "facts": d.facts,
        "registers": {
            str(reg): {"planned": c.describe(reg, want), "inverter": c.describe(reg, d.current.get(reg))}
            for reg, want in d.plan.registers.items()
        },
        "pending": [{"reg": r, "want": c.describe(r, want), "was": c.describe(r, was)} for r, want, was in d.pending],
        "week": d.week,
    })
    return out
