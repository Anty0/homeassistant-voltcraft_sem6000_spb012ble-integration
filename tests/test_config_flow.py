"""Tests for the config flow entry title and picker label."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.const import CONF_MAC
from homeassistant.data_entry_flow import FlowResultType

from custom_components.voltcraft_sem6000_spb012ble import config_flow
from custom_components.voltcraft_sem6000_spb012ble.const import DOMAIN, SERVICE_UUID

ADDRESS = "AA:BB:CC:DD:EE:FF"
NAME = "Test Plug"


async def test_user_flow_title_is_bare_name(hass, monkeypatch):
    discovery = SimpleNamespace(address=ADDRESS, name=NAME, service_uuids=[SERVICE_UUID])
    monkeypatch.setattr(config_flow, "async_discovered_service_info", lambda hass: [discovery])

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM

    # The picker keeps the address so identically-named plugs stay distinguishable.
    container = result["data_schema"].schema[CONF_MAC].container
    assert any(ADDRESS in label for label in container.values())

    # Stub entry setup so finishing the flow does not pull in the bluetooth
    # dependency (which needs a real adapter); the test only checks flow output.
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_MAC: ADDRESS})
        if result["type"] == FlowResultType.FORM:
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME


async def test_user_flow_aborts_when_no_devices(hass, monkeypatch):
    monkeypatch.setattr(config_flow, "async_discovered_service_info", lambda hass: [])
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
