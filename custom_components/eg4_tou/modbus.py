"""Minimal async Modbus-TCP client for the EG4 12000XP (whitelisted writes only).

Design notes, learned the hard way on real hardware:

* Many RS485-to-Ethernet gateways broadcast every reply to every connected TCP
  client, so any frame that is not the answer to *our* request (transaction id,
  unit, function code, length) is discarded, never used.
* We keep connections short-lived (connect, do the work, close) so we add as
  little foreign traffic as possible to other clients such as the EG4 web
  monitor integration.
* Writes are limited to the registers this integration manages, and each is
  range-checked. Everything else is refused before it reaches the wire.
"""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

# Registers we are allowed to write, with inclusive (min, max) raw values.
# Time registers encode (minute << 8) | hour.
TIME_REGS = tuple(range(68, 74)) + tuple(range(152, 158))
SOC_REGS = (160, 161)
POWER_REG = 66  # AC charge power, 100 W units

WRITE_LIMITS: dict[int, tuple[int, int]] = {
    POWER_REG: (10, 100),   # 1.0 .. 10.0 kW
    160: (0, 100),          # AC charge start SOC %
    161: (0, 100),          # AC charge end SOC %
}
for _r in TIME_REGS:
    WRITE_LIMITS[_r] = (0, (59 << 8) | 23)


class ModbusError(Exception):
    """Transport or protocol failure (safe to retry for idempotent writes)."""


class WriteRefused(ValueError):
    """The register or value is outside the integration's whitelist."""


def pack_time(hhmm: str) -> int:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"time out of range: {hhmm}")
    return (minutes << 8) | hours


def unpack_time(value: int) -> str:
    return f"{value & 0xFF:02d}:{(value >> 8) & 0xFF:02d}"


def check_write(reg: int, value: int) -> None:
    limits = WRITE_LIMITS.get(reg)
    if limits is None:
        raise WriteRefused(f"register {reg} is not writable by this integration")
    low, high = limits
    if not low <= value <= high:
        raise WriteRefused(f"value {value} out of range {low}..{high} for register {reg}")
    if reg in TIME_REGS and (value & 0xFF) > 23 or reg in TIME_REGS and ((value >> 8) & 0xFF) > 59:
        raise WriteRefused(f"value {value} is not a valid time for register {reg}")


@dataclass
class ModbusConfig:
    host: str
    port: int = 502
    unit: int = 1
    timeout: float = 5.0
    attempts: int = 3
    pause: float = 1.5


class Eg4Modbus:
    """One operation = one short-lived connection, serialised by a lock."""

    def __init__(self, config: ModbusConfig) -> None:
        self._cfg = config
        self._lock = asyncio.Lock()
        self._tid = 0

    async def read(self, start: int, count: int = 1) -> list[int]:
        pdu = struct.pack(">BHH", 3, start, count)
        body = await self._with_retries(pdu, count)
        return list(struct.unpack(f">{count}H", body[2 : 2 + 2 * count]))

    async def write(self, reg: int, value: int) -> None:
        check_write(reg, value)
        body = await self._with_retries(struct.pack(">BHH", 6, reg, value), 1)
        echoed = struct.unpack(">HH", body[1:5])
        if echoed != (reg, value):
            raise ModbusError(f"write echo mismatch: sent {(reg, value)}, got {echoed}")

    async def _with_retries(self, pdu: bytes, count: int) -> bytes:
        last: Exception | None = None
        async with self._lock:
            for attempt in range(self._cfg.attempts):
                try:
                    return await asyncio.wait_for(
                        self._once(pdu, count), timeout=self._cfg.timeout * 2
                    )
                except (OSError, ModbusError, asyncio.TimeoutError) as exc:
                    last = exc
                    if attempt + 1 < self._cfg.attempts:
                        await asyncio.sleep(self._cfg.pause)
        raise ModbusError(f"no valid reply after {self._cfg.attempts} attempts: {last!r}")

    async def _once(self, pdu: bytes, count: int) -> bytes:
        cfg = self._cfg
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(cfg.host, cfg.port), timeout=cfg.timeout
        )
        try:
            self._tid = (self._tid + 1) & 0xFFFF
            tid, want_fc = self._tid, pdu[0]
            writer.write(struct.pack(">HHHB", tid, 0, len(pdu) + 1, cfg.unit) + pdu)
            await writer.drain()
            for _ in range(16):  # tolerate foreign frames broadcast by the gateway
                hdr = await asyncio.wait_for(reader.readexactly(7), timeout=cfg.timeout)
                rtid, proto, length, unit = struct.unpack(">HHHB", hdr)
                if length < 1 or length > 260:
                    raise ModbusError("bad MBAP length")
                body = await asyncio.wait_for(reader.readexactly(length - 1), timeout=cfg.timeout)
                if rtid != tid or proto != 0 or unit != cfg.unit or not body:
                    continue
                if body[0] == (want_fc | 0x80):
                    raise ModbusError(f"modbus exception fc={want_fc} code={body[1] if len(body) > 1 else '?'}")
                if body[0] != want_fc:
                    continue
                if want_fc == 3 and (len(body) != 2 + 2 * count or body[1] != 2 * count):
                    continue
                if want_fc == 6 and len(body) != 5:
                    continue
                return body
            raise ModbusError("no matching reply among foreign frames")
        except asyncio.IncompleteReadError as exc:
            raise ModbusError("connection closed by gateway") from exc
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
