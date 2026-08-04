from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, time as dt_time, timedelta
from typing import Any
from bleak import BleakClient, BleakGATTCharacteristic
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from .const import (
    COMMAND_UUID,
    CONF_PIN,
    DEFAULT_PIN,
    DEVICE_NAME,
    DEVICE_INFO_UUID,
    DEVICE_NAME_UUID,
    DOMAIN,
    NOTIFY_UUID,
    SCAN_INTERVAL,
)
from .protocol import (
    AckNotifyPayload,
    Command,
    Commands,
    ConsumptionHistoryNotifyPayload,
    HistoryKind,
    LoginNotifyPayload,
    MeasureNotifyPayload,
    ParsedNotifyPayload,
    PinOperation,
    RandomStatusNotifyPayload,
    ScheduleEntry,
    ScheduleOperation,
    ScheduleStatusNotifyPayload,
    SerialNotifyPayload,
    SettingsNotifyPayload,
    SettingsOperation,
    SwitchModes,
    TimerAction,
    TimerStatusNotifyPayload,
    expected_message_length,
    normalize_pin,
    parse_notify_payload,
    response_key,
    weekday_mask,
)
_LOGGER = logging.getLogger(__name__)

_BLE_OPERATION_TIMEOUT = 5.0
_CONNECT_TIMEOUT = 15.0
_NOTIFY_TIMEOUT = 10.0
_LOGIN_TIMEOUT = 3.0
_COMMAND_RESPONSE_TIMEOUT = 4.0
_RECONNECT_COOLDOWN = 5.0
_MAX_MISSED_UPDATES = 3
_HISTORY_POLL_INTERVAL = 300.0
_HISTORY_RESPONSE_TIMEOUT = 15.0
_PIN_CHANGE_SETTLE_DELAY = 3.0
_PIN_VERIFY_ATTEMPTS = 3
_PIN_VERIFY_RETRY_DELAY = 2.0

@dataclass(frozen=True)
class VoltcraftData:
    is_on: bool | None = None
    power: float | None = None
    voltage: float | None = None
    current: float | None = None
    frequency: int | None = None
    power_factor: float | None = None
    consumed_energy: float | None = None
    history_24h_wh: tuple[int | None, ...] = ()
    history_30d_wh: tuple[int | None, ...] = ()
    history_12m_wh: tuple[int | None, ...] = ()
    device_name: str | None = None
    serial: str | None = None
    vendor: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    night_mode: bool | None = None
    power_protection_enabled: bool | None = None
    power_limit_watts: int | None = None
    normal_tariff: float | None = None
    reduced_tariff: float | None = None
    reduced_tariff_enabled: bool | None = None
    reduced_tariff_start: dt_time | None = None
    reduced_tariff_end: dt_time | None = None

    random_enabled: bool | None = None
    random_weekday_mask: int | None = None
    random_start: dt_time | None = None
    random_end: dt_time | None = None
    timer_action: TimerAction = TimerAction.INACTIVE
    timer_target: datetime | None = None
    timer_original_runtime_seconds: int = 0

    schedules: tuple[ScheduleEntry, ...] = ()

    session_transport: str | None = None
    app_cccd_handshake_applied: bool | None = None
    app_initialization_complete: bool = False
    app_finalize_succeeded: bool | None = None
    att_mtu: int | None = None
    last_connected_at: datetime | None = None

