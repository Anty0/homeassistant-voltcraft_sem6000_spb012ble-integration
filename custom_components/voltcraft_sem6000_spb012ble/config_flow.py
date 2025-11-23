from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_MAC
from homeassistant.helpers.device_registry import format_mac
from homeassistant.components import onboarding
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN, DEVICE_NAME, SERVICE_UUID

_LOGGER = logging.getLogger(__name__)


class MainConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
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
        if user_input is not None or not onboarding.async_is_onboarded(self.hass):
            return self._create_entry()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._name},
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
                    vol.Required(CONF_MAC): vol.In(self._discovered_devices),
                }
            ),
        )

    @property
    def _name(self) -> str:
        return self.context["title_placeholders"]["name"] or DEVICE_NAME

    @_name.setter
    def _name(self, name: str) -> None:
        self.context["title_placeholders"] = {"name": name}

    def _create_entry(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._name,
            data={CONF_MAC: self._mac_address},
        )
