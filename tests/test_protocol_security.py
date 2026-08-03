from __future__ import annotations

import importlib.util
import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "voltcraft_sem6000_spb012ble"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


protocol = _load_module("voltcraft_protocol_test", COMPONENT / "protocol.py")
security = _load_module("voltcraft_security_test", COMPONENT / "security.py")


class ProtocolSecurityTests(unittest.TestCase):
    def test_standard_checksum_is_accepted(self) -> None:
        frame = protocol.build_frame(
            protocol.Command.SET_POWER_PROTECTION, 0, b"\x00\x00"
        )
        self.assertEqual(protocol.validate_notify_frame(frame), bytes(frame))

    def test_modified_standard_checksum_is_rejected(self) -> None:
        frame = protocol.build_frame(protocol.Command.SET_POWER_LIMIT, 0, b"\x03\xe8")
        frame[-3] ^= 0x01
        with self.assertRaisesRegex(ValueError, "checksum"):
            protocol.parse_notify_payload(frame)

    def test_hardware_three_measurement_checksum_is_accepted(self) -> None:
        frame = bytes.fromhex("0f0f04000100f67fe401e03201000001f17ce1")
        payload = protocol.parse_notify_payload(frame)
        self.assertIsInstance(payload, protocol.MeasureNotifyPayload)

    def test_hardware_two_measurement_checksum_is_accepted(self) -> None:
        # Hardware 2 uses length byte 0x11 but still sends a 19-byte frame.
        frame = bytearray.fromhex("0f0f04000100f67fe401e03201000001f17ce1")
        frame[1] = 0x11
        frame[-1] = (1 + sum(frame[2:-1])) & 0xFF
        payload = protocol.parse_notify_payload(frame)
        self.assertIsInstance(payload, protocol.MeasureNotifyPayload)

    def test_modified_measurement_checksum_is_rejected(self) -> None:
        frame = bytearray.fromhex("0f0f04000100f67fe401e03201000001f17ce1")
        frame[-1] ^= 0x01
        with self.assertRaisesRegex(ValueError, "measurement checksum"):
            protocol.parse_notify_payload(frame)

    def test_observed_app_finalize_response_is_accepted(self) -> None:
        frame = bytes.fromhex("0f080700000007ffff")
        payload = protocol.parse_notify_payload(frame)
        self.assertIsInstance(payload, protocol.AckNotifyPayload)
        self.assertTrue(payload.was_successful)

    def test_modified_app_finalize_checksum_is_rejected(self) -> None:
        frame = bytearray.fromhex("0f080700000007ffff")
        frame[4] = 1
        with self.assertRaisesRegex(ValueError, "finalization response checksum"):
            protocol.parse_notify_payload(frame)

    def test_observed_settings_response_is_accepted(self) -> None:
        frame = bytes.fromhex("0f0e1000001eff00000078010000640bffff")
        payload = protocol.parse_notify_payload(frame)
        self.assertIsInstance(payload, protocol.SettingsNotifyPayload)

    def test_observed_power_protection_ack_is_accepted(self) -> None:
        frame = bytes.fromhex("0f0406000007ffff")
        payload = protocol.parse_notify_payload(frame)
        self.assertIsInstance(payload, protocol.AckNotifyPayload)
        self.assertTrue(payload.was_successful)
        self.assertEqual(protocol.response_key(payload), (protocol.Command.SET_POWER_PROTECTION, 0))

    def test_pin_change_response_is_correlated_to_change_operation(self) -> None:
        frame = protocol.build_frame(
            protocol.Command.LOGIN,
            0,
            bytes([0, protocol.PinOperation.CHANGE]),
        )
        payload = protocol.parse_notify_payload(frame)
        self.assertIsInstance(payload, protocol.LoginNotifyPayload)
        self.assertTrue(payload.was_successful)
        self.assertEqual(payload.operation, protocol.PinOperation.CHANGE)
        self.assertEqual(
            protocol.response_key(payload),
            (protocol.Command.LOGIN, protocol.PinOperation.CHANGE),
        )

    def test_invalid_standard_suffix_is_rejected(self) -> None:
        frame = protocol.build_frame(protocol.Command.GET_TIMER, 0, b"\0\0")
        frame[-1] = 0
        with self.assertRaisesRegex(ValueError, "suffix"):
            protocol.parse_notify_payload(frame)

    def test_invalid_random_time_is_rejected_cleanly(self) -> None:
        frame = protocol.build_frame(
            protocol.Command.GET_RANDOM,
            0,
            bytes([1, 0x7F, 25, 0, 12, 0]),
        )
        with self.assertRaisesRegex(ValueError, "random-mode time"):
            protocol.parse_notify_payload(frame)

    def test_invalid_schedule_time_is_skipped(self) -> None:
        entry = bytes(
            [
                0,  # slot
                1,  # active
                1,  # turn on
                0x02,  # weekday mask
                26,  # year
                8,  # month
                4,  # day
                25,  # invalid hour
                0,  # minute
                0,
                0,
                0,
            ]
        )
        frame = protocol.build_frame(
            protocol.Command.GET_SCHEDULE, 0, bytes([1]) + entry
        )
        payload = protocol.parse_notify_payload(frame)
        self.assertIsInstance(payload, protocol.ScheduleStatusNotifyPayload)
        self.assertEqual(payload.entries, ())

    def test_pin_change_frame_uses_numeric_digits_and_valid_checksum(self) -> None:
        frame = protocol.Commands.change_pin("0000", "1234")
        self.assertEqual(protocol.validate_notify_frame(frame), bytes(frame))
        self.assertEqual(frame[2], protocol.Command.LOGIN)
        self.assertEqual(frame[4], protocol.PinOperation.CHANGE)
        self.assertEqual(bytes(frame[5:9]), b"\x01\x02\x03\x04")
        self.assertEqual(bytes(frame[9:13]), b"\x00\x00\x00\x00")

    def test_pin_validation_rejects_non_decimal_input(self) -> None:
        for value in ("123", "12345", "12a4", "１２３４"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                protocol.normalize_pin(value)


class SensitiveLogFilterTests(unittest.TestCase):
    def test_notification_hex_is_replaced_with_length(self) -> None:
        record = logging.LogRecord(
            name="custom_components.voltcraft_sem6000_spb012ble.coordinator",
            level=logging.DEBUG,
            pathname=__file__,
            lineno=1,
            msg="Received notification fragment: %s",
            args=("0f0406000007ffff",),
            exc_info=None,
        )
        self.assertTrue(security.SensitiveBleLogFilter().filter(record))
        self.assertEqual(
            record.getMessage(), "Received BLE notification fragment (8 bytes)"
        )
        self.assertNotIn("0f0406000007ffff", record.getMessage())

    def test_error_hex_is_redacted(self) -> None:
        record = logging.LogRecord(
            name="custom_components.voltcraft_sem6000_spb012ble.coordinator",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Invalid notification %s: %s",
            args=("0f0406000007ffff", "bad payload deadbeefcafebabe"),
            exc_info=None,
        )
        self.assertTrue(security.SensitiveBleLogFilter().filter(record))
        message = record.getMessage()
        self.assertIn("8 bytes", message)
        self.assertNotIn("deadbeefcafebabe", message)
        self.assertNotIn("0f0406000007ffff", message)


if __name__ == "__main__":
    unittest.main()