class VoltcraftDataUpdateCoordinator(DataUpdateCoordinator[VoltcraftData | None]):
    """Maintain one authenticated persistent BLE session with a SEM6000."""
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
        self.config_entry = config_entry
        self._mac_address = mac
        self.mac = format_mac(mac)
        self._device_name = device_name
        self._pin = normalize_pin(
            config_entry.options.get(
                CONF_PIN, config_entry.data.get(CONF_PIN, DEFAULT_PIN)
            )
        )
        self.client: BleakClient | None = None
        self._command_char: BleakGATTCharacteristic | None = None
        self._notify_char: BleakGATTCharacteristic | None = None
        self._session_ready = False
        self._notify_started = False
        self._passive_notify_backend: Any | None = None
        self._passive_notify_path: str | None = None
        self._notification_tasks: set[asyncio.Task[None]] = set()
        self._shutting_down = False
        self._next_connect_at = 0.0
        self._reconnect_task: asyncio.Task[None] | None = None
        self._extended_refresh_task: asyncio.Task[None] | None = None
        self._latest_data = VoltcraftData(device_name=device_name)
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._credential_lock = asyncio.Lock()
        self._notify_lock = asyncio.Lock()
        self._response_condition = asyncio.Condition()
        self._response_counts: dict[tuple[int, int], int] = {}
        self._last_responses: dict[tuple[int, int], ParsedNotifyPayload] = {}
        self._missed_updates = 0
        self._notify_buffer = bytearray()
        self._year_history_wh: tuple[int | None, ...] | None = None
        self._last_history_poll = 0.0
        self._history_request_in_flight = False

        config_entry.async_on_unload(
            bluetooth.async_register_callback(
                hass,
                self._handle_bluetooth_event,
                {"address": mac, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
    @property
    def pin(self) -> str:
        return self._pin

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, self.mac)},
            identifiers={(DOMAIN, self.mac)},
            name=(self.data.device_name if self.data else None)
            or self._device_name
            or DEVICE_NAME,
            manufacturer="Voltcraft",
            model="SEM6000 / SPB012BLE",
        )
    @callback
    def _handle_bluetooth_event(self, service_info, change) -> None:
        if self._shutting_down or self._session_ready:
            return
        if time.monotonic() < self._next_connect_at:
            return
        self._schedule_reconnect(0.0)
    @callback
    def _handle_disconnected(self, client: BleakClient) -> None:
        if self._shutting_down or self.client is not client:
            return
        _LOGGER.debug("Voltcraft %s disconnected", self._mac_address)
        self.client = None
        self._reset_session_state()
        self._next_connect_at = time.monotonic() + 1.0
        self._schedule_reconnect(1.0)
    @callback
    def _schedule_reconnect(self, delay: float) -> None:
        task = self._reconnect_task
        if task is not None and not task.done():
            return
        self._reconnect_task = self.hass.async_create_task(
            self._async_delayed_refresh(delay)
        )
    async def _async_delayed_refresh(self, delay: float) -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            if not self._shutting_down:
                await self.async_request_refresh()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Deferred Voltcraft refresh failed: %s", err)
        finally:
            self._reconnect_task = None
    async def async_shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        await super().async_shutdown()

        for task_name in ("_reconnect_task", "_extended_refresh_task"):
            task = getattr(self, task_name)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                setattr(self, task_name, None)
        async with self._connect_lock:
            client = self.client
            notify_char = self._notify_char
            notify_started = self._notify_started
            self.client = None
            self._reset_session_state()
            if client is None:
                return
            if notify_started and notify_char is not None and client.is_connected:
                try:
                    async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                        await client.stop_notify(notify_char)
                except (TimeoutError, BleakError) as err:
                    _LOGGER.debug("Error stopping notifications: %s", err)
            for task in tuple(self._notification_tasks):
                task.cancel()
            self._notification_tasks.clear()
            await self._async_safe_disconnect(client, "shutdown")
    def _reset_session_state(self) -> None:
        self._session_ready = False
        self._notify_started = False
        self._remove_passive_notification_listener()
        self._command_char = None
        self._notify_char = None
        self._history_request_in_flight = False
        self._notify_buffer.clear()
        task = self._extended_refresh_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._extended_refresh_task = None
    def _update_data(self, **changes: Any) -> None:
        self._latest_data = replace(self._latest_data, **changes)
        self.async_set_updated_data(self._latest_data)

    def _history_total_kwh(self) -> float | None:
        if not self._year_history_wh:
            return None
        values = [value for value in self._year_history_wh if value is not None]
        return sum(values) / 1000.0 if values else None
    def _command_characteristic(self, client: BleakClient) -> BleakGATTCharacteristic:
        if (
            self.client is not client
            or not self._session_ready
            or self._command_char is None
        ):
            raise BleakError("Voltcraft BLE session is not ready")
        return self._command_char
    async def _publish_response(self, payload: ParsedNotifyPayload) -> None:
        key = response_key(payload)
        if key is None:
            return
        self._last_responses[key] = payload
        self._response_counts[key] = self._response_counts.get(key, 0) + 1
        async with self._response_condition:
            self._response_condition.notify_all()
    async def _write_and_wait(
        self,
        client: BleakClient,
        command_char: BleakGATTCharacteristic,
        frame: bytes | bytearray,
        key: tuple[int, int],
        timeout: float = _COMMAND_RESPONSE_TIMEOUT,
    ) -> ParsedNotifyPayload:
        async with self._operation_lock:
            start_count = self._response_counts.get(key, 0)
            async with asyncio.timeout(timeout):
                await client.write_gatt_char(command_char, frame, response=False)
                async with self._response_condition:
                    await self._response_condition.wait_for(
                        lambda: self._response_counts.get(key, 0) != start_count
                    )
            return self._last_responses[key]
    async def _send_and_wait(
        self,
        frame: bytes | bytearray,
        key: tuple[int, int],
        timeout: float = _COMMAND_RESPONSE_TIMEOUT,
    ) -> ParsedNotifyPayload:
        client = await self._async_ensure_connected()
        return await self._write_and_wait(
            client, self._command_characteristic(client), frame, key, timeout
        )
    async def _request_year_history(self, client: BleakClient) -> None:
        self._history_request_in_flight = True
        self._last_history_poll = time.monotonic()
        try:
            await asyncio.sleep(0.1)
            command_char = self._command_characteristic(client)
            async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                async with self._operation_lock:
                    await client.write_gatt_char(
                        command_char,
                        Commands.request_history(HistoryKind.YEAR),
                        response=False,
                    )
        except (TimeoutError, BleakError):
            self._history_request_in_flight = False
            raise
    async def _async_update_data(self) -> VoltcraftData | None:
        client = await self._async_ensure_connected()
        try:
            await self._write_and_wait(
                client,
                self._command_characteristic(client),
                Commands.measure(),
                (Command.MEASURE, 0),
                _BLE_OPERATION_TIMEOUT,
            )
            self._missed_updates = 0
        except (TimeoutError, BleakError) as err:
            self._missed_updates += 1
            if self.data is None or self._missed_updates >= _MAX_MISSED_UPDATES:
                await self._async_teardown()
                raise UpdateFailed(f"No measurement received: {err}") from err
            return self.data
        now = time.monotonic()
        if (
            self._history_request_in_flight
            and now - self._last_history_poll >= _HISTORY_RESPONSE_TIMEOUT
        ):
            self._history_request_in_flight = False
        if (
            now - self._last_history_poll >= _HISTORY_POLL_INTERVAL
            and not self._history_request_in_flight
        ):
            try:
                await self._request_year_history(client)
            except (TimeoutError, BleakError) as err:
                _LOGGER.debug("Failed to request consumption history: %s", err)
        return self._latest_data
    async def _handle_notify(
        self, sender: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        _LOGGER.debug("Received BLE notification fragment (%s bytes)", len(data))
        async with self._notify_lock:
            self._notify_buffer.extend(data)
            while True:
                if len(self._notify_buffer) < 2:
                    return
                if self._notify_buffer[0] != 0x0F:
                    next_frame = self._notify_buffer.find(0x0F, 1)
                    if next_frame == -1:
                        _LOGGER.debug(
                            "Dropping stray BLE notification data (%s bytes)",
                            len(self._notify_buffer),
                        )
                        self._notify_buffer.clear()
                        return
                    del self._notify_buffer[:next_frame]
                    continue
                expected = expected_message_length(self._notify_buffer)
                if expected is None or len(self._notify_buffer) < expected:
                    return
                frame = bytearray(self._notify_buffer[:expected])
                del self._notify_buffer[:expected]
                try:
                    payload = parse_notify_payload(frame)
                except ValueError as err:
                    self._history_request_in_flight = False
                    _LOGGER.warning("Invalid BLE notification (%s bytes): %s", len(frame), err)
                    continue
                if payload is None:
                    _LOGGER.warning("Unknown BLE notification (%s bytes)", len(frame))
                    continue
                if isinstance(payload, MeasureNotifyPayload):
                    power = payload.power / 1000.0
                    voltage = float(payload.voltage)
                    current = payload.current / 1000.0
                    apparent = voltage * current
                    consumed = self._history_total_kwh()
                    if consumed is None and payload.consumed_energy and payload.consumed_energy > 0:
                        consumed = payload.consumed_energy / 1000.0
                    self._update_data(
                        is_on=payload.is_on,
                        power=power,
                        voltage=voltage,
                        current=current,
                        frequency=payload.frequency,
                        power_factor=min(power / apparent, 1.0) if apparent else None,
                        consumed_energy=consumed,
                    )
                elif isinstance(payload, ConsumptionHistoryNotifyPayload):
                    self._history_request_in_flight = False
                    if payload.kind is HistoryKind.DAY:
                        self._update_data(history_24h_wh=payload.values_wh)
                    elif payload.kind is HistoryKind.MONTH:
                        self._update_data(history_30d_wh=payload.values_wh)
                    elif payload.kind is HistoryKind.YEAR:
                        self._year_history_wh = payload.values_wh
                        self._update_data(
                            history_12m_wh=payload.values_wh,
                            consumed_energy=self._history_total_kwh(),
                        )
                elif isinstance(payload, SettingsNotifyPayload):
                    changes: dict[str, Any] = {
                        "night_mode": payload.night_mode,
                        "power_limit_watts": payload.power_limit_watts,
                        "normal_tariff": payload.normal_price_cents / 100.0,
                        "reduced_tariff": payload.reduced_price_cents / 100.0,
                        "reduced_tariff_enabled": payload.reduced_tariff_enabled,
                        "reduced_tariff_start": payload.reduced_start,
                        "reduced_tariff_end": payload.reduced_end,
                    }
                    # The settings response does not expose the protection state
                    # on this firmware. Do not overwrite a command-confirmed
                    # value with a guessed false value.
                    if payload.power_protection_enabled is not None:
                        changes["power_protection_enabled"] = (
                            payload.power_protection_enabled
                        )
                    self._update_data(**changes)
                elif isinstance(payload, RandomStatusNotifyPayload):
                    self._update_data(
                        random_enabled=payload.enabled,
                        random_weekday_mask=payload.weekday_mask,
                        random_start=payload.start,
                        random_end=payload.end,
                    )
                elif isinstance(payload, TimerStatusNotifyPayload):
                    self._update_data(
                        timer_action=payload.action,
                        timer_target=payload.target,
                        timer_original_runtime_seconds=payload.original_runtime_seconds,
                    )
                elif isinstance(payload, ScheduleStatusNotifyPayload):
                    self._update_data(schedules=payload.entries)
                elif isinstance(payload, SerialNotifyPayload):
                    self._update_data(serial=payload.serial)
                elif isinstance(payload, AckNotifyPayload):
                    if payload.command == Command.SWITCH and payload.was_successful:
                        self.hass.async_create_task(self.async_request_refresh())
                await self._publish_response(payload)
    def _install_passive_notification_listener(
        self, client: BleakClient, notify_char: BleakGATTCharacteristic
    ) -> bool:
        backend = getattr(client, "_backend", None)
        callbacks = getattr(backend, "_notification_callbacks", None)
        char_obj = getattr(notify_char, "obj", None)
        if not isinstance(callbacks, dict) or not isinstance(char_obj, tuple):
            return False
        char_path = char_obj[0]
        if not isinstance(char_path, str):
            return False
        def _dispatch(data: bytearray) -> None:
            task = self.hass.async_create_task(
                self._handle_notify(notify_char, bytearray(data))
            )
            self._notification_tasks.add(task)
            task.add_done_callback(self._notification_tasks.discard)
        callbacks[char_path] = _dispatch
        self._passive_notify_backend = backend
        self._passive_notify_path = char_path
        _LOGGER.debug("Using passive BlueZ notification listener for FFF4")
        return True
    def _remove_passive_notification_listener(self) -> None:
        backend = self._passive_notify_backend
        char_path = self._passive_notify_path
        self._passive_notify_backend = None
        self._passive_notify_path = None
        if backend is None or char_path is None:
            return
        callbacks = getattr(backend, "_notification_callbacks", None)
        if isinstance(callbacks, dict):
            callbacks.pop(char_path, None)
    async def _async_app_cccd_handshake(
        self, client: BleakClient, notify_char: BleakGATTCharacteristic
    ) -> bool:
        """Reproduce the app's FFF4 CCCD Write Request with value 0000.

        Bleak's public API intentionally blocks direct CCCD writes.  On the
        local BlueZ backend we use the same private D-Bus bus that Bleak uses,
        with a request write, while retaining the passive Value-change listener.
        Failure is non-fatal.
        """
        descriptor = next(
            (
                item
                for item in notify_char.descriptors
                if item.uuid.lower() == "00002902-0000-1000-8000-00805f9b34fb"
            ),
            None,
        )
        backend = getattr(client, "_backend", None)
        bus = getattr(backend, "_bus", None)
        obj = getattr(descriptor, "obj", None) if descriptor is not None else None
        if bus is None or not isinstance(obj, tuple) or not obj:
            return False
        try:
            from bleak.backends.bluezdbus import defs
            from bleak.backends.bluezdbus.utils import assert_gatt_reply
            from dbus_fast.message import Message
            reply = await bus.call(
                Message(
                    destination=defs.BLUEZ_SERVICE,
                    path=obj[0],
                    interface=defs.GATT_DESCRIPTOR_INTERFACE,
                    member="WriteValue",
                    signature="aya{sv}",
                    # GattDescriptor1.WriteValue has no characteristic-style
                    # write type option.  An empty options dictionary issues
                    # the ATT descriptor write request used by the app.
                    body=[bytes(b"\x00\x00"), {}],
                )
            )
            assert_gatt_reply(reply)
            _LOGGER.debug("Applied official-app FFF4 CCCD handshake (0000)")
            return True
        except Exception as err:
            _LOGGER.debug("Official-app CCCD handshake was not available: %s", err)
            return False
    async def _async_login(
        self,
        client: BleakClient,
        command_char: BleakGATTCharacteristic,
        timeout: float = _LOGIN_TIMEOUT,
        *,
        pin: str | None = None,
    ) -> None:
        login_pin = self._pin if pin is None else normalize_pin(pin)
        response = await self._write_and_wait(
            client,
            command_char,
            Commands.login(login_pin),
            (Command.LOGIN, PinOperation.AUTHORIZE),
            timeout,
        )
        if not isinstance(response, LoginNotifyPayload) or not response.was_successful:
            raise BleakError("Voltcraft login with configured PIN was rejected")
    async def _async_read_device_identity(self, client: BleakClient) -> None:
        try:
            name_char = client.services.get_characteristic(DEVICE_NAME_UUID)
            if name_char is not None:
                async with self._operation_lock:
                    async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                        raw_name = await client.read_gatt_char(name_char)
                name = bytes(raw_name).rstrip(b"\0").decode("utf-8", errors="replace")
                if name:
                    self._device_name = name
                    self._update_data(device_name=name)
        except Exception as err:
            _LOGGER.debug("Reading device name failed: %s", err)
        try:
            info_char = client.services.get_characteristic(DEVICE_INFO_UUID)
            if info_char is not None:
                async with self._operation_lock:
                    async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                        raw_info = bytes(await client.read_gatt_char(info_char))
                if len(raw_info) >= 15:
                    vendor = raw_info[:6].rstrip(b"\0").decode("ascii", errors="replace")
                    self._update_data(
                        vendor=vendor or None,
                        firmware_version=f"{raw_info[11]}.{raw_info[12]}",
                        hardware_version=f"{raw_info[13]}.{raw_info[14]}",
                    )
                else:
                    _LOGGER.debug("Unexpected FFF1 device-info payload (%s bytes)", len(raw_info))
        except Exception as err:
            _LOGGER.debug("Reading firmware/hardware information failed: %s", err)
    async def _async_acquire_mtu(self, client: BleakClient) -> int | None:
        """Negotiate ATT MTU on BlueZ, mirroring the official app sequence.

        Bleak exposes this only on its BlueZ backend. The operation is best-effort
        and non-fatal; the integration continues with the default MTU if BlueZ or
        the adapter does not support it.
        """
        backend = getattr(client, "_backend", None)
        acquire_mtu = getattr(backend, "_acquire_mtu", None)
        if not callable(acquire_mtu):
            return None
        try:
            async with self._operation_lock:
                async with asyncio.timeout(3.0):
                    await acquire_mtu()
            mtu = getattr(backend, "_mtu_size", None)
            if isinstance(mtu, int):
                self._update_data(att_mtu=mtu)
                _LOGGER.debug("Negotiated Voltcraft ATT MTU: %s", mtu)
                return mtu
        except Exception as err:
            _LOGGER.debug("Voltcraft ATT MTU negotiation was not available: %s", err)
        return None
    async def _async_core_initialization(
        self, client: BleakClient, command_char: BleakGATTCharacteristic
    ) -> tuple[bool, bool]:
        """Run the official app's post-login initialization without blocking reconnect."""
        time_ok = False
        settings_ok = False
        random_ok = False
        finalize_ok = False
        # Match the official app order: login, read FFF1, negotiate MTU, then
        # synchronize the device clock. The MTU step is best-effort.
        await self._async_read_device_identity(client)
        await self._async_acquire_mtu(client)
        try:
            await self._write_and_wait(
                client,
                command_char,
                Commands.sync_time(dt_util.now().replace(tzinfo=None)),
                (Command.SET_TIME, 0),
                2.5,
            )
            time_ok = True
        except Exception as err:
            _LOGGER.debug("Device time synchronization failed: %s", err)
        try:
            await self._write_and_wait(
                client,
                command_char,
                Commands.request_settings(),
                (Command.GET_SETTINGS, 0),
                3.0,
            )
            settings_ok = True
        except Exception as err:
            _LOGGER.debug("Initial settings request failed: %s", err)
        # The app takes a first measurement, reads the yearly history and then
        # repeats settings before querying random mode.  Reproducing this order
        # also gives the device enough time to leave its connection animation.
        try:
            await self._write_and_wait(
                client,
                command_char,
                Commands.measure(),
                (Command.MEASURE, 0),
                3.0,
            )
        except Exception as err:
            _LOGGER.debug("Initial measurement request failed: %s", err)
        try:
            await self._write_and_wait(
                client,
                command_char,
                Commands.request_history(HistoryKind.YEAR),
                (Command.CONSUMPTION_YEAR, 0),
                _HISTORY_RESPONSE_TIMEOUT,
            )
        except Exception as err:
            _LOGGER.debug("Initial yearly-history request failed: %s", err)
        try:
            await self._write_and_wait(
                client,
                command_char,
                Commands.request_settings(),
                (Command.GET_SETTINGS, 0),
                3.0,
            )
        except Exception as err:
            _LOGGER.debug("Second app-style settings request failed: %s", err)
        try:
            await self._write_and_wait(
                client,
                command_char,
                Commands.request_random(),
                (Command.GET_RANDOM, 0),
                3.0,
            )
            random_ok = True
        except Exception as err:
            _LOGGER.debug("Initial random-mode request failed: %s", err)
        # This exact zero-argument probe is the final command in the captured
        # Android app initialization.  Its semantics are undocumented, but the
        # request is read-only and is the strongest remaining candidate for
        # ending the plug's blue connection animation.
        try:
            response = await self._write_and_wait(
                client,
                command_char,
                Commands.app_finalize(),
                (Command.APP_FINALIZE, 0),
                3.0,
            )
            finalize_ok = not isinstance(response, AckNotifyPayload) or response.was_successful
        except Exception as err:
            _LOGGER.debug("Official-app 0x07 finalization probe failed: %s", err)
        return time_ok and settings_ok and random_ok, finalize_ok
    async def _async_post_connect_sequence(self) -> None:
        try:
            await asyncio.sleep(0.25)
            client = self.client
            command_char = self._command_char
            if (
                client is None
                or command_char is None
                or not client.is_connected
                or not self._session_ready
            ):
                return
            initialized, finalized = await self._async_core_initialization(
                client, command_char
            )
            if self.client is not client or not self._session_ready:
                return
            self._update_data(
                app_initialization_complete=initialized,
                app_finalize_succeeded=finalized,
            )
            await self.async_refresh_all(include_initialization=False)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Post-connect SEM6000 initialization failed: %s", err)
    async def _async_ensure_connected(self, *, pin: str | None = None) -> BleakClient:
        login_pin = self._pin if pin is None else normalize_pin(pin)
        client = self.client
        if (
            pin is None
            and client is not None
            and client.is_connected
            and self._session_ready
        ):
            return client
        async with self._connect_lock:
            client = self.client
            if (
                pin is None
                and client is not None
                and client.is_connected
                and self._session_ready
            ):
                return client
            now = time.monotonic()
            if now < self._next_connect_at:
                raise UpdateFailed(
                    "Waiting before the next Voltcraft BLE connection attempt",
                    retry_after=max(1.0, self._next_connect_at - now),
                )
            if client is not None:
                self.client = None
                self._reset_session_state()
                await self._async_safe_disconnect(client, "replace stale client")
            if not bluetooth.async_address_present(
                self.hass, self._mac_address, connectable=True
            ):
                raise UpdateFailed(
                    f"Device {self._mac_address} is not currently advertising",
                    retry_after=5.0,
                )
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._mac_address, connectable=True
            )
            if ble_device is None:
                raise UpdateFailed(
                    f"No connectable Bluetooth path for {self._mac_address}",
                    retry_after=5.0,
                )
            new_client: BleakClient | None = None
            stage = "transport and service discovery"
            try:
                new_client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self.name,
                    disconnected_callback=self._handle_disconnected,
                    max_attempts=1,
                    timeout=_CONNECT_TIMEOUT,
                )
                self.client = new_client
                self._reset_session_state()
                command_char = new_client.services.get_characteristic(COMMAND_UUID)
                notify_char = new_client.services.get_characteristic(NOTIFY_UUID)
                if command_char is None or notify_char is None:
                    raise BleakError("Required FFF3/FFF4 characteristics were not discovered")
                self._command_char = command_char
                self._notify_char = notify_char
                passive = self._install_passive_notification_listener(
                    new_client, notify_char
                )
                # Direct CCCD writes are rejected by this BlueZ/device path.
                # Keep the proven v5.1 passive listener and do not issue the
                # known-failing descriptor write on every reconnect.
                handshake_applied = False
                if passive:
                    stage = "passive notification login"
                    try:
                        await self._async_login(
                            new_client, command_char, 2.0, pin=login_pin
                        )
                    except TimeoutError:
                        self._remove_passive_notification_listener()
                        passive = False
                if not passive:
                    stage = "notification subscription"
                    async with asyncio.timeout(_NOTIFY_TIMEOUT):
                        await new_client.start_notify(
                            notify_char,
                            self._handle_notify,
                            bluez={"use_start_notify": True},
                        )
                    self._notify_started = True
                    stage = "session login"
                    await self._async_login(new_client, command_char, pin=login_pin)
                self._session_ready = True
                self._missed_updates = 0
                self._history_request_in_flight = False
                self._last_history_poll = 0.0
                self._next_connect_at = 0.0
                self._update_data(
                    session_transport=(
                        "bluez_passive_app_handshake"
                        if passive and handshake_applied
                        else "bluez_passive"
                        if passive
                        else "bleak_notify"
                    ),
                    app_cccd_handshake_applied=handshake_applied,
                    app_initialization_complete=False,
                    app_finalize_succeeded=None,
                    last_connected_at=dt_util.utcnow().replace(tzinfo=None),
                )
                _LOGGER.info(
                    "Connected and authenticated to Voltcraft %s", self._mac_address
                )
                # Keep the proven v5.1 reconnect fast.  App-compatible setup and
                # the remaining feature-state reads continue in the background.
                self._extended_refresh_task = self.hass.async_create_task(
                    self._async_post_connect_sequence()
                )
                return new_client
            except (TimeoutError, BleakError) as err:
                if self.client is new_client:
                    self.client = None
                self._reset_session_state()
                if new_client is not None:
                    await self._async_safe_disconnect(new_client, f"failed during {stage}")
                self._next_connect_at = time.monotonic() + _RECONNECT_COOLDOWN
                raise UpdateFailed(
                    f"Failed to connect to {self._mac_address} during {stage}: {err}",
                    retry_after=_RECONNECT_COOLDOWN,
                ) from err
    async def _async_teardown(self) -> None:
        async with self._connect_lock:
            client = self.client
            self.client = None
            self._reset_session_state()
            if client is not None:
                await self._async_safe_disconnect(client, "teardown")
            self._next_connect_at = max(self._next_connect_at, time.monotonic() + 2.0)
    async def _async_safe_disconnect(self, client: BleakClient, context: str) -> None:
        try:
            async with asyncio.timeout(_BLE_OPERATION_TIMEOUT):
                await client.disconnect()
        except (TimeoutError, BleakError) as err:
            _LOGGER.debug("Error disconnecting client (%s): %s", context, err)
    async def _async_cancel_pending_reconnect(self) -> None:
        task = self._reconnect_task
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if self._reconnect_task is task:
            self._reconnect_task = None

    async def _async_verify_pin(
        self, pin: str, *, settle_delay: float = 0.0
    ) -> bool:
        """Verify a PIN through a fresh BLE connection and login."""
        candidate = normalize_pin(pin)
        await self._async_cancel_pending_reconnect()
        await self._async_teardown()
        if settle_delay > 0:
            await asyncio.sleep(settle_delay)

        last_error: Exception | None = None
        for attempt in range(_PIN_VERIFY_ATTEMPTS):
            self._next_connect_at = 0.0
            try:
                await self._async_ensure_connected(pin=candidate)
                return True
            except (TimeoutError, BleakError, UpdateFailed) as err:
                last_error = err
                await self._async_teardown()
                if attempt + 1 < _PIN_VERIFY_ATTEMPTS:
                    await asyncio.sleep(_PIN_VERIFY_RETRY_DELAY)

        _LOGGER.warning(
            "Voltcraft PIN verification failed after %s attempt(s): %s",
            _PIN_VERIFY_ATTEMPTS,
            last_error or "unknown error",
        )
        return False

    def _store_verified_pin(self, pin: str) -> None:
        verified_pin = normalize_pin(pin)
        self._pin = verified_pin
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_PIN: verified_pin},
        )

    async def _async_verify_and_store_pin(
        self,
        *,
        new_pin: str,
        previous_pin: str,
        operation_name: str,
    ) -> None:
        """Persist a changed PIN only after a fresh login proves it works."""
        if await self._async_verify_pin(
            new_pin, settle_delay=_PIN_CHANGE_SETTLE_DELAY
        ):
            self._store_verified_pin(new_pin)
            return

        if await self._async_verify_pin(previous_pin):
            raise HomeAssistantError(
                f"{operation_name} was acknowledged, but the device rejected the new PIN. "
                "The previous PIN remains stored."
            )

        await self._async_teardown()
        raise HomeAssistantError(
            f"{operation_name} was acknowledged, but neither the new nor the previous PIN "
            "could be verified. The stored PIN was not changed. Enter the working PIN in "
            "the integration options."
        )

    async def _user_command(
        self,
        frame: bytes | bytearray,
        key: tuple[int, int],
        timeout: float = _COMMAND_RESPONSE_TIMEOUT,
    ) -> ParsedNotifyPayload:
        try:
            response = await self._send_and_wait(frame, key, timeout)
            if isinstance(response, AckNotifyPayload) and not response.was_successful:
                raise HomeAssistantError("The SEM6000 rejected the command")
            return response
        except ValueError:
            raise
        except (TimeoutError, BleakError, UpdateFailed) as err:
            await self._async_teardown()
            detail = str(err) or type(err).__name__
            raise HomeAssistantError(f"Voltcraft command failed: {detail}") from err
    async def async_send_switch_command(self, mode: SwitchModes) -> None:
        await self._user_command(mode.build_payload(), (Command.SWITCH, 0))
        await self.async_request_refresh()

    async def async_sync_time(self) -> None:
        await self._user_command(
            Commands.sync_time(dt_util.now().replace(tzinfo=None)),
            (Command.SET_TIME, 0),
        )
    async def async_refresh_settings(self) -> None:
        await self._send_and_wait(
            Commands.request_settings(), (Command.GET_SETTINGS, 0)
        )

    async def async_set_night_mode(self, enabled: bool) -> None:
        await self._user_command(
            Commands.set_night_mode(enabled),
            (Command.SETTINGS_CONTROL, 0),
        )
        await self.async_refresh_settings()
    async def async_set_power_limit(self, watts: int) -> None:
        await self._user_command(
            Commands.set_power_limit(watts), (Command.SET_POWER_LIMIT, 0)
        )
        await self.async_refresh_settings()
    async def async_set_power_protection(self, enabled: bool) -> None:
        await self._user_command(
            Commands.set_power_protection(enabled),
            (Command.SET_POWER_PROTECTION, 0),
        )
        # Command 0x06 is acknowledged by the device, but GET_SETTINGS does not
        # report its state on the tested firmware. Publish the acknowledged
        # requested state locally and preserve it across normal refreshes.
        self._update_data(power_protection_enabled=enabled)
        await self.async_refresh_settings()
    async def async_set_prices(self, normal: float, reduced: float) -> None:
        await self._user_command(
            Commands.set_prices(round(normal * 100), round(reduced * 100)),
            (Command.SETTINGS_CONTROL, 0),
        )
        await self.async_refresh_settings()
    async def async_set_reduced_period(
        self, enabled: bool, start: dt_time, end: dt_time
    ) -> None:
        await self._user_command(
            Commands.set_reduced_period(enabled, start, end),
            (Command.SETTINGS_CONTROL, 0),
        )
        await self.async_refresh_settings()

    async def async_refresh_history(self) -> None:
        for kind in (HistoryKind.DAY, HistoryKind.MONTH, HistoryKind.YEAR):
            await self._send_and_wait(
                Commands.request_history(kind),
                (
                    {
                        HistoryKind.DAY: Command.CONSUMPTION_DAY,
                        HistoryKind.MONTH: Command.CONSUMPTION_MONTH,
                        HistoryKind.YEAR: Command.CONSUMPTION_YEAR,
                    }[kind],
                    0,
                ),
                _HISTORY_RESPONSE_TIMEOUT,
            )
    async def async_refresh_random(self) -> None:
        await self._send_and_wait(Commands.request_random(), (Command.GET_RANDOM, 0))

    async def async_set_random(
        self, enabled: bool, weekdays: int, start: dt_time, end: dt_time
    ) -> None:
        await self._user_command(
            Commands.set_random(enabled, weekdays, start, end),
            (Command.SET_RANDOM, 0),
        )
        await self.async_refresh_random()
    async def async_refresh_timer(self) -> None:
        await self._send_and_wait(Commands.request_timer(), (Command.GET_TIMER, 0))

    async def async_set_timer(self, turn_on: bool, target: datetime) -> None:
        action = TimerAction.TURN_ON if turn_on else TimerAction.TURN_OFF
        await self._user_command(
            Commands.set_timer(action, target), (Command.SET_TIMER, 0)
        )
        await self.async_refresh_timer()
    async def async_set_timer_delay(self, turn_on: bool, seconds: int) -> None:
        if seconds < 1:
            raise ValueError("Timer delay must be at least one second")
        target = dt_util.now().replace(tzinfo=None) + timedelta(seconds=seconds)
        await self.async_set_timer(turn_on, target)
    async def async_stop_timer(self) -> None:
        await self._user_command(
            Commands.set_timer(TimerAction.INACTIVE, None), (Command.SET_TIMER, 0)
        )
        await self.async_refresh_timer()
    async def async_refresh_schedules(self) -> None:
        # The response count is the number of entries in that page, not a
        # reliable global total.  Read all three four-entry pages so all 12
        # device slots are represented.
        entries: list[ScheduleEntry] = []
        for page in range(3):
            response = await self._send_and_wait(
                Commands.request_schedule(page), (Command.GET_SCHEDULE, 0), 5.0
            )
            if not isinstance(response, ScheduleStatusNotifyPayload):
                continue
            entries.extend(response.entries)
        unique = {entry.slot_id: entry for entry in entries}
        self._update_data(schedules=tuple(unique[key] for key in sorted(unique)))
    def _hardware_major(self) -> int | None:
        version = self._latest_data.hardware_version
        if not version:
            return None
        try:
            return int(str(version).split(".", 1)[0])
        except (TypeError, ValueError):
            return None
    def schedule_user_slot(self, wire_slot: int) -> int:
        """Translate a device scheduler ID to the 1-based UI slot."""
        major = self._hardware_major()
        if major is not None and major >= 3:
            return int(wire_slot) + 1
        if major is not None and major <= 2:
            return int(wire_slot) - 8
        # Infer the generation from the wire ID until FFF1 has been read.
        return int(wire_slot) + 1 if int(wire_slot) < 9 else int(wire_slot) - 8
    def _wire_slot(self, user_slot: int) -> int:
        """Translate a 1-based UI slot to the generation-specific wire ID."""
        slot = int(user_slot)
        if not 1 <= slot <= 12:
            raise ValueError("Schedule slot must be between 1 and 12")
        # Prefer the exact raw ID already returned by this device.  This avoids
        # assumptions when editing/removing an existing schedule.
        for entry in self._latest_data.schedules:
            if self.schedule_user_slot(entry.slot_id) == slot:
                return entry.slot_id
        major = self._hardware_major()
        if major is not None and major <= 2:
            return slot + 8
        # Hardware version 3 uses zero-based IDs.  It is also the safe default
        # for this device generation while identity data is still loading.
        return slot - 1
    async def async_set_schedule(
        self,
        *,
        slot: int | None,
        active: bool,
        turn_on: bool,
        weekdays: list[str] | tuple[str, ...] | set[str] | str,
        when: datetime,
    ) -> None:
        operation = ScheduleOperation.ADD if slot is None else ScheduleOperation.EDIT
        wire_slot = 0 if slot is None else self._wire_slot(slot)
        await self._user_command(
            Commands.set_schedule(
                operation,
                wire_slot,
                active,
                turn_on,
                weekday_mask(weekdays),
                when,
            ),
            (Command.SET_SCHEDULE, 0),
            5.0,
        )
        await self.async_refresh_schedules()
    async def async_remove_schedule(self, slot: int) -> None:
        await self._user_command(
            Commands.set_schedule(
                ScheduleOperation.REMOVE,
                self._wire_slot(slot),
                False,
                False,
                0,
                datetime(2000, 1, 1),
            ),
            (Command.SET_SCHEDULE, 0),
            5.0,
        )
        await self.async_refresh_schedules()
    async def async_set_device_name(self, name: str) -> None:
        await self._user_command(Commands.set_name(name), (Command.SET_NAME, 0))
        self._device_name = name
        self._update_data(device_name=name)

    async def async_refresh_device_identity(self) -> None:
        client = await self._async_ensure_connected()
        await self._async_read_device_identity(client)
    async def async_refresh_serial(self) -> None:
        await self._send_and_wait(Commands.request_serial(), (Command.GET_SERIAL, 0), 5.0)
    async def async_change_pin(self, new_pin: str) -> None:
        new_pin = normalize_pin(new_pin)
        async with self._credential_lock:
            previous_pin = self._pin
            if new_pin == previous_pin:
                return
            response = await self._user_command(
                Commands.change_pin(previous_pin, new_pin),
                (Command.LOGIN, PinOperation.CHANGE),
            )
            if (
                not isinstance(response, LoginNotifyPayload)
                or response.operation != PinOperation.CHANGE
                or not response.was_successful
            ):
                raise HomeAssistantError("PIN change was rejected")
            await self._async_verify_and_store_pin(
                new_pin=new_pin,
                previous_pin=previous_pin,
                operation_name="PIN change",
            )
    async def async_reset_pin(self) -> None:
        async with self._credential_lock:
            previous_pin = self._pin
            response = await self._user_command(
                Commands.reset_pin(), (Command.LOGIN, PinOperation.RESET)
            )
            if (
                not isinstance(response, LoginNotifyPayload)
                or response.operation != PinOperation.RESET
                or not response.was_successful
            ):
                raise HomeAssistantError("PIN reset was rejected")
            await self._async_verify_and_store_pin(
                new_pin=DEFAULT_PIN,
                previous_pin=previous_pin,
                operation_name="PIN reset",
            )
    async def async_reset_consumption(self) -> None:
        await self._user_command(
            Commands.reset_consumption(),
            (Command.SETTINGS_CONTROL, 0),
            5.0,
        )
        self._year_history_wh = None
        self._last_history_poll = 0.0
        self._update_data(consumed_energy=0.0)
    async def async_factory_reset(self) -> None:
        async with self._credential_lock:
            previous_pin = self._pin
            await self._user_command(
                Commands.factory_reset(),
                (Command.SETTINGS_CONTROL, 0),
                5.0,
            )
            await self._async_verify_and_store_pin(
                new_pin=DEFAULT_PIN,
                previous_pin=previous_pin,
                operation_name="Factory reset",
            )
    async def async_refresh_all(self, *, include_initialization: bool = True) -> None:
        client = await self._async_ensure_connected()
        if include_initialization:
            command_char = self._command_characteristic(client)
            initialized, finalized = await self._async_core_initialization(
                client, command_char
            )
            self._update_data(
                app_initialization_complete=initialized,
                app_finalize_succeeded=finalized,
            )
        operations = (
            self.async_refresh_device_identity,
            self.async_refresh_history,
            self.async_refresh_timer,
            self.async_refresh_serial,
            self.async_refresh_schedules,
        )
        for operation in operations:
            try:
                await operation()
            except (TimeoutError, BleakError, UpdateFailed, HomeAssistantError) as err:
                _LOGGER.debug("Optional state refresh failed: %s", err)
