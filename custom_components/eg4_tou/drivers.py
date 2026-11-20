"""How the plan reaches the inverter.

The planner works in *slots*: small integers that name one setting each (the numbers happen to be the
EG4 12000XP's Modbus registers, which is what a slot stores there). A driver reads and writes slots:

* ModbusDriver talks to the inverter through an RS485 gateway (the default).
* EntityDriver reads and writes Home Assistant entities the user already has (another EG4
  integration, an ESPHome inverter, input helpers, ...), one entity per slot. Slots the user did not
  map are simply not managed.

Every write goes through modbus.check_write, so a driver can only ever write values the integration
has declared safe, whichever backend carries them.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from homeassistant.core import HomeAssistant

from .modbus import Eg4Modbus, ModbusError, WriteRefused, check_write, pack_time, unpack_time

POWER = 66
CHARGE_SLOTS = tuple(range(68, 74))    # 3 windows: start/end pairs
HOLD_SLOTS = tuple(range(152, 158))    # 3 windows: start/end pairs
SOC_START, SOC_END = 160, 161
TIME_SLOTS = frozenset(CHARGE_SLOTS + HOLD_SLOTS)

# Settings the planner manages, in groups the user can switch on and off one by one.
GROUPS: dict[str, tuple[int, ...]] = {
    "charge_window": CHARGE_SLOTS,
    "hold_windows": HOLD_SLOTS,
    "soc": (SOC_START, SOC_END),
    "power": (POWER,),
}

# Names for the slots, used by the entity pickers and the auto-detection.
ROLES: dict[str, int] = {
    "charge_1_start": 68, "charge_1_end": 69, "charge_2_start": 70, "charge_2_end": 71,
    "charge_3_start": 72, "charge_3_end": 73,
    "hold_1_start": 152, "hold_1_end": 153, "hold_2_start": 154, "hold_2_end": 155,
    "hold_3_start": 156, "hold_3_end": 157,
    "soc_start": SOC_START, "soc_end": SOC_END, "power": POWER,
}
TIME_DOMAINS = ("time", "input_datetime")
NUMBER_DOMAINS = ("number", "input_number")

READ_BLOCKS = ((66, 8), (152, 6), (160, 2))  # 66..73, 152..157, 160..161


class DriverError(Exception):
    """The backend could not read or write (safe to retry next cycle)."""


class Driver(Protocol):
    slots: frozenset[int]

    async def read(self) -> dict[int, int]: ...
    async def write(self, slot: int, value: int) -> None: ...


class ModbusDriver:
    slots = frozenset(ROLES.values())

    def __init__(self, client: Eg4Modbus) -> None:
        self.client = client

    async def read(self) -> dict[int, int]:
        out: dict[int, int] = {}
        try:
            for start, count in READ_BLOCKS:
                for i, v in enumerate(await self.client.read(start, count)):
                    out[start + i] = v
        except ModbusError as exc:
            raise DriverError(str(exc)) from exc
        return out

    async def write(self, slot: int, value: int) -> None:
        try:
            await self.client.write(slot, value)
        except (ModbusError, WriteRefused) as exc:
            raise DriverError(str(exc)) from exc


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


class EntityDriver:
    """One Home Assistant entity per slot. `mapping` is {role: entity_id}; unknown roles are ignored."""

    def __init__(self, hass: HomeAssistant, mapping: Mapping[str, str]) -> None:
        self.hass = hass
        self.by_slot = {ROLES[r]: e for r, e in mapping.items() if r in ROLES and e}
        self.slots = frozenset(self.by_slot)

    async def read(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for slot, entity_id in self.by_slot.items():
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown", ""):
                raise DriverError(f"{entity_id} is not available")
            try:
                out[slot] = self._decode(slot, state.state, state.attributes.get("unit_of_measurement"))
            except ValueError as exc:
                raise DriverError(f"{entity_id} has an unusable value {state.state!r}") from exc
        return out

    @staticmethod
    def _decode(slot: int, raw: str, unit: str | None) -> int:
        if slot in TIME_SLOTS:
            return pack_time(raw[:5])
        value = float(raw)
        if slot == POWER:
            kw = value / 1000 if unit == "W" else value
            return int(round(kw * 10))
        return int(round(value))

    async def write(self, slot: int, value: int) -> None:
        check_write(slot, value)
        entity_id = self.by_slot[slot]
        domain = _domain(entity_id)
        data: dict[str, Any]
        if slot in TIME_SLOTS:
            hhmm = unpack_time(value)
            service = "set_datetime" if domain == "input_datetime" else "set_value"
            data = {"entity_id": entity_id, "time": f"{hhmm}:00"}
        else:
            state = self.hass.states.get(entity_id)
            unit = state.attributes.get("unit_of_measurement") if state else None
            number = value / 10 if slot == POWER else value
            if slot == POWER and unit == "W":
                number *= 1000
            service, data = "set_value", {"entity_id": entity_id, "value": number}
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except Exception as exc:  # noqa: BLE001 - any service failure is a failed write, retried next cycle
            raise DriverError(f"{entity_id}: {exc}") from exc
