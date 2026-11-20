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
from typing import TYPE_CHECKING

from .modbus import Eg4Modbus, ModbusConfig, ModbusError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CONNECT_TIMEOUT = 0.7
CONCURRENCY = 100
MAX_PREFIX_SPAN = 24   # never scan more than a /24 per adapter


@dataclass(frozen=True)
class Found:
    host: str
    looks_like_eg4: bool


# Explicit list on purpose: ipaddress.is_private is also true for reserved documentation ranges and
# other non-routable blocks. Shared/CGNAT space (100.64.0.0/10) is deliberately NOT local: it can be an
# ISP's network, not yours (use a host name for a Tailscale-style address).
_LOCAL_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",      # RFC 1918
    "127.0.0.0/8", "169.254.0.0/16",                      # loopback, link-local
    "::1/128", "fc00::/7", "fe80::/10",                   # IPv6 loopback, unique-local, link-local
))


def is_local_address(host: str) -> bool:
    """True only for IP literals on your own network (RFC 1918, loopback, link-local), never a public
    address. Modbus has no authentication, so this integration must never probe or talk to the internet."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip.version == n.version and ip in n for n in _LOCAL_NETS)


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
    hosts = [h for h in hosts if is_local_address(h)]   # defence in depth: private networks only
    sem = asyncio.Semaphore(CONCURRENCY)
    opened = await asyncio.gather(*(_is_open(h, port, sem) for h in hosts))
    open_hosts = [h for h, ok in zip(hosts, opened) if ok]
    found = await asyncio.gather(*(_identify(h, port, unit) for h in open_hosts))
    return sorted(found, key=lambda f: (not f.looks_like_eg4, ipaddress.ip_address(f.host)))


async def candidate_hosts(hass: HomeAssistant) -> list[str]:
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
            if ipaddress.ip_address(own).is_loopback or not is_local_address(own):
                continue   # skip loopback and any adapter with a public address
            prefix = max(int(ip["network_prefix"]), MAX_PREFIX_SPAN)
            net = ipaddress.ip_network(f"{own}/{prefix}", strict=False)
            hosts += [str(h) for h in net.hosts() if str(h) != own]
    return list(dict.fromkeys(hosts))


async def async_scan(hass: HomeAssistant, port: int = 502, unit: int = 1) -> list[Found]:
    return await scan_hosts(await candidate_hosts(hass), port, unit)
