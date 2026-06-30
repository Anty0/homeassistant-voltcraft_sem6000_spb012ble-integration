"""Tests for integration setup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_MAC
from homeassistant.exceptions import ConfigEntryNotReady

import custom_components.voltcraft_sem6000_spb012ble as integration
from custom_components.voltcraft_sem6000_spb012ble.const import DOMAIN
from custom_components.voltcraft_sem6000_spb012ble.coordinator import VoltcraftDataUpdateCoordinator

ADDRESS = "AA:BB:CC:DD:EE:FF"


async def test_setup_entry_device_not_found_raises_not_ready(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: ADDRESS})
    entry.add_to_hass(hass)
    monkeypatch.setattr(integration.bluetooth, "async_ble_device_from_address", lambda *args, **kwargs: None)
    with pytest.raises(ConfigEntryNotReady):
        await integration.async_setup_entry(hass, entry)


async def test_setup_entry_success_stores_coordinator_and_forwards(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: ADDRESS})
    entry.add_to_hass(hass)
    device = SimpleNamespace(name="Test Plug", address=ADDRESS)
    lookup = MagicMock(return_value=device)
    monkeypatch.setattr(integration.bluetooth, "async_ble_device_from_address", lookup)
    # Avoid real BLE work: the first refresh and platform forwarding are stubbed.
    monkeypatch.setattr(VoltcraftDataUpdateCoordinator, "async_config_entry_first_refresh", AsyncMock())
    forward = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", forward)

    assert await integration.async_setup_entry(hass, entry) is True
    assert isinstance(hass.data[DOMAIN][entry.entry_id], VoltcraftDataUpdateCoordinator)
    assert lookup.call_args.kwargs["connectable"] is True  # only connectable adverts
    forward.assert_awaited_once()
