from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import onboarding
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_MAC
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_PIN, DEFAULT_PIN, DEVICE_NAME, DOMAIN, SERVICE_UUID


class VoltcraftConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: dict[str, str] = {}
        self._mac_address: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._mac_address = discovery_info.address
        self._name = discovery_info.name
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None or not onboarding.async_is_onboarded(self.hass):
            return self._create_entry()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm", description_placeholders={"name": self._name}
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input[CONF_MAC]
            await self.async_set_unique_id(format_mac(address), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._mac_address = address
            self._name = self._discovered_devices[address]
            return await self.async_step_confirm()

        current_ids = self._async_current_ids()
        for discovery in async_discovered_service_info(self.hass):
            if (
                discovery.address not in current_ids
                and discovery.address not in self._discovered_devices
                and SERVICE_UUID in discovery.service_uuids
            ):
                self._discovered_devices[discovery.address] = (
                    f"{discovery.name} ({discovery.address})"
                )
        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_MAC): vol.In(self._discovered_devices)}
            ),
        )

    @staticmethod
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return VoltcraftOptionsFlow()

    @property
    def _name(self) -> str:
        return self.context.get("title_placeholders", {}).get("name") or DEVICE_NAME

    @_name.setter
    def _name(self, value: str | None) -> None:
        self.context["title_placeholders"] = {"name": value or DEVICE_NAME}

    def _create_entry(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._name,
            data={CONF_MAC: self._mac_address, CONF_PIN: DEFAULT_PIN},
        )


class VoltcraftOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current_pin = self.config_entry.options.get(
            CONF_PIN, self.config_entry.data.get(CONF_PIN, DEFAULT_PIN)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PIN, default=current_pin): vol.All(
                        str, vol.Match(r"^\d{4}$")
                    )
                }
            ),
        )
