"""
Protocol definitions for Voltcraft SEM6000 / SPB012BLE devices.

This version keeps the existing live MEASURE parser but also adds support
for accumulated consumption history notifications:
- 0x0A: last 23 hours
- 0x0B: last 30 days
- 0x0C: last 12 months

The goal is to derive a persistent device-side total energy counter from the
history path instead of relying on the live MEASURE payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class Command(IntEnum):
    SWITCH = 0x03
    MEASURE = 0x04
    CONSUMPTION_DAY = 0x0A
    CONSUMPTION_MONTH = 0x0B
    CONSUMPTION_YEAR = 0x0C

    def build_payload(self, params: bytes | bytearray | None = None) -> bytearray:
        if params is None:
            params = b""

        params = bytearray(params)
        length = len(params) + 3
        checksum = (1 + sum(params) + int(self)) % 256
        return bytearray([0x0F, length, int(self), 0x00]) + params + bytearray([checksum, 0xFF, 0xFF])


def expected_message_length(buffer: bytes | bytearray) -> int | None:
    if len(buffer) < 2:
        return None
    if buffer[0] != 0x0F:
        return None
    return int(buffer[1]) + 4


class SwitchModes(IntEnum):
    ON = 0x01
    OFF = 0x00

    def build_payload(self) -> bytearray:
        return Command.SWITCH.build_payload(bytearray([self]))


class HistoryKind(Enum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class NotifyPayload:
    @staticmethod
    def from_payload(payload: bytearray) -> ParsedNotifyPayload | None:
        if len(payload) < 4 or payload[0] != 0x0F:
            return None

        expected = expected_message_length(payload)
        if expected is None or len(payload) < expected:
            return None

        length = payload[1]
        body = payload[2 : length + 2]
        params = body[0:-1]

        if len(params) < 2:
            return None

        command = params[0]
        arguments = params[2:]

        if command == Command.SWITCH:
            return SwitchNotifyPayload.from_data(arguments)
        if command == Command.MEASURE:
            return MeasureNotifyPayload.from_data(arguments)
        if command == Command.CONSUMPTION_DAY:
            return ConsumptionHistoryNotifyPayload.from_day(arguments)
        if command == Command.CONSUMPTION_MONTH:
            return ConsumptionHistoryNotifyPayload.from_month(arguments)
        if command == Command.CONSUMPTION_YEAR:
            return ConsumptionHistoryNotifyPayload.from_year(arguments)

        return None


@dataclass(frozen=True)
class MeasureNotifyPayload(NotifyPayload):
    is_on: bool
    power: int
    voltage: int
    current: int
    frequency: int
    consumed_energy: int | None

    @staticmethod
    def from_data(data: bytearray) -> "MeasureNotifyPayload":
        if len(data) < 8:
            raise ValueError(
                f"Unexpected MEASURE payload length: {len(data)} bytes ({data.hex()})"
            )

        return MeasureNotifyPayload(
            is_on=bool(data[0]),
            power=int.from_bytes(data[1:4], byteorder="big"),
            voltage=int(data[4]),
            current=int.from_bytes(data[5:7], byteorder="big"),
            frequency=int(data[7]),
            consumed_energy=int.from_bytes(data[10:14], byteorder="big") if len(data) >= 14 else None,
        )


@dataclass(frozen=True)
class ConsumptionHistoryNotifyPayload(NotifyPayload):
    kind: HistoryKind
    values_wh: tuple[int | None, ...]

    @staticmethod
    def from_day(data: bytearray) -> "ConsumptionHistoryNotifyPayload":
        values: list[int | None] = []
        for offset in range(0, len(data), 2):
            chunk = data[offset : offset + 2]
            if len(chunk) == 2:
                values.insert(0, int.from_bytes(chunk, byteorder="big"))

        return ConsumptionHistoryNotifyPayload(
            kind=HistoryKind.DAY,
            values_wh=tuple(values),
        )

    @staticmethod
    def from_month(data: bytearray) -> "ConsumptionHistoryNotifyPayload":
        values: list[int | None] = []
        for offset in range(0, len(data), 4):
            chunk = data[offset : offset + 4]
            if len(chunk) == 4:
                values.insert(0, int.from_bytes(chunk[0:3], byteorder="big"))

        # Based on known reverse-engineered parsers, the current day is not included.
        values.insert(0, None)

        return ConsumptionHistoryNotifyPayload(
            kind=HistoryKind.MONTH,
            values_wh=tuple(values),
        )

    @staticmethod
    def from_year(data: bytearray) -> "ConsumptionHistoryNotifyPayload":
        values: list[int | None] = []
        for offset in range(0, len(data), 4):
            chunk = data[offset : offset + 4]
            if len(chunk) == 4:
                values.insert(0, int.from_bytes(chunk[0:3], byteorder="big"))

        # Based on known reverse-engineered parsers, the current month is not included.
        values.insert(0, None)

        return ConsumptionHistoryNotifyPayload(
            kind=HistoryKind.YEAR,
            values_wh=tuple(values),
        )


@dataclass(frozen=True)
class SwitchNotifyPayload(NotifyPayload):
    @staticmethod
    def from_data(data: bytearray) -> "SwitchNotifyPayload":
        return SwitchNotifyPayload()


ParsedNotifyPayload = (
    SwitchNotifyPayload
    | MeasureNotifyPayload
    | ConsumptionHistoryNotifyPayload
)
