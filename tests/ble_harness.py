"""Shared fake-BLE scaffolding for the coordinator and recovery tests.

A small importable helper module (not a test module) so both suites consume the
same FakeClient and frame builders without coupling to each other's internals.
"""

from __future__ import annotations

import asyncio

from bleak.exc import BleakError

RAW_MAC = "AA:BB:CC:DD:EE:FF"
FORMATTED_MAC = "aa:bb:cc:dd:ee:ff"

MEASURE_ARGS = bytearray([0x01, 0x00, 0x05, 0xDC, 0xE6, 0x00, 0xFA, 0x32, 0xAB, 0xCD, 0x04, 0xD2])

LOGIN_SUCCESS_FRAME = bytearray.fromhex("0f06170000000018ffff")
LOGIN_FAILURE_FRAME = bytearray.fromhex("0f06170001000018ffff")


def measure_frame() -> bytearray:
    params = bytearray([0x04, 0x00]) + MEASURE_ARGS
    body = params + bytearray([0x00])
    return bytearray([0x0F, len(body)]) + body + bytearray([0xFF, 0xFF])


def switch_frame() -> bytearray:
    params = bytearray([0x03, 0x00, 0x01])
    body = params + bytearray([0x00])
    return bytearray([0x0F, len(body)]) + body + bytearray([0xFF, 0xFF])


def written_commands(client: FakeClient) -> list[int]:
    return [w[2] for w in client.written]


class FakeClient:
    def __init__(self) -> None:
        self.is_connected = True
        self.notify_cb = None
        self.written: list[bytes] = []
        self.stopped = False
        self.disconnect_error = False
        self.raise_on_write = False
        self.hang_on_write = False
        self.raise_on_login_write = False
        self.hang_on_login_write = False
        self.auto_measure_frame: bytearray | None = None
        self.auto_login_frame: bytearray | None = None

    async def start_notify(self, uuid, cb) -> None:
        self.notify_cb = cb

    async def stop_notify(self, uuid) -> None:
        self.stopped = True

    async def write_gatt_char(self, uuid, data) -> None:
        cmd = bytes(data)[2]
        if self.raise_on_write or (self.raise_on_login_write and cmd == 0x17):
            raise BleakError("write failed")
        if self.hang_on_write or (self.hang_on_login_write and cmd == 0x17):
            await asyncio.Event().wait()  # never resolves; relies on the caller's timeout
        self.written.append(bytes(data))
        if cmd == 0x17 and self.auto_login_frame is not None:
            asyncio.get_running_loop().create_task(self._deliver(self.auto_login_frame))
        if cmd == 0x04 and self.auto_measure_frame is not None:
            asyncio.get_running_loop().create_task(self._deliver(self.auto_measure_frame))

    async def _deliver(self, frame: bytearray) -> None:
        if self.notify_cb is not None:
            await self.notify_cb(None, frame)

    async def deliver_frame(self, frame: bytearray) -> None:
        await self.notify_cb(None, frame)

    async def disconnect(self) -> None:
        if self.disconnect_error:
            raise BleakError("disconnect failed")
        self.is_connected = False
