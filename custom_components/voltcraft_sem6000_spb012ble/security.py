"""Security and privacy helpers for the Voltcraft integration."""

from __future__ import annotations

import logging
import re
from typing import Any

_COORDINATOR_LOGGER = "custom_components.voltcraft_sem6000_spb012ble.coordinator"
_HEX_SEQUENCE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8,}(?![0-9A-Fa-f])")
_FILTER_MARKER = "_voltcraft_sensitive_log_filter_installed"


def _hex_length(value: Any) -> int:
    if isinstance(value, str):
        compact = value.strip()
        if len(compact) % 2 == 0:
            try:
                bytes.fromhex(compact)
            except ValueError:
                pass
            else:
                return len(compact) // 2
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    return 0


def _safe_error(value: Any) -> str:
    return _HEX_SEQUENCE.sub("<redacted>", str(value))


class SensitiveBleLogFilter(logging.Filter):
    """Remove complete BLE frames from normal Home Assistant logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != _COORDINATOR_LOGGER or not isinstance(record.msg, str):
            return True

        args = record.args if isinstance(record.args, tuple) else ()
        if record.msg == "Received notification fragment: %s" and args:
            record.msg = "Received BLE notification fragment (%d bytes)"
            record.args = (_hex_length(args[0]),)
        elif record.msg == "Dropping stray notification data: %s" and args:
            record.msg = "Dropping stray BLE notification data (%d bytes)"
            record.args = (_hex_length(args[0]),)
        elif record.msg == "Invalid notification %s: %s" and len(args) >= 2:
            record.msg = "Invalid BLE notification (%d bytes): %s"
            record.args = (_hex_length(args[0]), _safe_error(args[1]))
        elif record.msg == "Unknown payload received: %s" and args:
            record.msg = "Unknown BLE payload received (%d bytes)"
            record.args = (_hex_length(args[0]),)
        elif record.msg == "Unexpected FFF1 device-info payload: %s" and args:
            record.msg = "Unexpected FFF1 device-info payload (%d bytes)"
            record.args = (_hex_length(args[0]),)
        return True


def install_sensitive_log_filter() -> None:
    """Install the redaction filter once for the coordinator logger."""
    logger = logging.getLogger(_COORDINATOR_LOGGER)
    if getattr(logger, _FILTER_MARKER, False):
        return
    logger.addFilter(SensitiveBleLogFilter())
    setattr(logger, _FILTER_MARKER, True)
