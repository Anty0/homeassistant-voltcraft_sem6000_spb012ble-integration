"""
Protocol definitions for Voltcraft SEM6000 / SPB012BLE devices.
Reverse engineered by monitoring communication with an Android app using nRF Connect.
Not all commands are implemented.

Payload structure:
- 0x0f * 1          : Header
- 0xXX * 1          : Length
- 0xXX * 1          : Command
- 0x00 * 1          : ?
- 0xXX * (length-3) : Params
- 0xXX * 1          : Checksum
- 0xFF * 2          : ? (part of the checksum??)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Command(IntEnum):
    SWITCH = 0x03
    MEASURE = 0x04

    def build_payload(self, params: bytearray | None = None) -> bytearray:
        if params is None:
            params = bytearray()

        length = len(params) + 3
        checksum = (1 + sum(list(params)) + self) % 256
        return bytearray([0x0F, length, self, 0x00]) + params + bytearray([checksum, 0xFF, 0xFF])


class SwitchModes(IntEnum):
    ON = 0x01
    OFF = 0x00

    def build_payload(self) -> bytearray:
        return Command.SWITCH.build_payload(bytearray([self]))


class NotifyPayload:
    @staticmethod
    def from_payload(payload: bytearray) -> ParsedNotifyPayload | None:
        if payload[0] != 0x0F:
            # Not a valid payload
            return None

        length = payload[1]
        body = payload[2 : length + 2]

        params = body[0:-1]

        # # The checksum always seems to be wrong...
        # checksum = body[-1]
        # checksumExpected = (1 + sum(list(params))) % 256
        # if checksum != checksumExpected:
        #     # Not a valid payload
        #     return None

        command = params[0]

        arguments = params[2:]

        if command == Command.SWITCH:
            return SwitchNotifyPayload.from_data(arguments)
        elif command == Command.MEASURE:
            return MeasureNotifyPayload.from_data(arguments)
        else:
            # Unknown command
            return None


@dataclass(frozen=True)
class MeasureNotifyPayload(NotifyPayload):
    is_on: bool
    # power: int
    # voltage: int
    # current: int
    # frequency: int
    # power_factor: int
    # consumed_energy: int

    @staticmethod
    def from_data(data: bytearray) -> MeasureNotifyPayload:
        return MeasureNotifyPayload(
            is_on=bool(data[0]),
            # power=int.from_bytes(data[1:4]),
            # voltage=int(data[4]),
            # current=int.from_bytes(data[5:7]),
            # frequency=int(data[7]),
            # power_factor=int.from_bytes(data[8:10]),
            # consumed_energy=int.from_bytes(data[10:14])
        )


@dataclass(frozen=True)
class SwitchNotifyPayload(NotifyPayload):
    @staticmethod
    def from_data(data: bytearray) -> SwitchNotifyPayload:
        return SwitchNotifyPayload()


ParsedNotifyPayload = SwitchNotifyPayload | MeasureNotifyPayload
