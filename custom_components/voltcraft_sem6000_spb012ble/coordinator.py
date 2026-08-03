from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace

from bleak import BleakClient, BleakGATTCharacteristic
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import COMMAND_UUID, DEVICE_NAME, DOMAIN, NOTIFY_UUID, SCAN_INTERVAL
from .protocol import (
    Command,
    ConsumptionHistoryNotifyPayload,
    HistoryKind,
    MeasureNotifyPayload,
    NotifyPayload,
    SetPowerLimitNotifyPayload,
    SettingsNotifyPayload,
    SwitchModes,
    SwitchNotifyPayload,
    expected_message_length,
)

_LOGGER = logging.getLogger(__name__)

# Keep all BLE operations shorter than the normal polling interval.
_BLE_OPERATION_TIMEOUT = 4.0
_MAX_MISSED_UPDATES = 3
_HISTORY_POLL_INTERVAL = 300.0
_HISTORY_RESPONSE_TIMEOUT = 15.0
_SETTINGS_RESPONSE_TIMEOUT = 15.0
_MIN_POWER_LIMIT_W = 1
_MAX_POWER_LIMIT_W = 3680


@dataclass
class VoltcraftData:
    """Data from Voltcraft device measurements."""

    is_on: bool
    power: float
    voltage: float
    current: float
    frequency: int
    power_factor: float | None
    consumed_energy: float | None

    @staticmethod
    def from_measure_payload(
        payload: MeasureNotifyPayload,
        fallback_consumed_energy_kwh: float | None = None,
    ) -> "VoltcraftData":
        power = payload.power / 1000.0
        voltage = float(payload.voltage)
        current = payload.current / 1000.0

        apparent_power = voltage * current
        if apparent_power > 0:
            power_factor = min(power / apparent_power, 1.0)
        else:
            power_factor = None

        consumed_energy = fallback_consumed_energy_kwh
        if payload.consumed_energy is not None and payload.consumed_energy > 0:
            consumed_energy = payload.consumed_energy / 1000.0

        return VoltcraftData(
            is_on=payload.is_on,
            power=power,
            voltage=voltage,
            current=current,
            frequency=payload.frequency,
            power_factor=power_factor,
            consumed_energy=consumed_energy,
        )


