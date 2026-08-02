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

Accumulated consumption notifications:
- 0x0A          : last 23 hours
- 0x0B          : last 30 days
- 0x0C          : last 12 months
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

    def build_payload(self, params: bytearray | None = None) -> bytearray:
        if params is None:
            params = bytearray()

        length = len(params) + 3
        checksum = (1 + sum(list(params)) + self) % 256
        return bytearray([0x0F, length, self, 0x00]) + params + bytearray([checksum, 0xFF, 0xFF])


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
            # Not a valid payload
            return None

        expected = expected_message_length(payload)
        if expected is None or len(payload) < expected:
            # Incomplete payload
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

        if len(params) < 2:
            # Not enough data for command + status byte
            return None

        command = params[0]

        arguments = params[2:]

        if command == Command.SWITCH:
            return SwitchNotifyPayload.from_data(arguments)
        elif command == Command.MEASURE:
            return MeasureNotifyPayload.from_data(arguments)
        elif command == Command.CONSUMPTION_DAY:
            return ConsumptionHistoryNotifyPayload.from_day(arguments)
        elif command == Command.CONSUMPTION_MONTH:
            return ConsumptionHistoryNotifyPayload.from_month(arguments)
        elif command == Command.CONSUMPTION_YEAR:
            return ConsumptionHistoryNotifyPayload.from_year(arguments)
        else:
            # Unknown command
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
    def from_data(data: bytearray) -> MeasureNotifyPayload:
        if len(data) < 8:
            raise ValueError(
                f"Unexpected MEASURE payload length: {len(data)} bytes ({data.hex()})"
            )

        # data[8:10] are unknown padding bytes — skip them
        # 14-byte payloads may contain total consumption at offset 10;
        # 12-byte payloads do not provide a usable value on affected devices
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
    def from_day(data: bytearray) -> ConsumptionHistoryNotifyPayload:
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
    def from_month(data: bytearray) -> ConsumptionHistoryNotifyPayload:
        values: list[int | None] = []

        for offset in range(0, len(data), 4):
            chunk = data[offset : offset + 4]
            if len(chunk) == 4:
                values.insert(0, int.from_bytes(chunk[0:3], byteorder="big"))

        # Notification does not contain measurement for today
        values.insert(0, None)

        return ConsumptionHistoryNotifyPayload(
            kind=HistoryKind.MONTH,
            values_wh=tuple(values),
        )

    @staticmethod
    def from_year(data: bytearray) -> ConsumptionHistoryNotifyPayload:
        values: list[int | None] = []

        for offset in range(0, len(data), 4):
            chunk = data[offset : offset + 4]
            if len(chunk) == 4:
                values.insert(0, int.from_bytes(chunk[0:3], byteorder="big"))

        # Notification does not contain measurement for current month
        values.insert(0, None)

        return ConsumptionHistoryNotifyPayload(
            kind=HistoryKind.YEAR,
            values_wh=tuple(values),
        )


@dataclass(frozen=True)
class SwitchNotifyPayload(NotifyPayload):
    @staticmethod
    def from_data(data: bytearray) -> SwitchNotifyPayload:
        return SwitchNotifyPayload()


ParsedNotifyPayload = (
    SwitchNotifyPayload
    | MeasureNotifyPayload
    | ConsumptionHistoryNotifyPayload
)
