"""Tests for integration setup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_MAC, CONF_PIN
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

import custom_components.voltcraft_sem6000_spb012ble as integration
from custom_components.voltcraft_sem6000_spb012ble.const import DOMAIN
from custom_components.voltcraft_sem6000_spb012ble.coordinator import VoltcraftDataUpdateCoordinator
from tests.ble_harness import FakeClient

ADDRESS = "AA:BB:CC:DD:EE:FF"


def _stub_setup(hass, monkeypatch, *, first_refresh=None):
    device = SimpleNamespace(name="Test Plug", address=ADDRESS)
    monkeypatch.setattr(integration.bluetooth, "async_ble_device_from_address", MagicMock(return_value=device))
    monkeypatch.setattr(
        VoltcraftDataUpdateCoordinator,
        "async_config_entry_first_refresh",
        first_refresh or AsyncMock(),
    )
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock(return_value=True))


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


async def test_setup_forwards_pin_and_entry(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: ADDRESS, CONF_PIN: "1234"})
    entry.add_to_hass(hass)
    _stub_setup(hass, monkeypatch)

    assert await integration.async_setup_entry(hass, entry) is True
    coord = hass.data[DOMAIN][entry.entry_id]
    assert coord._pin == "1234"
    assert coord.config_entry is entry


async def test_setup_forwards_none_pin_for_entry_without_one(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: ADDRESS})
    entry.add_to_hass(hass)
    _stub_setup(hass, monkeypatch)

    assert await integration.async_setup_entry(hass, entry) is True
    assert hass.data[DOMAIN][entry.entry_id]._pin is None


async def test_first_refresh_auth_failed_propagates_as_reauth(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    entry.add_to_hass(hass)
    _stub_setup(hass, monkeypatch, first_refresh=AsyncMock(side_effect=ConfigEntryAuthFailed("bad pin")))

    # Must surface as auth failure (reauth), NOT be masked as ConfigEntryNotReady.
    with pytest.raises(ConfigEntryAuthFailed):
        await integration.async_setup_entry(hass, entry)


async def test_unload_entry_shuts_down_cleanly(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: ADDRESS})
    entry.add_to_hass(hass)
    _stub_setup(hass, monkeypatch)
    assert await integration.async_setup_entry(hass, entry) is True

    coord = hass.data[DOMAIN][entry.entry_id]
    client = FakeClient()
    coord.client = client  # a live client so shutdown traverses stop_notify/disconnect
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True))

    assert await integration.async_unload_entry(hass, entry) is True
    assert client.stopped is True
    assert client.is_connected is False
    assert coord.client is None
