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
from homeassistant.helpers.device_registry import format_mac, DeviceInfo

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

    switch = MainSwitchEntity(hass, mac_address, client)
    await switch.async_setup()
    async_add_entities([switch])


class MainSwitchEntity(SwitchEntity):
    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_should_poll = False  # local_push
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, mac: str, client: BleakClient) -> None:
        self.hass: HomeAssistant = hass
        self.mac: str = mac
        self.client: BleakClient = client

        self._attr_unique_id = format_mac(self.mac)
        self._attr_name = self.client.name or DEVICE_NAME
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
        )

        self._attr_is_on = None  # Unknown at first
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
        await self._send_command(SwitchModes.ON.build_payload())

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_command(SwitchModes.OFF.build_payload())

    async def async_measure(self):
        await self._send_command(Command.MEASURE.build_payload())

    async def _send_command(self, payload: bytearray):
        await self.client.write_gatt_char(COMMAND_UUID, payload)

    async def _handle_notify(self, sender, data: bytearray):
        payload = NotifyPayload.from_payload(data)
        match payload:
            case MeasureNotifyPayload():
                self._attr_is_on = payload.is_on
                self.schedule_update_ha_state()
            case SwitchNotifyPayload():
                if self._attr_is_on is not None:
                    self._attr_is_on = True
                    self.schedule_update_ha_state()
                else:
                    _LOGGER.warning("Received SwitchNotifyPayload, but we don't know if the switch is on yet")
                    # Retry sending an initial update request
                    self.hass.create_task(self.async_measure())
            case None:
                _LOGGER.warning("Unknow payload received: %s", data.hex())
