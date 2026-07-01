from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bleak import BleakClient, BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    COMMAND_UUID,
    DEVICE_NAME,
    DOMAIN,
    LOGIN_TIMEOUT,
    MAX_MISSED_UPDATES,
    MEASURE_TIMEOUT,
    NOTIFY_UUID,
    SCAN_INTERVAL,
)
from .protocol import (
    Command,
    LoginNotifyPayload,
    MeasureNotifyPayload,
    NotifyPayload,
    SwitchModes,
    SwitchNotifyPayload,
    LoginCommand,
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
        config_entry: ConfigEntry,
        mac: str,
        ble_device: BLEDevice,
        pin: str | None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{mac}",
            update_interval=SCAN_INTERVAL,
        )
        self._mac_address = mac
        self.mac = format_mac(mac)
        self._device_name = ble_device.name
        self._pin = pin
        self.client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._measure_cond = asyncio.Condition()
        self._login_cond = asyncio.Condition()
        self._login_count = 0
        self._login_result: bool | None = None
        self._login_warning_logged = False
        self._missed_updates = 0
        self.measurement_count = 0

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, self.mac)},
            identifiers={(DOMAIN, self.mac)},
            name=self._device_name or DEVICE_NAME,
        )

    async def _async_update_data(self) -> VoltcraftData | None:
        """Send a measure command and return the response.

        Awaits the notification triggered by the command so that a device which
        stops responding is detected and entities are marked unavailable.
        """
        client = await self._async_ensure_connected()
        start_count = self.measurement_count

        try:
            async with asyncio.timeout(MEASURE_TIMEOUT):
                async with self._operation_lock:
                    await client.write_gatt_char(COMMAND_UUID, Command.MEASURE.build_payload())
                async with self._measure_cond:
                    await self._measure_cond.wait_for(lambda: self.measurement_count != start_count)
        except (TimeoutError, BleakError) as err:
            self._missed_updates += 1
            if self.data is None or self._missed_updates >= MAX_MISSED_UPDATES:
                await self._async_teardown()
                raise UpdateFailed(f"No measurement received: {err}") from err

        return self.data

    async def _handle_notify(self, sender: BleakGATTCharacteristic, data: bytearray) -> None:
        """Handle notifications from the device."""
        _LOGGER.debug("Received notification: %s", data.hex())
        payload = NotifyPayload.from_payload(data)

        match payload:
            case MeasureNotifyPayload():
                self._missed_updates = 0
                self.measurement_count += 1
                self.async_set_updated_data(VoltcraftData.from_payload(payload))
                async with self._measure_cond:
                    self._measure_cond.notify_all()

            case SwitchNotifyPayload():
                # Switch state changed, trigger immediate measure to update data
                self.hass.async_create_task(self.async_request_refresh())

            case LoginNotifyPayload():
                async with self._login_cond:
                    self._login_result = payload.was_successful
                    self._login_count += 1
                    self._login_cond.notify_all()

            case None:
                _LOGGER.warning("Unknown payload received: %s", data.hex())

    async def async_shutdown(self) -> None:
        await super().async_shutdown()

        if self.client is None:
            return

        try:
            await self.client.stop_notify(NOTIFY_UUID)
        except BleakError as err:
            _LOGGER.debug("Error stopping notifications: %s", err)

        await self._async_safe_disconnect(self.client, "shutdown")
        self.client = None

    async def async_send_switch_command(self, mode: SwitchModes) -> None:
        """Send a switch command to the device."""
        try:
            client = await self._async_ensure_connected()
            async with asyncio.timeout(MEASURE_TIMEOUT):
                async with self._operation_lock:
                    await client.write_gatt_char(COMMAND_UUID, mode.build_payload())
        except (TimeoutError, BleakError, UpdateFailed, ConfigEntryAuthFailed) as err:
            _LOGGER.error("Failed to send switch command: %s", err)
            raise HomeAssistantError(f"Failed to send switch command: {err}") from err

    async def _async_teardown(self) -> None:
        """Drop the connection so the next update re-establishes a fresh one."""
        client = self.client
        if client is None:
            return
        await self._async_safe_disconnect(client, "teardown")
        # A concurrent reconnect may have replaced the client during the await;
        # only clear the field if it still points at the one we tore down.
        if self.client is client:
            self.client = None

    async def _async_ensure_connected(self) -> BleakClient:
        if self.client is not None and self.client.is_connected:
            return self.client

        async with self._connect_lock:
            if self.client is not None and self.client.is_connected:
                return self.client

            ble_device = bluetooth.async_ble_device_from_address(self.hass, self._mac_address, connectable=True)
            if ble_device is None:
                raise UpdateFailed(f"Device {self._mac_address} not found")

            client: BleakClient | None = None
            try:
                client = await establish_connection(BleakClient, ble_device, self.name)
                # start_notify has no internal timeout and can hang
                async with asyncio.timeout(MEASURE_TIMEOUT):
                    await client.start_notify(NOTIFY_UUID, self._handle_notify)
                await self._async_login(client)
            except (TimeoutError, BleakError) as err:
                if client is not None:
                    await self._async_safe_disconnect(client, "after failed connect")
                raise UpdateFailed(f"Failed to connect to {self._mac_address}: {err}") from err
            except ConfigEntryAuthFailed:
                if client is not None:
                    await self._async_safe_disconnect(client, "after failed login")
                raise

            self.client = client
            self._missed_updates = 0
            return client

    async def _async_login(self, client: BleakClient) -> None:
        """Authenticate the session when a PIN is configured (firmware that requires it)."""
        if self._pin is None:
            return

        try:
            payload = LoginCommand.build_payload(self._pin)
        except ValueError as err:
            raise ConfigEntryAuthFailed("Invalid stored PIN") from err

        start = self._login_count
        try:
            async with asyncio.timeout(LOGIN_TIMEOUT):
                async with self._operation_lock:
                    await client.write_gatt_char(COMMAND_UUID, payload)
                async with self._login_cond:
                    await self._login_cond.wait_for(lambda: self._login_count != start)
                    success = self._login_result
        except TimeoutError:
            if not self._login_warning_logged:
                _LOGGER.warning(
                    "Login to %s timed out; the configured PIN may be wrong or the device may not use a PIN",
                    self._mac_address,
                )
                self._login_warning_logged = True
            raise

        if not success:
            raise ConfigEntryAuthFailed("Invalid PIN")
        self._login_warning_logged = False

    async def _async_safe_disconnect(self, client: BleakClient, context: str) -> None:
        try:
            await client.disconnect()
        except BleakError as err:
            _LOGGER.debug("Error disconnecting client (%s): %s", context, err)
