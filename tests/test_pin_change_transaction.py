from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "voltcraft_sem6000_spb012ble" / "coordinator.py"


def _load_protocol() -> Any:
    path = ROOT / "custom_components" / "voltcraft_sem6000_spb012ble" / "protocol.py"
    spec = importlib.util.spec_from_file_location("voltcraft_protocol_pin_test", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load protocol.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class FakeHomeAssistantError(Exception):
    """Stand-in for Home Assistant's user-facing service error."""


def _class_node() -> ast.ClassDef:
    tree = ast.parse(COORDINATOR.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "VoltcraftDataUpdateCoordinator":
            return node
    raise AssertionError("VoltcraftDataUpdateCoordinator was not found")


def _method_source(name: str) -> str:
    source = COORDINATOR.read_text(encoding="utf-8")
    for node in _class_node().body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment is None:
                break
            return textwrap.dedent(segment)
    raise AssertionError(f"Method {name} was not found")


def _load_method(name: str, globals_: dict[str, Any]) -> Any:
    namespace = dict(globals_)
    exec("from __future__ import annotations\n" + _method_source(name), namespace)
    return namespace[name]


def _normalize_pin(pin: str) -> str:
    if not isinstance(pin, str) or len(pin) != 4 or not pin.isdigit():
        raise ValueError("PIN must contain exactly four digits")
    return pin


class _FakeConfigEntries:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def async_update_entry(self, entry: SimpleNamespace, *, options: dict[str, Any]) -> None:
        entry.options = options
        self.updates.append(options)


class _Harness:
    _store_verified_pin = _load_method(
        "_store_verified_pin",
        {"normalize_pin": _normalize_pin, "CONF_PIN": "pin"},
    )
    _async_verify_and_store_pin = _load_method(
        "_async_verify_and_store_pin",
        {
            "HomeAssistantError": FakeHomeAssistantError,
            "_PIN_CHANGE_SETTLE_DELAY": 3.0,
        },
    )

    def __init__(self, outcomes: dict[str, list[bool]]) -> None:
        self._pin = "0000"
        self.config_entry = SimpleNamespace(options={"pin": "0000", "other": True})
        self.hass = SimpleNamespace(config_entries=_FakeConfigEntries())
        self.outcomes = {pin: list(values) for pin, values in outcomes.items()}
        self.verification_calls: list[tuple[str, float]] = []

    async def _async_verify_pin(self, pin: str, *, settle_delay: float = 0.0) -> bool:
        self.verification_calls.append((pin, settle_delay))
        values = self.outcomes.get(pin, [])
        return values.pop(0) if values else False

    async def _async_teardown(self) -> None:
        return None


class PinChangeTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_pin_is_saved_only_after_successful_login(self) -> None:
        coordinator = _Harness({"1234": [True]})

        await coordinator._async_verify_and_store_pin(
            new_pin="1234",
            previous_pin="0000",
            operation_name="PIN change",
        )

        self.assertEqual(coordinator._pin, "1234")
        self.assertEqual(coordinator.config_entry.options["pin"], "1234")
        self.assertTrue(coordinator.config_entry.options["other"])
        self.assertEqual(coordinator.hass.config_entries.updates, [coordinator.config_entry.options])
        self.assertEqual(coordinator.verification_calls, [("1234", 3.0)])

    async def test_old_pin_remains_when_new_pin_is_rejected(self) -> None:
        coordinator = _Harness({"1234": [False], "0000": [True]})

        with self.assertRaisesRegex(FakeHomeAssistantError, "previous PIN remains stored"):
            await coordinator._async_verify_and_store_pin(
                new_pin="1234",
                previous_pin="0000",
                operation_name="PIN change",
            )

        self.assertEqual(coordinator._pin, "0000")
        self.assertEqual(coordinator.config_entry.options["pin"], "0000")
        self.assertEqual(coordinator.hass.config_entries.updates, [])
        self.assertEqual(
            coordinator.verification_calls,
            [("1234", 3.0), ("0000", 0.0)],
        )

    async def test_stored_pin_is_unchanged_when_neither_pin_can_be_verified(self) -> None:
        coordinator = _Harness({"1234": [False], "0000": [False]})

        with self.assertRaisesRegex(FakeHomeAssistantError, "stored PIN was not changed"):
            await coordinator._async_verify_and_store_pin(
                new_pin="1234",
                previous_pin="0000",
                operation_name="PIN change",
            )

        self.assertEqual(coordinator._pin, "0000")
        self.assertEqual(coordinator.config_entry.options["pin"], "0000")
        self.assertEqual(coordinator.hass.config_entries.updates, [])


class PinChangeStructureTests(unittest.TestCase):
    def test_change_pin_frame_contains_new_pin_then_current_pin(self) -> None:
        protocol = _load_protocol()
        frame = protocol.Commands.change_pin("1234", "5678")
        self.assertEqual(frame[2], protocol.Command.LOGIN)
        self.assertEqual(frame[3], 0)
        self.assertEqual(
            bytes(frame[4:13]),
            bytes([protocol.PinOperation.CHANGE, 5, 6, 7, 8, 1, 2, 3, 4]),
        )

    def test_change_pin_does_not_persist_directly(self) -> None:
        source = _method_source("async_change_pin")
        self.assertIn("Commands.change_pin(previous_pin, new_pin)", source)
        self.assertIn("await self._async_verify_and_store_pin(", source)
        self.assertNotIn("async_update_entry", source)
        self.assertNotIn("self._pin = new_pin", source)

    def test_verification_uses_fresh_login_with_candidate_pin(self) -> None:
        source = _method_source("_async_verify_pin")
        self.assertIn("await self._async_teardown()", source)
        self.assertIn("await self._async_ensure_connected(pin=candidate)", source)

        login_source = _method_source("_async_login")
        self.assertIn("Commands.login(login_pin)", login_source)

        connect_source = _method_source("_async_ensure_connected")
        self.assertGreaterEqual(connect_source.count("pin=login_pin"), 2)

    def test_pin_is_persisted_only_in_verified_storage_helper(self) -> None:
        source = _method_source("_async_verify_and_store_pin")
        verify_position = source.index("await self._async_verify_pin(")
        store_position = source.index("self._store_verified_pin(new_pin)")
        self.assertLess(verify_position, store_position)

    def test_ble_payloads_are_not_written_to_logs(self) -> None:
        source = COORDINATOR.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"debug", "info", "warning", "error", "exception"}:
                continue
            call_source = ast.get_source_segment(source, node) or ""
            self.assertNotIn(".hex()", call_source)


if __name__ == "__main__":
    unittest.main()
