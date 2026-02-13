from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bleak import BleakClient, BleakGATTCharacteristic
from bleak.exc import BleakError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import COMMAND_UUID, DEVICE_NAME, DOMAIN, NOTIFY_UUID, SCAN_INTERVAL
from .protocol import (
    Command,
    MeasureNotifyPayload,
    NotifyPayload,
    SwitchModes,
    SwitchNotifyPayload,
    LoginMode,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class VoltcraftData:
    """Data from Voltcraft device measurements."""

    is_on: bool
    power: float  # Watts (converted from mW)
    voltage: float  # Volts
    current: float  # Amps (converted from mA)
    frequency: int  # Hz
    power_factor: float | None  # 0.0 - 1.0, calculated from P/(V*I)
    consumed_energy: float  # kWh (converted from Wh)

    @staticmethod
    def from_payload(payload: MeasureNotifyPayload) -> VoltcraftData:
        power = payload.power / 1000.0  # mW to W
        voltage = float(payload.voltage)
        current = payload.current / 1000.0  # mA to A

        # Power factor - calculate from P / (V * I)
        apparent_power = voltage * current
        power_factor: float | None
        if apparent_power > 0:
            power_factor = min(power / apparent_power, 1.0)
        else:
            power_factor = None

        return VoltcraftData(
            is_on=payload.is_on,
            power=power,
            voltage=voltage,
            current=current,
            frequency=payload.frequency,
            power_factor=power_factor,
            consumed_energy=payload.consumed_energy / 1000.0,  # Wh to kWh
        )


class VoltcraftDataUpdateCoordinator(DataUpdateCoordinator[VoltcraftData | None]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: BleakClient,
        mac: str,
        device_name: str | None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{mac}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.mac = format_mac(mac)
        self._device_name = device_name
        self._latest_data: VoltcraftData | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, self.mac)},
            identifiers={(DOMAIN, self.mac)},
            name=self._device_name or DEVICE_NAME,
        )

    async def async_setup(self) -> None:
        await self.client.start_notify(NOTIFY_UUID, self._handle_notify)
        # login required for some firmware versions
        await asyncio.sleep(2.0)
        await self.client.write_gatt_char(COMMAND_UUID, LoginMode.build_payload())

    async def async_shutdown(self) -> None:
        try:
            await self.client.stop_notify(NOTIFY_UUID)
        except BleakError as err:
            _LOGGER.debug("Error stopping notifications: %s", err)

        try:
            await self.client.disconnect()
        except BleakError as err:
            _LOGGER.debug("Error disconnecting client: %s", err)

    async def _async_update_data(self) -> VoltcraftData | None:
        """Fetch data from the device.

        This sends a measure command and returns the latest data.
        The actual data update happens asynchronously via a notification handler.
        """
        try:
            # reconnect if connection was lost
            if not self.client.is_connected:
                await self.client.connect()
                await self.client.start_notify(NOTIFY_UUID, self._handle_notify)
                await self.client.write_gatt_char(COMMAND_UUID, LoginMode.build_payload())
                self._latest_data = None

            async with asyncio.timeout(5.0):
                await self.client.write_gatt_char(COMMAND_UUID, Command.MEASURE.build_payload())
                # wait for notification to be processed
                await asyncio.sleep(0.5)
        except Exception as err:
            # force disconnect on error to ensure clean state for next poll
            try:
                await self.client.disconnect()
            except:
                pass
            raise UpdateFailed(f"Failed to send measure command: {err}") from err

        return self._latest_data

    async def _handle_notify(self, sender: BleakGATTCharacteristic, data: bytearray) -> None:
        """Handle notifications from the device."""
        _LOGGER.debug("Received notification: %s", data.hex())
        
        # ignore login confirmation payloads
        if len(data) > 2 and data[2] == Command.LOGIN:
            return

        payload = NotifyPayload.from_payload(data)

        match payload:
            case MeasureNotifyPayload():
                self._latest_data = VoltcraftData.from_payload(payload)
                self.async_set_updated_data(self._latest_data)

            case SwitchNotifyPayload():
                # Switch state changed, trigger immediate measure to update data
                self.hass.create_task(self.async_request_refresh())

            case None:
                _LOGGER.warning("Unknown payload received: %s", data.hex())

    async def async_send_switch_command(self, mode: SwitchModes) -> None:
        """Send a switch command to the device."""
        try:
            await self.client.write_gatt_char(COMMAND_UUID, mode.build_payload())
        except BleakError as err:
            _LOGGER.error("Failed to send switch command: %s", err)
            raise
