from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_MAC
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_PIN, DEFAULT_PIN, DEVICE_NAME, DOMAIN, SERVICE_UUID
from .protocol import normalize_pin

_PIN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


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
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                pin = normalize_pin(user_input[CONF_PIN])
            except (KeyError, ValueError):
                errors["base"] = "invalid_pin"
            else:
                return self._create_entry(pin)

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_PIN, default=DEFAULT_PIN): _PIN_SELECTOR}
            ),
            errors=errors,
            description_placeholders={"name": self._name},
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
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VoltcraftOptionsFlow()

    @property
    def _name(self) -> str:
        return self.context.get("title_placeholders", {}).get("name") or DEVICE_NAME

    @_name.setter
    def _name(self, value: str | None) -> None:
        self.context["title_placeholders"] = {"name": value or DEVICE_NAME}

    def _create_entry(self, pin: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._name,
            data={CONF_MAC: self._mac_address, CONF_PIN: pin},
        )


class VoltcraftOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            updated_options = dict(self.config_entry.options)
            entered_pin = str(user_input.get(CONF_PIN, "")).strip()
            if entered_pin:
                try:
                    updated_options[CONF_PIN] = normalize_pin(entered_pin)
                except ValueError:
                    errors["base"] = "invalid_pin"
                else:
                    return self.async_create_entry(title="", data=updated_options)
            else:
                # A blank password field deliberately leaves the stored PIN
                # untouched and never sends it back to the browser.
                return self.async_create_entry(title="", data=updated_options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Optional(CONF_PIN): _PIN_SELECTOR}),
            errors=errors,
        )
