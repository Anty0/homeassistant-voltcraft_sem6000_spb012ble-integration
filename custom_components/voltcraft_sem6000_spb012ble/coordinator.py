from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace

from bleak import BleakClient, BleakGATTCharacteristic
from bleak.exc import BleakError
from homeassistant.core import HomeAssistant
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
    SwitchModes,
    SwitchNotifyPayload,
    expected_message_length,
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
    consumed_energy: float | None  # kWh (converted from Wh)

    @staticmethod
    def from_payload(
        payload: MeasureNotifyPayload,
        fallback_consumed_energy_kwh: float | None = None,
    ) -> VoltcraftData:
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

        consumed_energy = fallback_consumed_energy_kwh
        if payload.consumed_energy is not None and payload.consumed_energy > 0:
            consumed_energy = payload.consumed_energy / 1000.0  # Wh to kWh

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

        # Some notifications can arrive fragmented, especially history responses
        self._notify_buffer = bytearray()

        # Cached accumulated-consumption history from the device
        self._year_history_wh: tuple[int | None, ...] | None = None
        self._last_history_poll = 0.0
        self._history_request_in_flight = False

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, self.mac)},
            identifiers={(DOMAIN, self.mac)},
            name=self._device_name or DEVICE_NAME,
        )

    async def async_setup(self) -> None:
        await self.client.start_notify(NOTIFY_UUID, self._handle_notify)

    async def async_shutdown(self) -> None:
        try:
            await self.client.stop_notify(NOTIFY_UUID)
        except BleakError as err:
            _LOGGER.debug("Error stopping notifications: %s", err)

        try:
            await self.client.disconnect()
        except BleakError as err:
            _LOGGER.debug("Error disconnecting client: %s", err)

    def _history_total_kwh(self) -> float | None:
        if not self._year_history_wh:
            return None

        values = [value for value in self._year_history_wh if value is not None]
        if not values:
            return None

        return sum(values) / 1000.0  # Wh to kWh

    async def _request_year_history(self) -> None:
        self._history_request_in_flight = True

        # Small delay after MEASURE request to reduce collisions between requests
        await asyncio.sleep(0.1)

        await self.client.write_gatt_char(
            COMMAND_UUID,
            Command.CONSUMPTION_YEAR.build_payload(bytearray([0x00, 0x00])),
        )

    async def _async_update_data(self) -> VoltcraftData | None:
        """Fetch data from the device.

        This sends a measure command and returns the latest data. The actual data update
        happens asynchronously via a notification handler.
        """

        try:
            await self.client.write_gatt_char(COMMAND_UUID, Command.MEASURE.build_payload())

            # Periodically refresh accumulated device history for a persistent
            # Total Energy value on devices where MEASURE does not provide one
            now = time.monotonic()
            if now - self._last_history_poll >= 300 and not self._history_request_in_flight:
                self._last_history_poll = now
                await self._request_year_history()

        except BleakError as err:
            raise UpdateFailed(f"Failed to send measure command: {err}") from err

        return self._latest_data

    async def _handle_notify(self, sender: BleakGATTCharacteristic, data: bytearray) -> None:
        """Handle notifications from the device."""

        _LOGGER.debug("Received notification: %s", data.hex())
        self._notify_buffer.extend(data)

        while True:
            expected = expected_message_length(self._notify_buffer)
            if expected is None:
                if self._notify_buffer:
                    _LOGGER.debug("Dropping invalid notification fragment: %s", self._notify_buffer.hex())
                    self._notify_buffer.clear()
                return

            if len(self._notify_buffer) < expected:
                return

            frame = bytearray(self._notify_buffer[:expected])
            del self._notify_buffer[:expected]

            payload = NotifyPayload.from_payload(frame)

            match payload:
                case MeasureNotifyPayload():
                    self._latest_data = VoltcraftData.from_payload(
                        payload,
                        fallback_consumed_energy_kwh=self._history_total_kwh(),
                    )
                    self.async_set_updated_data(self._latest_data)

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

                case SwitchNotifyPayload():
                    # Switch state changed, trigger immediate measure to update data
                    self.hass.create_task(self.async_request_refresh())

                case None:
                    self._history_request_in_flight = False
                    _LOGGER.warning("Unknown payload received: %s", frame.hex())

    async def async_send_switch_command(self, mode: SwitchModes) -> None:
        """Send a switch command to the device."""
        try:
            await self.client.write_gatt_char(COMMAND_UUID, mode.build_payload())
        except BleakError as err:
            _LOGGER.error("Failed to send switch command: %s", err)
            raise
