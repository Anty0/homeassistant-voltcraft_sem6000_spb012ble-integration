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

MEASURE notification layout:
  Byte 0       : is_on (bool)
  Bytes 1-3    : power (3 bytes, big-endian, milliwatts)
  Byte 4       : voltage (1 byte, volts)
  Bytes 5-6    : current (2 bytes, big-endian, milliamps)
  Byte 7       : frequency (1 byte, Hz)
  Bytes 8-9    : unknown padding (NOT power_factor)
  Bytes 10+    : consumed_energy (big-endian, Wh)
                 14-byte payload (hw v2): 4 bytes
                 12-byte payload (hw v3): 2 bytes
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
        if len(payload) < 2 or payload[0] != 0x0F:
            # Not a valid payload
            return None

        length = payload[1]
        body = payload[2 : length + 2]

        if length + 2 > len(payload) or len(body) < 3:
            return None

        params = body[0:-1]

        # Checksum validation is disabled: checksums from the device never match
        # the computed value, so we cannot use them to reject payloads.

        command = params[0]

        arguments = params[2:]

        if command == Command.SWITCH:
            return SwitchNotifyPayload.from_data(arguments)
        elif command == Command.MEASURE:
            return MeasureNotifyPayload.from_data(arguments)
        else:
            # Unknown command
            return None


# Minimum valid MEASURE frame length (see the module docstring layout).
MEASURE_ARGS_MIN_LEN = 12


@dataclass(frozen=True)
class MeasureNotifyPayload(NotifyPayload):
    is_on: bool
    power: int
    voltage: int
    current: int
    frequency: int
    consumed_energy: int

    @staticmethod
    def from_data(data: bytearray) -> MeasureNotifyPayload | None:
        if len(data) < MEASURE_ARGS_MIN_LEN:
            # Truncated frame
            return None
        return MeasureNotifyPayload(
            is_on=bool(data[0]),
            power=int.from_bytes(data[1:4], byteorder="big"),
            voltage=int(data[4]),
            current=int.from_bytes(data[5:7], byteorder="big"),
            frequency=int(data[7]),
            consumed_energy=int.from_bytes(data[10:14], byteorder="big"),
        )


@dataclass(frozen=True)
class SwitchNotifyPayload(NotifyPayload):
    @staticmethod
    def from_data(data: bytearray) -> SwitchNotifyPayload:
        return SwitchNotifyPayload()


ParsedNotifyPayload = SwitchNotifyPayload | MeasureNotifyPayload
