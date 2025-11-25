import logging
from typing import Any

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.const import CONF_MAC
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import format_mac, DeviceInfo, CONNECTION_BLUETOOTH

from .const import DOMAIN, DEVICE_NAME, COMMAND_UUID, NOTIFY_UUID
from .protocol import Command, SwitchModes, NotifyPayload, SwitchNotifyPayload, MeasureNotifyPayload

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    mac_address = entry.data[CONF_MAC]
    ble_device = bluetooth.async_ble_device_from_address(hass, mac_address)
    if not ble_device:
        _LOGGER.warning("Device not found at address %s", mac_address)
        return

    client = await establish_connection(
        BleakClient,
        ble_device,
        entry.entry_id,
    )

    switch = MainSwitchEntity(mac_address, ble_device.name, client)
    await switch.async_setup()
    async_add_entities([switch])


class MainSwitchEntity(SwitchEntity):
    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_should_poll = False  # local_push
    _attr_has_entity_name = True

    def __init__(self, mac: str, device_name: str | None, client: BleakClient) -> None:
        self.mac: str = mac
        self.client: BleakClient = client

        self._attr_unique_id = format_mac(self.mac)
        self._attr_name = device_name or DEVICE_NAME
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, self.mac)},
            identifiers={(DOMAIN, self.mac)},
            name=device_name or DEVICE_NAME,
        )

        self._attr_is_on: bool | None = None  # Unknown at first
        self._attr_is_on_next: bool | None = None
        self._attr_available = True

    async def async_setup(self) -> None:
        await self.client.start_notify(NOTIFY_UUID, self._handle_notify)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Request initial update
        self.hass.create_task(self.async_measure())

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        await self.client.stop_notify(NOTIFY_UUID)
        await self.client.disconnect()

    async def async_update(self) -> None:
        # Request update
        await self.async_measure()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._warn_about_missing_notify()
        self._attr_is_on_next = True
        await self._send_command(SwitchModes.ON.build_payload())

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._warn_about_missing_notify()
        self._attr_is_on_next = False
        await self._send_command(SwitchModes.OFF.build_payload())

    async def async_measure(self) -> None:
        await self._send_command(Command.MEASURE.build_payload())

    async def _send_command(self, payload: bytearray) -> None:
        await self.client.write_gatt_char(COMMAND_UUID, payload)

    async def _handle_notify(self, sender: Any, data: bytearray) -> None:
        payload = NotifyPayload.from_payload(data)
        match payload:
            case MeasureNotifyPayload():
                self._attr_is_on = payload.is_on
                self.schedule_update_ha_state()
            case SwitchNotifyPayload():
                self._attr_is_on = self._attr_is_on_next
                self._attr_is_on_next = None
                self.schedule_update_ha_state()

                if self._attr_is_on is None:
                    _LOGGER.warning("Lost track of switch state, requesting initial update")
                    # Retry sending an initial update request
                    self.hass.create_task(self.async_measure())
            case None:
                _LOGGER.warning("Unknow payload received: %s", data.hex())

    def _warn_about_missing_notify(self) -> None:
        if self._attr_is_on_next is None:
            return
        _LOGGER.warning("Didn't receive confirmation of last command. Switch state may get out of sync.")
