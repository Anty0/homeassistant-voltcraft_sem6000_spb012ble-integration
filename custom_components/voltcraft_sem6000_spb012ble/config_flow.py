from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_MAC, CONF_PIN
from homeassistant.helpers.device_registry import format_mac
from homeassistant.components import onboarding
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult

from .const import DOMAIN, DEVICE_NAME, SERVICE_UUID
from .protocol import LoginCommand

_LOGGER = logging.getLogger(__name__)


class MainConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._discovered_devices: dict[str, str] = {}
        self._mac_address: str | None = None

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        device_unique_id = format_mac(discovery_info.address)
        await self.async_set_unique_id(device_unique_id)
        self._abort_if_unique_id_configured()
        self._mac_address = discovery_info.address
        self._name = discovery_info.name
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None and not onboarding.async_is_onboarded(self.hass):
            return self._create_entry(None)

        errors: dict[str, str] = {}
        if user_input is not None:
            pin, error = self._validate_pin(user_input, required=False)
            if error:
                errors["base"] = error
            else:
                return self._create_entry(pin)

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Optional(CONF_PIN): str}),
            description_placeholders={"name": self._name},
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            mac_address = user_input[CONF_MAC]
            device_unique_id = format_mac(mac_address)
            await self.async_set_unique_id(device_unique_id, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            name = self._discovered_devices[mac_address]
            self._name = name
            self._mac_address = mac_address

            return await self.async_step_confirm()

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue

            if SERVICE_UUID in discovery_info.service_uuids:
                self._discovered_devices[address] = discovery_info.name

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC): vol.In(
                        {address: f"{name} ({address})" for address, name in self._discovered_devices.items()}
                    ),
                }
            ),
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            pin, error = self._validate_pin(user_input, required=True)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates={CONF_PIN: pin})

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PIN): str}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            pin, error = self._validate_pin(user_input, required=False)
            if error:
                errors["base"] = error
            else:
                new_data = dict(entry.data)
                if pin is None:
                    new_data.pop(CONF_PIN, None)
                else:
                    new_data[CONF_PIN] = pin
                for flow in entry.async_get_active_flows(self.hass, {SOURCE_REAUTH}):
                    self.hass.config_entries.flow.async_abort(flow["flow_id"])
                return self.async_update_reload_and_abort(entry, data=new_data)

        current_pin = entry.data.get(CONF_PIN)
        schema = vol.Schema({vol.Optional(CONF_PIN): str})
        if current_pin:
            schema = self.add_suggested_values_to_schema(schema, {CONF_PIN: current_pin})
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @property
    def _name(self) -> str:
        return self.context["title_placeholders"]["name"] or DEVICE_NAME

    @_name.setter
    def _name(self, name: str) -> None:
        self.context["title_placeholders"] = {"name": name}

    def _create_entry(self, pin: str | None) -> ConfigFlowResult:
        data: dict[str, Any] = {CONF_MAC: self._mac_address}
        if pin is not None:
            data[CONF_PIN] = pin
        return self.async_create_entry(title=self._name, data=data)

    def _validate_pin(self, user_input: dict[str, Any], *, required: bool) -> tuple[str | None, str | None]:
        value = user_input.get(CONF_PIN)
        if not value:
            return (None, "invalid_pin") if required else (None, None)
        if not isinstance(value, str):
            return None, "invalid_pin"
        try:
            return LoginCommand.normalize_pin(value), None
        except ValueError:
            return None, "invalid_pin"
