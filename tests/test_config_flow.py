"""Tests for the config flow: device picker, optional PIN, reauth and reconfigure."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_MAC, CONF_PIN
from homeassistant.data_entry_flow import FlowResultType

from custom_components.voltcraft_sem6000_spb012ble import config_flow
from custom_components.voltcraft_sem6000_spb012ble.const import DOMAIN, SERVICE_UUID

ADDRESS = "AA:BB:CC:DD:EE:FF"
NAME = "Test Plug"


async def _reach_confirm(hass, monkeypatch):
    """Drive the user step up to the confirmation form (onboarded path)."""
    discovery = SimpleNamespace(address=ADDRESS, name=NAME, service_uuids=[SERVICE_UUID])
    monkeypatch.setattr(config_flow, "async_discovered_service_info", lambda hass: [discovery])
    monkeypatch.setattr(config_flow.onboarding, "async_is_onboarded", lambda hass: True)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_MAC: ADDRESS})


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


async def test_confirm_stores_optional_pin(hass, monkeypatch):
    result = await _reach_confirm(hass, monkeypatch)
    assert result["type"] == FlowResultType.FORM
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "1234"})
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_MAC: ADDRESS, CONF_PIN: "1234"}


async def test_confirm_empty_pin_stores_none(hass, monkeypatch):
    result = await _reach_confirm(hass, monkeypatch)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_PIN not in result["data"]


async def test_confirm_invalid_pin_shows_error(hass, monkeypatch):
    result = await _reach_confirm(hass, monkeypatch)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "12"})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_pin"}


async def test_confirm_leading_zero_pin_preserved(hass, monkeypatch):
    result = await _reach_confirm(hass, monkeypatch)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "0007"})
        await hass.async_block_till_done()
    assert result["data"][CONF_PIN] == "0007"


async def test_non_onboarded_auto_creates_without_pin(hass, monkeypatch):
    # Calling the step directly returns the CREATE_ENTRY result without the flow
    # manager setting up the entry (which would pull in the bluetooth scanner).
    monkeypatch.setattr(config_flow.onboarding, "async_is_onboarded", lambda hass: False)
    flow = config_flow.MainConfigFlow()
    flow.hass = hass
    flow.context = {"title_placeholders": {"name": NAME}}
    flow._mac_address = ADDRESS
    result = await flow.async_step_confirm()  # user_input None, not onboarded -> create directly
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_MAC: ADDRESS}


def test_validate_pin_unit():
    flow = config_flow.MainConfigFlow()
    assert flow._validate_pin({}, required=False) == (None, None)
    assert flow._validate_pin({}, required=True) == (None, "invalid_pin")
    assert flow._validate_pin({CONF_PIN: ""}, required=False) == (None, None)
    assert flow._validate_pin({CONF_PIN: "    "}, required=False) == (None, "invalid_pin")
    assert flow._validate_pin({CONF_PIN: 1234}, required=False) == (None, "invalid_pin")
    assert flow._validate_pin({CONF_PIN: 1234}, required=True) == (None, "invalid_pin")
    assert flow._validate_pin({CONF_PIN: "1234"}, required=False) == ("1234", None)


def _entry(hass, data):
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id="aa:bb:cc:dd:ee:ff")
    entry.add_to_hass(hass)
    return entry


async def test_reauth_updates_pin(hass, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "1234"})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {CONF_MAC: ADDRESS, CONF_PIN: "1234"}


async def test_reauth_rejects_invalid_pin(hass, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "12"})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_pin"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "1234"})
    assert result["type"] == FlowResultType.ABORT


async def test_reconfigure_updates_pin_keeps_mac(hass, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "4321"})
    assert result["type"] == FlowResultType.ABORT
    assert entry.data == {CONF_MAC: ADDRESS, CONF_PIN: "4321"}


async def test_reconfigure_clears_pin(hass, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.ABORT
    assert entry.data == {CONF_MAC: ADDRESS}


async def test_reconfigure_adds_pin_to_entry_without_one(hass, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS})
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "5678"})
    assert result["type"] == FlowResultType.ABORT
    assert entry.data == {CONF_MAC: ADDRESS, CONF_PIN: "5678"}


async def test_reconfigure_invalid_pin_shows_error(hass, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "99"})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_pin"}


async def test_reconfigure_prefills_current_pin(hass, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    marker = next(key for key in result["data_schema"].schema if key == CONF_PIN)
    assert marker.description == {"suggested_value": "0000"}


async def test_reconfigure_dismisses_pending_reauth(hass, monkeypatch):
    entry = _entry(hass, {CONF_MAC: ADDRESS, CONF_PIN: "0000"})
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    await entry.start_reauth_flow(hass)
    assert any(f["context"]["source"] == SOURCE_REAUTH for f in hass.config_entries.flow.async_progress())

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "1234"})
    assert result["type"] == FlowResultType.ABORT
    assert not any(f["context"]["source"] == SOURCE_REAUTH for f in hass.config_entries.flow.async_progress())
