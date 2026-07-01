"""End-to-end PIN recovery: a wrong/unwanted PIN -> config-flow fix -> the
coordinator (rebuilt from the updated entry data, as __init__ does on reload)
reconnects and a measurement succeeds.

The HA reload machinery is exercised in unit form elsewhere; here the focus is the
cross-component seam: config-flow mutation of entry.data feeding the coordinator.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_MAC, CONF_PIN
from homeassistant.data_entry_flow import FlowResultType

from custom_components.voltcraft_sem6000_spb012ble import coordinator as coord_mod
from custom_components.voltcraft_sem6000_spb012ble.const import DOMAIN
from custom_components.voltcraft_sem6000_spb012ble.coordinator import (
    VoltcraftData,
    VoltcraftDataUpdateCoordinator,
)
from tests.ble_harness import (
    FORMATTED_MAC,
    LOGIN_FAILURE_FRAME,
    LOGIN_SUCCESS_FRAME,
    RAW_MAC,
    FakeClient,
    measure_frame,
    written_commands,
)

ADDRESS = RAW_MAC


@pytest.fixture
def ble(hass, monkeypatch):
    """A fake BLE stack: each connect yields a FakeClient with the configured frames."""
    state = {"login_frame": None, "measure_frame": None}
    clients: list[FakeClient] = []

    def _establish(*args, **kwargs):
        client = FakeClient()
        client.auto_login_frame = state["login_frame"]
        client.auto_measure_frame = state["measure_frame"]
        clients.append(client)
        return client

    device = SimpleNamespace(name="Test Plug", address=RAW_MAC)
    monkeypatch.setattr(coord_mod, "MEASURE_TIMEOUT", 0.1)
    monkeypatch.setattr(coord_mod, "LOGIN_TIMEOUT", 0.1)
    monkeypatch.setattr(coord_mod, "establish_connection", AsyncMock(side_effect=_establish))
    monkeypatch.setattr(coord_mod.bluetooth, "async_ble_device_from_address", MagicMock(return_value=device))

    return SimpleNamespace(state=state, clients=clients, device=device)


def _entry(hass, data):
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=FORMATTED_MAC)
    entry.add_to_hass(hass)
    return entry


async def _coord_from_entry(hass, ble, entry):
    """Rebuild the coordinator from entry data exactly as __init__.async_setup_entry does."""
    coord = VoltcraftDataUpdateCoordinator(hass, entry, RAW_MAC, ble.device, entry.data.get(CONF_PIN))
    await coord.async_refresh()
    return coord


async def test_reauth_then_login_succeeds(hass, ble, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    # Wrong PIN: the device rejects the login, which would trigger HA reauth.
    ble.state["login_frame"] = LOGIN_FAILURE_FRAME
    coord = await _coord_from_entry(hass, ble, entry)
    assert coord.last_update_success is False

    # User completes reauth with the correct PIN; the device now accepts it.
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "1234"})
    assert result["type"] == FlowResultType.ABORT
    assert entry.data[CONF_PIN] == "1234"

    ble.state["login_frame"] = LOGIN_SUCCESS_FRAME
    ble.state["measure_frame"] = measure_frame()
    recovered = await _coord_from_entry(hass, ble, entry)  # rebuilt on reload
    assert isinstance(recovered.data, VoltcraftData)
    assert recovered.last_update_success is True


async def test_reconfigure_clear_recovers_non_pin_device(hass, ble, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    # Device does not use PIN login: it silently ignores the 0x17 frame, so login times out.
    ble.state["login_frame"] = None
    ble.state["measure_frame"] = measure_frame()
    coord = await _coord_from_entry(hass, ble, entry)
    assert coord.last_update_success is False

    # User clears the PIN via Reconfigure.
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.ABORT
    assert CONF_PIN not in entry.data

    recovered = await _coord_from_entry(hass, ble, entry)  # rebuilt with pin=None
    assert isinstance(recovered.data, VoltcraftData)
    assert all(0x17 not in written_commands(client) for client in ble.clients[-1:])


async def test_reconfigure_change_recovers(hass, ble, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    ble.state["login_frame"] = LOGIN_FAILURE_FRAME
    coord = await _coord_from_entry(hass, ble, entry)
    assert coord.last_update_success is False

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "4321"})
    assert result["type"] == FlowResultType.ABORT
    assert entry.data[CONF_PIN] == "4321"

    ble.state["login_frame"] = LOGIN_SUCCESS_FRAME
    ble.state["measure_frame"] = measure_frame()
    recovered = await _coord_from_entry(hass, ble, entry)
    assert isinstance(recovered.data, VoltcraftData)
