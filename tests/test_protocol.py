"""Unit tests for the reverse-engineered BLE protocol.

No physical device or original nRF Connect capture is available in the repo, so
the fixtures are synthesized from the documented byte layout in ``protocol.py``
(the contract under test). The ``build_payload`` -> ``from_payload`` round-trip
test independently cross-checks the framing without relying on those offsets.
"""

from __future__ import annotations

import pytest

from custom_components.voltcraft_sem6000_spb012ble.protocol import (
    Command,
    MeasureNotifyPayload,
    NotifyPayload,
    SwitchModes,
    SwitchNotifyPayload,
)

# Synthesized 12-byte (hw v3, 2-byte energy) MEASURE argument block.
MEASURE_ARGS_12 = bytearray(
    [
        0x01,  # is_on
        0x00,
        0x05,
        0xDC,  # power = 1500
        0xE6,  # voltage = 230
        0x00,
        0xFA,  # current = 250
        0x32,  # frequency = 50
        0xAB,
        0xCD,  # padding (ignored)
        0x04,
        0xD2,  # consumed_energy = 1234
    ]
)

# Synthesized 14-byte (hw v2, 4-byte energy) variant with the same fields.
MEASURE_ARGS_14 = bytearray(
    [
        0x01,
        0x00,
        0x05,
        0xDC,
        0xE6,
        0x00,
        0xFA,
        0x32,
        0xAB,
        0xCD,
        0x00,
        0x00,
        0x04,
        0xD2,  # consumed_energy = 1234 (4 bytes)
    ]
)


def make_notify(command: int, arguments: bytearray | list[int]) -> bytearray:
    """Wrap a command + argument block in the notification framing.

    Layout: 0x0F, length, command, 0x00 (unknown), *arguments, checksum, 0xFF 0xFF.
    The checksum is irrelevant (device validation is disabled) so it is 0x00.
    """
    params = bytearray([command, 0x00]) + bytearray(arguments)
    body = params + bytearray([0x00])  # trailing checksum byte
    return bytearray([0x0F, len(body)]) + body + bytearray([0xFF, 0xFF])


def test_measure_build_payload_exact_bytes():
    assert Command.MEASURE.build_payload() == bytearray([0x0F, 0x03, 0x04, 0x00, 0x05, 0xFF, 0xFF])


def test_switch_on_build_payload_exact_bytes():
    assert SwitchModes.ON.build_payload() == bytearray([0x0F, 0x04, 0x03, 0x00, 0x01, 0x05, 0xFF, 0xFF])


def test_switch_off_build_payload_exact_bytes():
    assert SwitchModes.OFF.build_payload() == bytearray([0x0F, 0x04, 0x03, 0x00, 0x00, 0x04, 0xFF, 0xFF])


@pytest.mark.parametrize("arguments", [MEASURE_ARGS_12, MEASURE_ARGS_14])
def test_measure_parse_fields(arguments):
    payload = NotifyPayload.from_payload(make_notify(Command.MEASURE, arguments))
    assert isinstance(payload, MeasureNotifyPayload)
    assert payload.is_on is True
    assert payload.power == 1500
    assert payload.voltage == 230
    assert payload.current == 250
    assert payload.frequency == 50
    assert payload.consumed_energy == 1234


@pytest.mark.parametrize("arguments", [MEASURE_ARGS_12, MEASURE_ARGS_14])
def test_padding_bytes_do_not_influence_output(arguments):
    """Offsets 8-9 are padding (NOT power_factor) — varying them changes nothing."""
    baseline = NotifyPayload.from_payload(make_notify(Command.MEASURE, arguments))
    mutated = bytearray(arguments)
    mutated[8] = 0x11
    mutated[9] = 0x22
    result = NotifyPayload.from_payload(make_notify(Command.MEASURE, mutated))
    assert result == baseline


def test_overlong_frame_caps_energy_at_four_bytes():
    # A corrupt over-length frame must not read its whole tail as one huge energy
    # value (which would spike the TOTAL_INCREASING statistic).
    args = bytearray(MEASURE_ARGS_14) + bytearray([0xFF] * 6)  # 6 bytes of garbage tail
    payload = NotifyPayload.from_payload(make_notify(Command.MEASURE, args))
    assert isinstance(payload, MeasureNotifyPayload)
    assert payload.consumed_energy == 1234  # only bytes 10:14, garbage tail ignored


def test_switch_frame_parses_to_switch_payload():
    payload = NotifyPayload.from_payload(make_notify(Command.SWITCH, [0x01]))
    assert isinstance(payload, SwitchNotifyPayload)


def test_switch_build_to_from_payload_round_trip():
    frame = SwitchModes.ON.build_payload()
    assert isinstance(NotifyPayload.from_payload(frame), SwitchNotifyPayload)


@pytest.mark.parametrize(
    "payload",
    [
        bytearray(),  # empty
        bytearray([0x0F]),  # 1 byte
        bytearray([0x0F, 0x20, 0x04, 0x00, 0x00, 0xFF, 0xFF]),  # length byte overruns buffer
        bytearray([0x0F, 0x02, 0x04, 0x00, 0xFF, 0xFF]),  # body too short for a command
        make_notify(0x09, [0x00] * 12),  # unknown command byte
        make_notify(Command.MEASURE, [0x00] * 8),  # truncated MEASURE arguments (8 bytes)
        make_notify(Command.MEASURE, [0x00] * 11),  # truncated MEASURE arguments (11 bytes)
    ],
)
def test_malformed_payload_returns_none(payload):
    assert NotifyPayload.from_payload(payload) is None