class VoltcraftDataUpdateCoordinator(DataUpdateCoordinator[VoltcraftData | None]):
    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        mac: str,
        device_name: str | None,
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
        self._device_name = device_name
        self.client: BleakClient | None = None
        self._latest_data: VoltcraftData | None = None

        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._notify_lock = asyncio.Lock()
        self._measure_condition = asyncio.Condition()
        self._measurement_count = 0
        self._missed_updates = 0

        self._power_limit_condition = asyncio.Condition()
        self._power_limit_ack_count = 0
        self._last_power_limit_set_success: bool | None = None

        self._notify_buffer = bytearray()
        self._year_history_wh: tuple[int | None, ...] | None = None
        self._last_history_poll = 0.0
        self._history_request_in_flight = False

        self._power_limit_w: int | None = None
        self._settings_request_in_flight = False
        self._last_settings_request = 0.0

    @property
    def power_limit_w(self) -> int | None:
        """Return the power limit stored by the device."""

        return self._power_limit_w

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, self.mac)},
            identifiers={(DOMAIN, self.mac)},
            name=self._device_name or DEVICE_NAME,
        )

    async def async_shutdown(self) -> None:
        await super().async_shutdown()

        client = self.client
        self.client = None
        self._history_request_in_flight = False
        self._settings_request_in_flight = False
        self._notify_buffer.clear()

        if client is None:
            return

        try:
            async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                await client.stop_notify(NOTIFY_UUID)
        except (TimeoutError, BleakError) as err:
            _LOGGER.debug("Error stopping notifications: %s", err)

        await self._async_safe_disconnect(client, "shutdown")

    def _history_total_kwh(self) -> float | None:
        if not self._year_history_wh:
            return None

        values = [value for value in self._year_history_wh if value is not None]
        if not values:
            return None

        return sum(values) / 1000.0

    async def _request_year_history(self, client: BleakClient) -> None:
        self._history_request_in_flight = True
        self._last_history_poll = time.monotonic()

        try:
            # Small delay after MEASURE to reduce collisions between responses.
            await asyncio.sleep(0.1)
            async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                async with self._operation_lock:
                    await client.write_gatt_char(
                        COMMAND_UUID,
                        Command.CONSUMPTION_YEAR.build_payload(
                            bytearray([0x00, 0x00])
                        ),
                    )
        except (TimeoutError, BleakError):
            self._history_request_in_flight = False
            raise


    async def _request_settings(self, client: BleakClient) -> None:
        self._settings_request_in_flight = True
        self._last_settings_request = time.monotonic()

        try:
            await asyncio.sleep(0.1)
            async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                async with self._operation_lock:
                    await client.write_gatt_char(
                        COMMAND_UUID,
                        Command.REQUEST_SETTINGS.build_payload(
                            bytearray([0x00, 0x00])
                        ),
                    )
        except (TimeoutError, BleakError):
            self._settings_request_in_flight = False
            raise

    async def _async_update_data(self) -> VoltcraftData | None:
        """Fetch a fresh measurement and periodically refresh energy history."""

        client = await self._async_ensure_connected()
        start_count = self._measurement_count

        try:
            async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                async with self._operation_lock:
                    await client.write_gatt_char(
                        COMMAND_UUID,
                        Command.MEASURE.build_payload(),
                    )

                async with self._measure_condition:
                    await self._measure_condition.wait_for(
                        lambda: self._measurement_count != start_count
                    )
        except (TimeoutError, BleakError) as err:
            self._missed_updates += 1

            if self.data is None or self._missed_updates >= _MAX_MISSED_UPDATES:
                await self._async_teardown()
                raise UpdateFailed(f"No measurement received: {err}") from err

            # Preserve the last valid reading for two isolated missed responses.
            return self.data

        now = time.monotonic()
        if (
            self._settings_request_in_flight
            and now - self._last_settings_request >= _SETTINGS_RESPONSE_TIMEOUT
        ):
            _LOGGER.debug("Timed out waiting for settings response")
            self._settings_request_in_flight = False

        if (
            self._history_request_in_flight
            and now - self._last_history_poll >= _HISTORY_RESPONSE_TIMEOUT
        ):
            _LOGGER.debug("Timed out waiting for consumption-history response")
            self._history_request_in_flight = False

        # Send only one auxiliary request per update to avoid overlapping replies.
        if self._power_limit_w is None and not self._settings_request_in_flight:
            try:
                await self._request_settings(client)
            except (TimeoutError, BleakError) as err:
                _LOGGER.debug("Failed to request device settings: %s", err)
                await self._async_teardown()
        elif (
            now - self._last_history_poll >= _HISTORY_POLL_INTERVAL
            and not self._history_request_in_flight
        ):
            try:
                await self._request_year_history(client)
            except (TimeoutError, BleakError) as err:
                # The live measurement is valid. Drop the suspect connection and
                # reconnect on the next poll without discarding that measurement.
                _LOGGER.debug("Failed to request consumption history: %s", err)
                await self._async_teardown()

        return self.data

    async def _handle_notify(
        self,
        sender: BleakGATTCharacteristic,
        data: bytearray,
    ) -> None:
        """Handle complete or fragmented notifications from the device."""

        _LOGGER.debug("Received notification fragment: %s", data.hex())

        async with self._notify_lock:
            self._notify_buffer.extend(data)

            while True:
                if len(self._notify_buffer) < 2:
                    return

                if self._notify_buffer[0] != 0x0F:
                    next_frame = self._notify_buffer.find(0x0F, 1)
                    if next_frame == -1:
                        _LOGGER.debug(
                            "Dropping stray notification data: %s",
                            self._notify_buffer.hex(),
                        )
                        self._notify_buffer.clear()
                        return

                    _LOGGER.debug(
                        "Dropping stray notification prefix: %s",
                        self._notify_buffer[:next_frame].hex(),
                    )
                    del self._notify_buffer[:next_frame]
                    continue

                expected = expected_message_length(self._notify_buffer)
                if expected is None or len(self._notify_buffer) < expected:
                    return

                frame = bytearray(self._notify_buffer[:expected])
                del self._notify_buffer[:expected]

                try:
                    payload = NotifyPayload.from_payload(frame)
                except ValueError as err:
                    self._history_request_in_flight = False
                    self._settings_request_in_flight = False
                    _LOGGER.warning(
                        "Invalid notification payload %s: %s",
                        frame.hex(),
                        err,
                    )
                    continue

                match payload:
                    case MeasureNotifyPayload():
                        self._missed_updates = 0
                        self._measurement_count += 1
                        self._latest_data = VoltcraftData.from_measure_payload(
                            payload,
                            fallback_consumed_energy_kwh=self._history_total_kwh(),
                        )
                        self.async_set_updated_data(self._latest_data)

                        async with self._measure_condition:
                            self._measure_condition.notify_all()

                    case ConsumptionHistoryNotifyPayload(kind=HistoryKind.YEAR):
                        self._history_request_in_flight = False
                        self._year_history_wh = payload.values_wh

                        if self._latest_data is not None:
                            self._latest_data = replace(
                                self._latest_data,
                                consumed_energy=self._history_total_kwh(),
                            )
                            self.async_set_updated_data(self._latest_data)

                    case ConsumptionHistoryNotifyPayload():
                        self._history_request_in_flight = False


                    case SettingsNotifyPayload():
                        self._settings_request_in_flight = False
                        self._power_limit_w = payload.power_limit_w
                        self.async_update_listeners()

                    case SetPowerLimitNotifyPayload():
                        self._last_power_limit_set_success = payload.success
                        self._power_limit_ack_count += 1
                        async with self._power_limit_condition:
                            self._power_limit_condition.notify_all()

                    case SwitchNotifyPayload():
                        self.hass.async_create_task(self.async_request_refresh())

                    case None:
                        self._history_request_in_flight = False
                        self._settings_request_in_flight = False
                        _LOGGER.warning("Unknown payload received: %s", frame.hex())


    async def async_set_power_limit(self, power_limit_w: int) -> None:
        """Set the persistent device-side overpower threshold."""

        if not _MIN_POWER_LIMIT_W <= power_limit_w <= _MAX_POWER_LIMIT_W:
            raise HomeAssistantError(
                f"Power limit must be between {_MIN_POWER_LIMIT_W} and "
                f"{_MAX_POWER_LIMIT_W} W"
            )

        start_count = self._power_limit_ack_count
        self._last_power_limit_set_success = None

        try:
            client = await self._async_ensure_connected()
            params = bytearray(power_limit_w.to_bytes(2, byteorder="big"))
            params.extend([0x00, 0x00])

            async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                async with self._operation_lock:
                    await client.write_gatt_char(
                        COMMAND_UUID,
                        Command.SET_POWER_LIMIT.build_payload(params),
                    )

                async with self._power_limit_condition:
                    await self._power_limit_condition.wait_for(
                        lambda: self._power_limit_ack_count != start_count
                    )

            if not self._last_power_limit_set_success:
                raise HomeAssistantError("Device rejected the power limit")

            self._power_limit_w = power_limit_w
            self.async_update_listeners()
        except HomeAssistantError:
            raise
        except (TimeoutError, BleakError, UpdateFailed) as err:
            await self._async_teardown()
            _LOGGER.error("Failed to set power limit: %s", err)
            raise HomeAssistantError(f"Failed to set power limit: {err}") from err

    async def async_send_switch_command(self, mode: SwitchModes) -> None:
        """Send a switch command to the device."""

        try:
            client = await self._async_ensure_connected()
            async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                async with self._operation_lock:
                    await client.write_gatt_char(
                        COMMAND_UUID,
                        mode.build_payload(),
                    )
        except (TimeoutError, BleakError, UpdateFailed) as err:
            await self._async_teardown()
            _LOGGER.error("Failed to send switch command: %s", err)
            raise HomeAssistantError(
                f"Failed to send switch command: {err}"
            ) from err

    async def _async_ensure_connected(self) -> BleakClient:
        client = self.client
        if client is not None and client.is_connected:
            return client

        async with self._connect_lock:
            client = self.client
            if client is not None and client.is_connected:
                return client

            if client is not None:
                await self._async_safe_disconnect(client, "replace stale client")
                if self.client is client:
                    self.client = None

            ble_device = bluetooth.async_ble_device_from_address(
                self.hass,
                self._mac_address,
                connectable=True,
            )
            if ble_device is None:
                raise UpdateFailed(f"Device {self._mac_address} not found")

            new_client: BleakClient | None = None
            try:
                new_client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self.name,
                )
                self._notify_buffer.clear()
                async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                    await new_client.start_notify(NOTIFY_UUID, self._handle_notify)
            except (TimeoutError, BleakError) as err:
                if new_client is not None:
                    await self._async_safe_disconnect(
                        new_client,
                        "after failed connect",
                    )
                raise UpdateFailed(
                    f"Failed to connect to {self._mac_address}: {err}"
                ) from err

            self.client = new_client
            self._missed_updates = 0
            self._history_request_in_flight = False
            self._last_history_poll = 0.0
            self._settings_request_in_flight = False
            self._power_limit_w = None
            return new_client

    async def _async_teardown(self) -> None:
        """Drop the connection so the next update creates a fresh one."""

        client = self.client
        if client is None:
            return

        await self._async_safe_disconnect(client, "teardown")

        # Do not erase a client installed by a concurrent reconnect.
        if self.client is client:
            self.client = None

        self._history_request_in_flight = False
        self._settings_request_in_flight = False
        self._power_limit_w = None
        self._notify_buffer.clear()

    async def _async_safe_disconnect(
        self,
        client: BleakClient,
        context: str,
    ) -> None:
        try:
            async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                await client.disconnect()
        except (TimeoutError, BleakError) as err:
            _LOGGER.debug("Error disconnecting client (%s): %s", context, err)
