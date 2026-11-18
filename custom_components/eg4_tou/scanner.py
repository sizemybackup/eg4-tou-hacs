"""Find the Modbus gateway on the local network (read-only).

Phase 1: quick TCP connect to port 502 on every host of the local /24.
Phase 2: for hosts that answered, read the AC-charge start/end SOC registers
(160/161). An EG4 returns two values 0..100, which is what "looks like an EG4"
means here. Nothing is ever written.
"""
from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass

from .modbus import Eg4Modbus, ModbusConfig, ModbusError

CONNECT_TIMEOUT = 0.7
CONCURRENCY = 100
MAX_PREFIX_SPAN = 24   # never scan more than a /24 per adapter


@dataclass(frozen=True)
class Found:
    host: str
    looks_like_eg4: bool


async def _is_open(host: str, port: int, sem: asyncio.Semaphore) -> bool:
    async with sem:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True


async def _identify(host: str, port: int, unit: int) -> Found:
    client = Eg4Modbus(ModbusConfig(host, port, unit, timeout=2.5, attempts=2, pause=0.5))
    try:
        start, end = await client.read(160, 2)
    except ModbusError:
        return Found(host, False)
    return Found(host, 0 <= start <= 100 and 0 <= end <= 100)


async def scan_hosts(hosts: list[str], port: int = 502, unit: int = 1) -> list[Found]:
    sem = asyncio.Semaphore(CONCURRENCY)
    opened = await asyncio.gather(*(_is_open(h, port, sem) for h in hosts))
    open_hosts = [h for h, ok in zip(hosts, opened) if ok]
    found = await asyncio.gather(*(_identify(h, port, unit) for h in open_hosts))
    return sorted(found, key=lambda f: (not f.looks_like_eg4, ipaddress.ip_address(f.host)))


async def candidate_hosts(hass) -> list[str]:
    """Hosts on the /24 (or smaller) of each enabled IPv4 adapter, minus ourselves."""
    from homeassistant.components import network

    try:
        adapters = await network.async_get_adapters(hass)
    except Exception:  # network integration unavailable: fall back to manual entry
        return []
    hosts: list[str] = []
    for adapter in adapters:
        if not adapter.get("enabled"):
            continue
        for ip in adapter.get("ipv4", []):
            own = ip["address"]
            if ipaddress.ip_address(own).is_loopback:
                continue
            prefix = max(int(ip["network_prefix"]), MAX_PREFIX_SPAN)
            net = ipaddress.ip_network(f"{own}/{prefix}", strict=False)
            hosts += [str(h) for h in net.hosts() if str(h) != own]
    return list(dict.fromkeys(hosts))


async def async_scan(hass, port: int = 502, unit: int = 1) -> list[Found]:
    return await scan_hosts(await candidate_hosts(hass), port, unit)
