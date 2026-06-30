"""Tests for the optimistic switch state machine.

These drive the entity directly with a fake coordinator and a stubbed
``async_write_ha_state`` so no full Home Assistant harness is required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.voltcraft_sem6000_spb012ble.coordinator import VoltcraftData
from custom_components.voltcraft_sem6000_spb012ble.protocol import SwitchModes
from custom_components.voltcraft_sem6000_spb012ble.switch import MainSwitchEntity


def _data(is_on: bool) -> VoltcraftData:
    return VoltcraftData(
        is_on=is_on,
        power=0.0,
        voltage=230.0,
        current=0.0,
        frequency=50,
        power_factor=None,
        consumed_energy=0.0,
    )


def _make_entity(measurement_count: int = 0, data: VoltcraftData | None = None) -> MainSwitchEntity:
    coordinator = MagicMock()
    coordinator.mac = "aa:bb:cc:dd:ee:ff"
    coordinator.device_info = {}
    coordinator.measurement_count = measurement_count
    coordinator.data = data
    coordinator.async_send_switch_command = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entity = MainSwitchEntity(coordinator)
    entity.async_write_ha_state = MagicMock()
    return entity


async def test_optimistic_set_before_first_measurement():
    entity = _make_entity(measurement_count=0, data=None)
    await entity.async_turn_on()
    assert entity.is_on is True
    entity.coordinator.async_send_switch_command.assert_awaited_once_with(SwitchModes.ON)
    entity.coordinator.async_request_refresh.assert_awaited_once()


async def test_optimistic_not_set_when_command_fails():
    entity = _make_entity(measurement_count=0, data=None)
    entity.coordinator.async_send_switch_command = AsyncMock(side_effect=HomeAssistantError("ble down"))
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._optimistic_is_on is None  # no fabricated state on failure
    entity.async_write_ha_state.assert_not_called()
    entity.coordinator.async_request_refresh.assert_not_awaited()


async def test_optimistic_overrides_device_until_confirmed():
    entity = _make_entity(measurement_count=5, data=_data(False))
    await entity.async_turn_off()
    entity.coordinator.async_send_switch_command.assert_awaited_once_with(SwitchModes.OFF)
    assert entity.is_on is False  # optimistic False overrides the device value
    entity.coordinator.data = _data(True)
    assert entity.is_on is False  # still optimistic until a measurement confirms


async def test_optimistic_cleared_by_confirming_measurement():
    entity = _make_entity(measurement_count=1, data=None)
    await entity.async_turn_on()
    assert entity.is_on is True

    # A measurement confirming the requested state clears optimism immediately.
    entity.coordinator.measurement_count = 2
    entity.coordinator.data = _data(True)
    entity._handle_coordinator_update()
    assert entity._optimistic_is_on is None
    assert entity.is_on is True


async def test_optimistic_not_cleared_by_single_stale_measurement():
    """A poll already in flight at toggle time reports the pre-toggle state."""
    entity = _make_entity(measurement_count=1, data=None)
    await entity.async_turn_on()

    entity.coordinator.measurement_count = 2  # one measurement (possibly the in-flight poll)
    entity.coordinator.data = _data(False)
    entity._handle_coordinator_update()
    assert entity.is_on is True  # no ON -> OFF flicker


async def test_optimistic_cleared_after_two_measurements_when_command_failed():
    entity = _make_entity(measurement_count=1, data=None)
    await entity.async_turn_on()

    entity.coordinator.measurement_count = 3  # a definitely-post-toggle reading
    entity.coordinator.data = _data(False)
    entity._handle_coordinator_update()
    assert entity._optimistic_is_on is None
    assert entity.is_on is False  # real (failed) state surfaced


async def test_optimistic_not_cleared_on_noop_update():
    entity = _make_entity(measurement_count=1, data=None)
    await entity.async_turn_on()

    # Timeout-return-last-data: listeners notified but measurement_count unchanged.
    entity.coordinator.data = _data(False)
    entity._handle_coordinator_update()
    assert entity.is_on is True
