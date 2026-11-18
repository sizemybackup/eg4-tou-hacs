"""Modbus client tests against a fake gateway that injects foreign frames,
like the real RS485 gateway that broadcasts every client's replies."""
import asyncio
import struct

import pytest

from custom_components.eg4_tou.modbus import (
    Eg4Modbus, ModbusConfig, ModbusError, WriteRefused,
)


class FakeGateway:
    def __init__(self, regs, noise=True, drop_first=0):
        self.regs, self.noise, self.drop_first = dict(regs), noise, drop_first
        self.writes = []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            hdr = await reader.readexactly(7)
            tid, _, length, unit = struct.unpack(">HHHB", hdr)
            pdu = await reader.readexactly(length - 1)
            if self.drop_first > 0:
                self.drop_first -= 1
                writer.close()
                return
            if self.noise:  # someone else's reply, wrong tid
                foreign = bytes([3, 2, 0, 7])
                writer.write(struct.pack(">HHHB", (tid + 100) & 0xFFFF, 0, len(foreign) + 1, unit) + foreign)
            fc = pdu[0]
            if fc == 3:
                start, count = struct.unpack(">HH", pdu[1:5])
                data = b"".join(struct.pack(">H", self.regs.get(start + i, 0)) for i in range(count))
                body = bytes([3, 2 * count]) + data
            else:
                reg, val = struct.unpack(">HH", pdu[1:5])
                self.regs[reg] = val
                self.writes.append((reg, val))
                body = pdu
            writer.write(struct.pack(">HHHB", tid, 0, len(body) + 1, unit) + body)
            await writer.drain()
        except asyncio.IncompleteReadError:
            pass  # a port probe connected and hung up without a request
        finally:
            writer.close()


@pytest.fixture
async def gw():
    g = FakeGateway({160: 65, 161: 70, 66: 50})
    port = await g.start()
    g.cfg = ModbusConfig("127.0.0.1", port, timeout=1.0, attempts=3, pause=0.01)
    yield g
    g.server.close()


async def test_read_ignores_foreign_frames(gw):
    assert await Eg4Modbus(gw.cfg).read(160, 2) == [65, 70]


async def test_write_roundtrip(gw):
    await Eg4Modbus(gw.cfg).write(161, 80)
    assert gw.writes == [(161, 80)]


async def test_retries_after_dropped_connection(gw):
    gw.drop_first = 2
    assert await Eg4Modbus(gw.cfg).read(66) == [50]


async def test_fails_after_all_attempts(gw):
    gw.drop_first = 99
    with pytest.raises(ModbusError):
        await Eg4Modbus(gw.cfg).read(66)


@pytest.mark.parametrize("reg,val", [(100, 1), (161, 101), (66, 5), (152, 24), (152, (60 << 8))])
async def test_refuses_out_of_whitelist(gw, reg, val):
    with pytest.raises(WriteRefused):
        await Eg4Modbus(gw.cfg).write(reg, val)
    assert gw.writes == []
