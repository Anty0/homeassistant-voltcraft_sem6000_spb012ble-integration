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
    LoginNotifyPayload,
    MeasureNotifyPayload,
    NotifyPayload,
    SwitchModes,
    SwitchNotifyPayload,
    LoginCommand,
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


def test_build_login_payload_zero_pin_matches_reference_frame():
    # Byte-identical to the community-reverse-engineered static "0000" login frame.
    assert LoginCommand.build_payload("0000") == bytearray.fromhex("0f0c170000000000000000000018ffff")


def test_build_login_payload_encodes_digits_one_byte_each():
    payload = LoginCommand.build_payload("1234")
    # frame = [0x0F, len, cmd, 0x00, *params, checksum, 0xFF, 0xFF];
    # params = [0x00] + digit bytes + [0,0,0,0], so digit bytes for "1234" sit at [5:9].
    assert payload[5:9] == bytearray([0x01, 0x02, 0x03, 0x04])
    # Round-trips through the shared framing: header, length, command, checksum, footer.
    assert payload[0] == 0x0F and payload[1] == 0x0C and payload[2] == Command.LOGIN
    assert payload[-2:] == bytearray([0xFF, 0xFF])


@pytest.mark.parametrize("pin", ["", "123", "12345", "12a4", " 12 ", "12.4", "١٢٣٤", "１２３４"])
def test_normalize_pin_and_build_reject_invalid(pin):
    with pytest.raises(ValueError):
        LoginCommand.normalize_pin(pin)
    with pytest.raises(ValueError):
        LoginCommand.build_payload(pin)


def test_normalize_pin_strips_and_returns_value():
    assert LoginCommand.normalize_pin(" 1234 ") == "1234"
    assert LoginCommand.normalize_pin("0000") == "0000"


def test_login_response_frames_parse_to_status():
    success = NotifyPayload.from_payload(bytearray.fromhex("0f06170000000018ffff"))
    assert isinstance(success, LoginNotifyPayload)
    assert success.was_successful is True

    failure = NotifyPayload.from_payload(bytearray.fromhex("0f06170001000018ffff"))
    assert isinstance(failure, LoginNotifyPayload)
    assert failure.was_successful is False


def test_truncated_login_frame_returns_none():
    # Valid framing but no status byte: arguments are empty after the command/separator
    # split, so LoginNotifyPayload.from_data hits its len(data) < 1 guard.
    assert NotifyPayload.from_payload(bytearray([0x0F, 0x03, Command.LOGIN, 0x00, 0x00, 0xFF, 0xFF])) is None
