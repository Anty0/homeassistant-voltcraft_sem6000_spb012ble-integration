"""Protocol support for Voltcraft SEM6000 / SPB012BLE devices.

The frame formats are based on the public reverse-engineered SEM6000 protocol
and the user's Android HCI capture. The separate over-power protection command
(0x06) is taken from that capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum, IntEnum
from typing import TypeAlias


class Command(IntEnum):
    SET_TIME = 0x01
    SET_NAME = 0x02
    SWITCH = 0x03
    MEASURE = 0x04
    SET_POWER_LIMIT = 0x05
    SET_POWER_PROTECTION = 0x06
    APP_FINALIZE = 0x07
    SET_TIMER = 0x08
    GET_TIMER = 0x09
    CONSUMPTION_DAY = 0x0A
    CONSUMPTION_MONTH = 0x0B
    CONSUMPTION_YEAR = 0x0C
    SETTINGS_CONTROL = 0x0F
    GET_SETTINGS = 0x10
    GET_SERIAL = 0x11
    SET_SCHEDULE = 0x13
    GET_SCHEDULE = 0x14
    SET_RANDOM = 0x15
    GET_RANDOM = 0x16
    LOGIN = 0x17

    def build_payload(self, params: bytes | bytearray | None = None) -> bytearray:
        return build_frame(self, 0x00, params or b"")


class SettingsOperation(IntEnum):
    RESET = 0x00
    REDUCED_PERIOD = 0x01
    PRICES = 0x04
    NIGHT_MODE = 0x05


class PinOperation(IntEnum):
    AUTHORIZE = 0x00
    CHANGE = 0x01
    RESET = 0x02


class SwitchModes(IntEnum):
    ON = 0x01
    OFF = 0x00

    def build_payload(self) -> bytearray:
        return build_frame(Command.SWITCH, 0x00, bytes([self, 0x00, 0x00]))


class TimerAction(IntEnum):
    INACTIVE = 0x00
    TURN_ON = 0x01
    TURN_OFF = 0x02


class ScheduleOperation(IntEnum):
    ADD = 0x00
    EDIT = 0x01
    REMOVE = 0x02


class HistoryKind(Enum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"


WEEKDAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def build_frame(
    command: int | Command,
    subcommand: int = 0,
    params: bytes | bytearray = b"",
    suffix: bytes = b"\xff\xff",
) -> bytearray:
    """Build a SEM6000 frame.

    The checksum used by this protocol is ``1 + sum(payload)`` modulo 256,
    where payload starts with command and subcommand.
    """
    payload = bytes([int(command), subcommand]) + bytes(params)
    length = len(payload) + 1
    checksum = (1 + sum(payload)) & 0xFF
    return bytearray(
        b"\x0f" + bytes([length]) + payload + bytes([checksum]) + suffix
    )


def expected_message_length(buffer: bytes | bytearray) -> int | None:
    if len(buffer) < 2 or buffer[0] != 0x0F:
        return None

    # The official Android app sends command 0x07 at the end of its initial
    # state read. This firmware answers with a nine-byte frame whose length
    # byte claims 0x08. Handle the observed wire format explicitly so the
    # following notification is not accidentally appended to it.
    if len(buffer) >= 4 and buffer[2] == Command.APP_FINALIZE and buffer[3] == 0:
        return 9

    # Measurement notifications are always 19 bytes on the wire. Hardware
    # version 2 reports length 0x11 and omits the usual FFFF suffix, while
    # hardware version 3 reports 0x0F. Both use the final wire byte as checksum.
    if (
        len(buffer) >= 4
        and buffer[2] == Command.MEASURE
        and buffer[3] == 0
        and buffer[1] in (0x0F, 0x11)
    ):
        return 19

    return int(buffer[1]) + 4


def validate_notify_frame(payload: bytes | bytearray) -> bytes:
    """Validate frame length, suffix and checksum before parsing its content."""
    if len(payload) < 4 or payload[0] != 0x0F:
        raise ValueError("Invalid SEM6000 frame header")

    expected = expected_message_length(payload)
    if expected is None or len(payload) < expected:
        raise ValueError("Incomplete SEM6000 frame")
    frame = bytes(payload[:expected])

    command = frame[2]
    subcommand = frame[3]

    if command == Command.APP_FINALIZE and subcommand == 0:
        # Observed wire response: 0f080700000007ffff. This response uses the
        # payload sum without the usual +1 as its checksum. Validate that
        # documented exception explicitly instead of accepting arbitrary data.
        if len(frame) != 9 or frame[1] != 0x08 or frame[-2:] != b"\xff\xff":
            raise ValueError("Invalid app-finalization response framing")
        expected_checksum = sum(frame[2:6]) & 0xFF
        if frame[6] != expected_checksum:
            raise ValueError("Invalid app-finalization response checksum")
        return frame

    if command == Command.MEASURE and subcommand == 0 and len(frame) == 19:
        actual_checksum = frame[-1]
        expected_checksum = (1 + sum(frame[2:-1])) & 0xFF
        if actual_checksum != expected_checksum:
            raise ValueError("Invalid measurement checksum")
        return frame

    length = frame[1]
    checksum_index = length + 1
    if checksum_index + 2 >= len(frame):
        raise ValueError("Invalid SEM6000 frame length")
    if frame[checksum_index + 1 : checksum_index + 3] != b"\xff\xff":
        raise ValueError("Invalid SEM6000 frame suffix")

    actual_checksum = frame[checksum_index]
    expected_checksum = (1 + sum(frame[2:checksum_index])) & 0xFF
    if actual_checksum != expected_checksum:
        raise ValueError("Invalid SEM6000 frame checksum")
    return frame


def normalize_pin(pin: str) -> str:
    pin = str(pin).strip()
    if len(pin) != 4 or not pin.isascii() or not pin.isdigit():
        raise ValueError("PIN must be exactly four ASCII decimal digits")
    return pin


def pin_bytes(pin: str) -> bytes:
    return bytes(int(digit) for digit in normalize_pin(pin))


def weekday_mask(values: list[str] | tuple[str, ...] | set[str] | str) -> int:
    if isinstance(values, str):
        values = [part.strip().lower() for part in values.split(",") if part.strip()]
    aliases = {
        "so": "sun",
        "sonntag": "sun",
        "sunday": "sun",
        "mo": "mon",
        "montag": "mon",
        "monday": "mon",
        "di": "tue",
        "dienstag": "tue",
        "tuesday": "tue",
        "mi": "wed",
        "mittwoch": "wed",
        "wednesday": "wed",
        "do": "thu",
        "donnerstag": "thu",
        "thursday": "thu",
        "fr": "fri",
        "freitag": "fri",
        "friday": "fri",
        "sa": "sat",
        "samstag": "sat",
        "saturday": "sat",
    }
    mask = 0
    for value in values:
        key = aliases.get(str(value).strip().lower(), str(value).strip().lower())
        if key not in WEEKDAY_NAMES:
            raise ValueError(f"Unknown weekday: {value}")
        mask |= 1 << WEEKDAY_NAMES.index(key)
    return mask


def weekdays_from_mask(mask: int) -> tuple[str, ...]:
    return tuple(
        name for index, name in enumerate(WEEKDAY_NAMES) if mask & (1 << index)
    )


def minutes_from_time(value: time) -> int:
    return value.hour * 60 + value.minute


def time_from_minutes(value: int) -> time:
    value %= 24 * 60
    return time(value // 60, value % 60)


class Commands:
    @staticmethod
    def login(pin: str) -> bytearray:
        return build_frame(
            Command.LOGIN,
            0,
            bytes([PinOperation.AUTHORIZE]) + pin_bytes(pin) + b"\0" * 4,
        )

    @staticmethod
    def change_pin(old_pin: str, new_pin: str) -> bytearray:
        return build_frame(
            Command.LOGIN,
            0,
            bytes([PinOperation.CHANGE]) + pin_bytes(new_pin) + pin_bytes(old_pin),
        )

    @staticmethod
    def reset_pin() -> bytearray:
        return build_frame(
            Command.LOGIN, 0, bytes([PinOperation.RESET]) + b"\0" * 8
        )

    @staticmethod
    def sync_time(value: datetime) -> bytearray:
        return build_frame(
            Command.SET_TIME,
            0,
            bytes([value.second, value.minute, value.hour, value.day, value.month])
            + value.year.to_bytes(2, "big")
            + b"\0\0",
        )

    @staticmethod
    def request_settings() -> bytearray:
        return build_frame(Command.GET_SETTINGS, 0, b"\0\0")

    @staticmethod
    def app_finalize() -> bytearray:
        """Send the read-only 0x07 probe used by the official Android app."""
        return build_frame(Command.APP_FINALIZE, 0, b"\0\0")

    @staticmethod
    def set_night_mode(enabled: bool) -> bytearray:
        # Night mode ON means LED ring OFF.
        return build_frame(
            Command.SETTINGS_CONTROL,
            0,
            bytes([SettingsOperation.NIGHT_MODE, 0 if enabled else 1]) + b"\0" * 4,
        )

    @staticmethod
    def set_power_limit(watts: int) -> bytearray:
        if not 1 <= int(watts) <= 4000:
            raise ValueError("Power limit must be between 1 and 4000 W")
        return build_frame(
            Command.SET_POWER_LIMIT, 0, int(watts).to_bytes(2, "big") + b"\0\0"
        )

    @staticmethod
    def set_power_protection(enabled: bool) -> bytearray:
        return build_frame(
            Command.SET_POWER_PROTECTION, 0, bytes([1 if enabled else 0, 0])
        )

    @staticmethod
    def set_prices(normal_cents: int, reduced_cents: int) -> bytearray:
        if not 0 <= normal_cents <= 255 or not 0 <= reduced_cents <= 255:
            raise ValueError("Tariff prices must be between 0 and 255 cents/kWh")
        return build_frame(
            Command.SETTINGS_CONTROL,
            0,
            bytes([SettingsOperation.PRICES, normal_cents, reduced_cents])
            + b"\0" * 4,
        )

    @staticmethod
    def set_reduced_period(enabled: bool, start: time, end: time) -> bytearray:
        return build_frame(
            Command.SETTINGS_CONTROL,
            0,
            bytes([SettingsOperation.REDUCED_PERIOD, 1 if enabled else 0])
            + minutes_from_time(start).to_bytes(2, "big")
            + minutes_from_time(end).to_bytes(2, "big"),
        )

    @staticmethod
    def measure() -> bytearray:
        return build_frame(Command.MEASURE, 0, b"\0\0")

    @staticmethod
    def request_history(kind: HistoryKind) -> bytearray:
        command = {
            HistoryKind.DAY: Command.CONSUMPTION_DAY,
            HistoryKind.MONTH: Command.CONSUMPTION_MONTH,
            HistoryKind.YEAR: Command.CONSUMPTION_YEAR,
        }[kind]
        return build_frame(command, 0, b"\0\0")

    @staticmethod
    def request_timer() -> bytearray:
        return build_frame(Command.GET_TIMER, 0, b"\0\0")

    @staticmethod
    def set_timer(action: TimerAction, target: datetime | None) -> bytearray:
        if action is TimerAction.INACTIVE or target is None:
            fields = b"\0" * 6
            action = TimerAction.INACTIVE
        else:
            fields = bytes(
                [
                    target.second,
                    target.minute,
                    target.hour,
                    target.day,
                    target.month,
                    target.year % 100,
                ]
            )
        return build_frame(
            Command.SET_TIMER, 0, bytes([action]) + fields + b"\0\0"
        )

    @staticmethod
    def request_schedule(page: int = 0) -> bytearray:
        if not 0 <= page <= 2:
            raise ValueError("Schedule page must be 0, 1 or 2")
        return build_frame(Command.GET_SCHEDULE, 0, bytes([page, 0, 0]))

    @staticmethod
    def set_schedule(
        operation: ScheduleOperation,
        slot_id: int,
        active: bool,
        turn_on: bool,
        weekdays: int,
        when: datetime,
    ) -> bytearray:
        wire_slot = 0 if operation is ScheduleOperation.ADD else slot_id
        if operation is ScheduleOperation.REMOVE:
            return build_frame(
                Command.SET_SCHEDULE,
                0,
                bytes([operation, wire_slot]) + b"\0" * 10,
            )
        params = bytes(
            [
                operation,
                wire_slot,
                1 if active else 0,
                1 if turn_on else 0,
                weekdays & 0x7F,
                when.year % 100,
                when.month,
                when.day,
                when.hour,
                when.minute,
                0,
                0,
            ]
        )
        return build_frame(Command.SET_SCHEDULE, 0, params)

    @staticmethod
    def request_random() -> bytearray:
        return build_frame(Command.GET_RANDOM, 0, b"\0\0")

    @staticmethod
    def set_random(
        enabled: bool, weekdays: int, start: time, end: time
    ) -> bytearray:
        return build_frame(
            Command.SET_RANDOM,
            0,
            bytes(
                [
                    1 if enabled else 0,
                    weekdays & 0x7F,
                    start.hour,
                    start.minute,
                    end.hour,
                    end.minute,
                    0,
                    0,
                ]
            ),
        )

    @staticmethod
    def reset_consumption() -> bytearray:
        return build_frame(
            Command.SETTINGS_CONTROL,
            0,
            bytes([0x02]) + b"\0" * 5,
        )

    @staticmethod
    def factory_reset() -> bytearray:
        return build_frame(
            Command.SETTINGS_CONTROL,
            0,
            bytes([0x00]) + b"\0" * 5,
        )

    @staticmethod
    def set_name(name: str) -> bytearray:
        encoded = name.encode("utf-8")
        if not encoded:
            raise ValueError("Device name must not be empty")
        if len(encoded) > 20:
            raise ValueError("Device name must be at most 20 UTF-8 bytes")
        return build_frame(Command.SET_NAME, 0, encoded.ljust(20, b"\0"))

    @staticmethod
    def request_serial() -> bytearray:
        return build_frame(Command.GET_SERIAL, 0, b"\0\0")


@dataclass(frozen=True)
class MeasureNotifyPayload:
    is_on: bool
    power: int
    voltage: int
    current: int
    frequency: int
    consumed_energy: int | None


@dataclass(frozen=True)
class ConsumptionHistoryNotifyPayload:
    kind: HistoryKind
    values_wh: tuple[int | None, ...]


@dataclass(frozen=True)
class LoginNotifyPayload:
    operation: PinOperation
    was_successful: bool


@dataclass(frozen=True)
class SettingsNotifyPayload:
    reduced_tariff_enabled: bool
    normal_price_cents: int
    reduced_price_cents: int
    reduced_start: time
    reduced_end: time
    night_mode: bool
    power_protection_enabled: bool | None
    power_limit_watts: int


@dataclass(frozen=True)
class TimerStatusNotifyPayload:
    action: TimerAction
    target: datetime | None
    original_runtime_seconds: int

    @property
    def active(self) -> bool:
        return self.action is not TimerAction.INACTIVE


@dataclass(frozen=True)
class RandomStatusNotifyPayload:
    enabled: bool
    weekday_mask: int
    start: time
    end: time


@dataclass(frozen=True)
class ScheduleEntry:
    slot_id: int
    active: bool
    turn_on: bool
    weekday_mask: int
    year: int
    month: int
    day: int
    hour: int
    minute: int

    @property
    def weekdays(self) -> tuple[str, ...]:
        return weekdays_from_mask(self.weekday_mask)

    @property
    def is_repeating(self) -> bool:
        return self.weekday_mask != 0

    @property
    def when(self) -> datetime | None:
        if self.is_repeating:
            return None
        try:
            return datetime(
                2000 + self.year,
                self.month,
                self.day,
                self.hour,
                self.minute,
            )
        except ValueError:
            return None

    @property
    def at_time(self) -> time:
        return time(self.hour, self.minute)


@dataclass(frozen=True)
class ScheduleStatusNotifyPayload:
    total_count: int
    entries: tuple[ScheduleEntry, ...]


@dataclass(frozen=True)
class SerialNotifyPayload:
    serial: str


@dataclass(frozen=True)
class AckNotifyPayload:
    command: int
    operation: int
    was_successful: bool


ParsedNotifyPayload: TypeAlias = (
    MeasureNotifyPayload
    | ConsumptionHistoryNotifyPayload
    | LoginNotifyPayload
    | SettingsNotifyPayload
    | TimerStatusNotifyPayload
    | RandomStatusNotifyPayload
    | ScheduleStatusNotifyPayload
    | SerialNotifyPayload
    | AckNotifyPayload
)


def _history_payload(
    kind: HistoryKind, data: bytes
) -> ConsumptionHistoryNotifyPayload:
    values: list[int | None] = []
    if kind is HistoryKind.DAY:
        size = 2
        for offset in range(0, len(data), size):
            chunk = data[offset : offset + size]
            if len(chunk) == size:
                values.insert(0, int.from_bytes(chunk, "big"))
    else:
        size = 4
        for offset in range(0, len(data), size):
            chunk = data[offset : offset + size]
            if len(chunk) == size:
                values.insert(0, int.from_bytes(chunk[:3], "big"))
        values.insert(0, None)
    return ConsumptionHistoryNotifyPayload(kind, tuple(values))


def parse_notify_payload(
    payload: bytes | bytearray,
) -> ParsedNotifyPayload | None:
    if len(payload) < 4 or payload[0] != 0x0F:
        return None
    expected = expected_message_length(payload)
    if expected is None or len(payload) < expected:
        return None

    frame = validate_notify_frame(payload)

    if frame[2] == Command.APP_FINALIZE and frame[3] == 0 and expected == 9:
        # Observed response: 0f080700000007ffff. It carries no documented
        # state; zero in the first status byte is treated as success.
        return AckNotifyPayload(Command.APP_FINALIZE, 0, frame[4] == 0)

    length = frame[1]
    body = bytes(frame[2 : length + 2])
    if len(body) < 3:
        return None
    params = body[:-1]  # strip protocol checksum in the standard body layout
    if len(params) < 2:
        return None
    command, subcommand = params[0], params[1]
    args = params[2:]

    if command == Command.MEASURE:
        if len(args) < 8:
            raise ValueError(
                f"Unexpected MEASURE payload length: {len(args)} bytes"
            )
        return MeasureNotifyPayload(
            is_on=bool(args[0]),
            power=int.from_bytes(args[1:4], "big"),
            voltage=args[4],
            current=int.from_bytes(args[5:7], "big"),
            frequency=args[7],
            consumed_energy=(
                int.from_bytes(args[10:14], "big") if len(args) >= 14 else None
            ),
        )

    if command == Command.CONSUMPTION_DAY:
        return _history_payload(HistoryKind.DAY, args)
    if command == Command.CONSUMPTION_MONTH:
        return _history_payload(HistoryKind.MONTH, args)
    if command == Command.CONSUMPTION_YEAR:
        return _history_payload(HistoryKind.YEAR, args)

    if command == Command.LOGIN:
        if not args:
            raise ValueError("PIN response has no status")
        operation = (
            PinOperation(args[1])
            if len(args) >= 2 and args[1] in PinOperation._value2member_map_
            else PinOperation.AUTHORIZE
        )
        return LoginNotifyPayload(operation, args[0] == 0)

    if command == Command.GET_SETTINGS:
        if len(args) < 11:
            raise ValueError(
                f"Unexpected settings payload length: {len(args)} bytes"
            )
        return SettingsNotifyPayload(
            reduced_tariff_enabled=bool(args[0]),
            normal_price_cents=args[1],
            reduced_price_cents=args[2],
            reduced_start=time_from_minutes(int.from_bytes(args[3:5], "big")),
            reduced_end=time_from_minutes(int.from_bytes(args[5:7], "big")),
            night_mode=args[7] == 0,
            # On the tested SEM6000 firmware, args[8] remains 0 both before
            # and after a successfully acknowledged 0x06 protection toggle.
            power_protection_enabled=None,
            power_limit_watts=int.from_bytes(args[9:11], "big"),
        )

    if command == Command.GET_TIMER:
        if len(args) < 10:
            raise ValueError(
                f"Unexpected timer payload length: {len(args)} bytes"
            )
        action = (
            TimerAction(args[0])
            if args[0] in TimerAction._value2member_map_
            else TimerAction.INACTIVE
        )
        target = None
        if action is not TimerAction.INACTIVE:
            try:
                target = datetime(
                    2000 + args[6],
                    args[5],
                    args[4],
                    args[3],
                    args[2],
                    args[1],
                )
            except ValueError:
                target = None
        runtime = int.from_bytes(args[7:10], "big")
        return TimerStatusNotifyPayload(action, target, runtime)

    if command == Command.GET_RANDOM:
        if len(args) < 6:
            raise ValueError(
                f"Unexpected random-mode payload length: {len(args)} bytes"
            )
        try:
            start = time(args[2], args[3])
            end = time(args[4], args[5])
        except ValueError as err:
            raise ValueError("Invalid random-mode time fields") from err
        return RandomStatusNotifyPayload(
            enabled=bool(args[0]),
            weekday_mask=args[1],
            start=start,
            end=end,
        )

    if command == Command.GET_SCHEDULE:
        if not args:
            return ScheduleStatusNotifyPayload(0, ())
        count = args[0]
        entries: list[ScheduleEntry] = []
        data = args[1:]
        for offset in range(0, len(data), 12):
            chunk = data[offset : offset + 12]
            if len(chunk) < 12:
                break
            try:
                time(chunk[7], chunk[8])
            except ValueError:
                # A corrupted schedule must not make the complete entity fail.
                continue
            entries.append(
                ScheduleEntry(
                    slot_id=chunk[0],
                    active=bool(chunk[1]),
                    turn_on=bool(chunk[2]),
                    weekday_mask=chunk[3],
                    year=chunk[4],
                    month=chunk[5],
                    day=chunk[6],
                    hour=chunk[7],
                    minute=chunk[8],
                )
            )
        return ScheduleStatusNotifyPayload(count, tuple(entries))

    if command == Command.GET_SERIAL:
        serial = args.rstrip(b"\0").decode("ascii", errors="replace")
        return SerialNotifyPayload(serial)

    # Generic acknowledgements. 0x0f embeds its operation as the first arg;
    # 0x17 is handled above because its layout is different.
    operation = (
        args[0] if command == Command.SETTINGS_CONTROL and args else subcommand
    )
    status_index = (
        1 if command == Command.SETTINGS_CONTROL and len(args) >= 2 else 0
    )
    success = not args or args[status_index] == 0
    return AckNotifyPayload(command, operation, success)


def response_key(payload: ParsedNotifyPayload) -> tuple[int, int] | None:
    if isinstance(payload, LoginNotifyPayload):
        return (Command.LOGIN, int(payload.operation))
    if isinstance(payload, SettingsNotifyPayload):
        return (Command.GET_SETTINGS, 0)
    if isinstance(payload, TimerStatusNotifyPayload):
        return (Command.GET_TIMER, 0)
    if isinstance(payload, RandomStatusNotifyPayload):
        return (Command.GET_RANDOM, 0)
    if isinstance(payload, ScheduleStatusNotifyPayload):
        return (Command.GET_SCHEDULE, 0)
    if isinstance(payload, SerialNotifyPayload):
        return (Command.GET_SERIAL, 0)
    if isinstance(payload, MeasureNotifyPayload):
        return (Command.MEASURE, 0)
    if isinstance(payload, ConsumptionHistoryNotifyPayload):
        command = {
            HistoryKind.DAY: Command.CONSUMPTION_DAY,
            HistoryKind.MONTH: Command.CONSUMPTION_MONTH,
            HistoryKind.YEAR: Command.CONSUMPTION_YEAR,
        }[payload.kind]
        return (command, 0)
    if isinstance(payload, AckNotifyPayload):
        return (payload.command, payload.operation)
    return None
