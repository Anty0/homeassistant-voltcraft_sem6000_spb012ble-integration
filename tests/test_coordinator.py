"""Tests for the coordinator's update/timeout/reconnect state machine.

Driven with a fake BleakClient and the Home Assistant test harness. The
module-level MEASURE_TIMEOUT is patched small so timed-out cycles are fast.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bleak.exc import BleakError

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.voltcraft_sem6000_spb012ble import coordinator as coord_mod
from custom_components.voltcraft_sem6000_spb012ble.coordinator import (
    VoltcraftData,
    VoltcraftDataUpdateCoordinator,
)
from custom_components.voltcraft_sem6000_spb012ble.protocol import MeasureNotifyPayload

RAW_MAC = "AA:BB:CC:DD:EE:FF"
FORMATTED_MAC = "aa:bb:cc:dd:ee:ff"

MEASURE_ARGS = bytearray([0x01, 0x00, 0x05, 0xDC, 0xE6, 0x00, 0xFA, 0x32, 0xAB, 0xCD, 0x04, 0xD2])


def measure_frame() -> bytearray:
    params = bytearray([0x04, 0x00]) + MEASURE_ARGS
    body = params + bytearray([0x00])
    return bytearray([0x0F, len(body)]) + body + bytearray([0xFF, 0xFF])


def switch_frame() -> bytearray:
    params = bytearray([0x03, 0x00, 0x01])
    body = params + bytearray([0x00])
    return bytearray([0x0F, len(body)]) + body + bytearray([0xFF, 0xFF])


class FakeClient:
    def __init__(self) -> None:
        self.is_connected = True
        self.notify_cb = None
        self.written: list[bytes] = []
        self.stopped = False
        self.disconnect_error = False
        self.raise_on_write = False
        self.hang_on_write = False
        self.auto_measure_frame: bytearray | None = None

    async def start_notify(self, uuid, cb) -> None:
        self.notify_cb = cb

    async def stop_notify(self, uuid) -> None:
        self.stopped = True

    async def write_gatt_char(self, uuid, data) -> None:
        if self.raise_on_write:
            raise BleakError("write failed")
        if self.hang_on_write:
            await asyncio.Event().wait()  # never resolves; relies on the caller's timeout
        self.written.append(bytes(data))
        if self.auto_measure_frame is not None and bytes(data)[2] == 0x04:
            asyncio.get_running_loop().create_task(self._deliver(self.auto_measure_frame))

    async def _deliver(self, frame: bytearray) -> None:
        if self.notify_cb is not None:
            await self.notify_cb(None, frame)

    async def deliver_measure(self, frame: bytearray) -> None:
        await self.notify_cb(None, frame)

    async def disconnect(self) -> None:
        if self.disconnect_error:
            raise BleakError("disconnect failed")
        self.is_connected = False


@dataclass
class Env:
    coord: VoltcraftDataUpdateCoordinator
    establish_mock: AsyncMock
    lookup_mock: MagicMock
    clients: list[FakeClient]


@pytest.fixture
def env(hass, monkeypatch):
    ble_device = SimpleNamespace(name="Test Plug", address=RAW_MAC)
    clients: list[FakeClient] = []
    state = {"default_frame": None}

    def _establish(*args, **kwargs):
        client = FakeClient()
        client.auto_measure_frame = state["default_frame"]
        clients.append(client)
        return client

    establish_mock = AsyncMock(side_effect=_establish)
    lookup_mock = MagicMock(return_value=ble_device)

    monkeypatch.setattr(coord_mod, "MEASURE_TIMEOUT", 0.1)
    monkeypatch.setattr(coord_mod, "establish_connection", establish_mock)
    monkeypatch.setattr(coord_mod.bluetooth, "async_ble_device_from_address", lookup_mock)

    coord = VoltcraftDataUpdateCoordinator(hass, RAW_MAC, ble_device)

    holder = Env(coord, establish_mock, lookup_mock, clients)

    def _set_default(frame):
        state["default_frame"] = frame

    holder._set_default = _set_default  # type: ignore[attr-defined]
    return holder


async def test_happy_path_returns_new_data(env):
    env._set_default(measure_frame())
    data = await env.coord._async_update_data()
    assert isinstance(data, VoltcraftData)
    assert data.is_on is True
    assert data.power == 1.5
    assert data.voltage == 230.0
    assert data.current == 0.25
    assert data.consumed_energy == 1.234
    assert env.coord.measurement_count == 1


def test_power_factor_none_when_no_apparent_power():
    for voltage, current in ((230, 0), (0, 250)):
        payload = MeasureNotifyPayload(
            is_on=True, power=1500, voltage=voltage, current=current, frequency=50, consumed_energy=1234
        )
        assert VoltcraftData.from_payload(payload).power_factor is None


def test_power_factor_clamped_to_one():
    payload = MeasureNotifyPayload(is_on=True, power=1_000_000, voltage=10, current=10, frequency=50, consumed_energy=0)
    assert VoltcraftData.from_payload(payload).power_factor == 1.0


def test_power_factor_representative_value():
    # power 1.15 W, voltage 230 V, current 0.01 A -> apparent 2.3 -> pf 0.5
    payload = MeasureNotifyPayload(is_on=True, power=1150, voltage=230, current=10, frequency=50, consumed_energy=0)
    assert VoltcraftData.from_payload(payload).power_factor == pytest.approx(0.5)


async def test_single_timeout_returns_last_data(env):
    env._set_default(measure_frame())
    last = await env.coord._async_update_data()

    env.clients[-1].auto_measure_frame = None  # stop delivering notifications
    result = await env.coord._async_update_data()
    assert result == last
    assert env.coord._missed_updates == 1


async def test_max_missed_updates_raises(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    env.clients[-1].auto_measure_frame = None

    assert await env.coord._async_update_data() is not None  # miss 1
    assert await env.coord._async_update_data() is not None  # miss 2
    with pytest.raises(UpdateFailed):  # miss 3 -> threshold
        await env.coord._async_update_data()
    assert env.coord.client is None


async def test_first_refresh_with_no_data_raises(env):
    env._set_default(None)  # device never answers
    with pytest.raises(UpdateFailed):
        await env.coord._async_update_data()
    assert env.coord.client is None


async def test_missed_counter_resets_on_measurement(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    env.clients[-1].auto_measure_frame = None
    await env.coord._async_update_data()  # miss 1
    assert env.coord._missed_updates == 1

    env.clients[-1].auto_measure_frame = measure_frame()
    await env.coord._async_update_data()
    assert env.coord._missed_updates == 0


async def test_concurrent_cycles_share_one_measurement(env, monkeypatch):
    monkeypatch.setattr(coord_mod, "MEASURE_TIMEOUT", 1.0)
    env._set_default(None)  # deliver manually
    t1 = asyncio.create_task(env.coord._async_update_data())
    t2 = asyncio.create_task(env.coord._async_update_data())
    await asyncio.sleep(0.05)  # both reach the measurement wait

    await env.coord._handle_notify(None, measure_frame())  # a single measurement
    d1 = await t1
    d2 = await t2
    assert isinstance(d1, VoltcraftData)
    assert isinstance(d2, VoltcraftData)
    assert env.coord._missed_updates == 0  # neither cycle spuriously missed


async def test_malformed_frame_does_not_complete_cycle(env, monkeypatch):
    monkeypatch.setattr(coord_mod, "MEASURE_TIMEOUT", 0.2)
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    count_before = env.coord.measurement_count

    env.clients[-1].auto_measure_frame = None
    task = asyncio.create_task(env.coord._async_update_data())
    await asyncio.sleep(0.02)
    await env.coord._handle_notify(None, bytearray([0x0F, 0x02, 0x04, 0x00, 0xFF, 0xFF]))

    result = await task
    assert result is not None  # times out -> last data, below the miss budget
    assert env.coord.measurement_count == count_before  # garbage did not count
    assert env.coord._missed_updates == 1


async def test_half_open_link_reconnects(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    assert env.establish_mock.call_count == 1

    env.coord.client.is_connected = False  # half-open: assigned but dead
    await env.coord._async_ensure_connected()
    assert env.establish_mock.call_count == 2
    assert env.coord._missed_updates == 0
    assert env.clients[-1].notify_cb is not None  # start_notify re-registered


async def test_connect_failure_raises_update_failed(env):
    env.establish_mock.side_effect = BleakError("unreachable")
    with pytest.raises(UpdateFailed):
        await env.coord._async_update_data()


async def test_hung_write_times_out_as_miss(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()  # connect + seed self.data

    env.clients[-1].hang_on_write = True
    result = await env.coord._async_update_data()  # write hangs -> timeout -> miss
    assert result is not None  # last data returned, below the miss budget
    assert env.coord._missed_updates == 1


async def test_hung_switch_write_raises(env):
    await env.coord._async_ensure_connected()
    env.clients[-1].hang_on_write = True
    with pytest.raises(HomeAssistantError):
        await env.coord.async_send_switch_command(coord_mod.SwitchModes.ON)


async def test_hung_start_notify_raises_update_failed(env):
    bad = FakeClient()

    async def _hang_start_notify(uuid, cb):
        await asyncio.Event().wait()

    bad.start_notify = _hang_start_notify
    env.establish_mock.side_effect = lambda *args, **kwargs: bad

    with pytest.raises(UpdateFailed):
        await env.coord._async_ensure_connected()
    assert env.coord.client is None
    assert bad.is_connected is False  # guard-disconnected after the timeout


async def test_start_notify_failure_disconnects_client(env):
    bad = FakeClient()

    async def _bad_start_notify(uuid, cb):
        raise BleakError("notify failed")

    bad.start_notify = _bad_start_notify
    env.establish_mock.side_effect = lambda *args, **kwargs: bad

    with pytest.raises(UpdateFailed):
        await env.coord._async_ensure_connected()
    assert bad.is_connected is False  # orphaned client was disconnected
    assert env.coord.client is None


async def test_ensure_connected_already_connected_is_noop(env):
    await env.coord._async_ensure_connected()
    await env.coord._async_ensure_connected()
    assert env.establish_mock.call_count == 1


async def test_concurrent_connect_establishes_once(env):
    gate = asyncio.Event()

    async def _gated_establish(*args, **kwargs):
        await gate.wait()
        client = FakeClient()
        client.auto_measure_frame = measure_frame()
        env.clients.append(client)
        return client

    env.establish_mock.side_effect = _gated_establish

    t1 = asyncio.create_task(env.coord._async_ensure_connected())
    t2 = asyncio.create_task(env.coord._async_ensure_connected())
    await asyncio.sleep(0.01)  # t1 holds _connect_lock inside establish; t2 blocks on the lock
    gate.set()
    await asyncio.gather(t1, t2)

    assert env.establish_mock.call_count == 1  # double-checked lock prevents a duplicate connect
    assert len(env.clients) == 1


async def test_lookup_uses_raw_uppercase_mac(env):
    await env.coord._async_ensure_connected()
    env.lookup_mock.assert_called_once()
    assert env.lookup_mock.call_args.args[1] == RAW_MAC
    assert env.lookup_mock.call_args.kwargs["connectable"] is True
    assert env.coord.mac == FORMATTED_MAC


async def test_none_device_raises_then_recovers(env):
    env.lookup_mock.return_value = None
    with pytest.raises(UpdateFailed):
        await env.coord._async_update_data()

    env.lookup_mock.return_value = SimpleNamespace(name="Test Plug", address=RAW_MAC)
    env._set_default(measure_frame())
    data = await env.coord._async_update_data()
    assert isinstance(data, VoltcraftData)


async def test_reconnect_restores_full_miss_budget(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()  # establish #1
    env.clients[-1].auto_measure_frame = None
    await env.coord._async_update_data()  # miss 1
    await env.coord._async_update_data()  # miss 2
    with pytest.raises(UpdateFailed):
        await env.coord._async_update_data()  # miss 3 -> teardown
    assert env.establish_mock.call_count == 1

    env._set_default(None)  # reconnected client also silent
    await env.coord._async_update_data()  # reconnect #2, miss 1 (budget reset)
    await env.coord._async_update_data()  # miss 2 -> still below threshold
    assert env.establish_mock.call_count == 2  # no second teardown/churn


async def test_teardown_on_guarded_disconnect_failure(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    env.clients[-1].auto_measure_frame = None
    env.clients[-1].disconnect_error = True

    await env.coord._async_update_data()
    await env.coord._async_update_data()
    with pytest.raises(UpdateFailed):
        await env.coord._async_update_data()
    assert env.coord.client is None  # teardown completed despite disconnect error


async def test_teardown_does_not_null_a_replacement_client(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    client_a = env.coord.client

    # Pause client_a.disconnect so a reconnect can interleave during the await.
    gate = asyncio.Event()

    async def _gated_disconnect():
        client_a.is_connected = False
        await gate.wait()

    client_a.disconnect = _gated_disconnect

    teardown_task = asyncio.create_task(env.coord._async_teardown())
    await asyncio.sleep(0.01)  # teardown now blocked inside disconnect

    client_b = await env.coord._async_ensure_connected()  # establishes a fresh client
    assert client_b is not client_a
    assert env.coord.client is client_b

    gate.set()
    await teardown_task
    assert env.coord.client is client_b  # the replacement was not discarded


async def test_async_shutdown_with_no_client_does_not_raise(env):
    await env.coord.async_shutdown()  # client is None


async def test_async_shutdown_tolerates_bleak_errors(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()

    async def _raise(uuid):
        raise BleakError("boom")

    env.coord.client.stop_notify = _raise
    env.coord.client.disconnect_error = True
    await env.coord.async_shutdown()  # must not propagate


async def test_async_shutdown_disconnects(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    client = env.coord.client
    await env.coord.async_shutdown()
    assert client.stopped is True
    assert env.coord.client.is_connected is False


async def test_switch_notification_triggers_refresh(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    env.coord.async_request_refresh = AsyncMock()

    await env.coord._handle_notify(None, switch_frame())
    await asyncio.sleep(0.01)
    env.coord.async_request_refresh.assert_called()


async def test_switch_command_reraises_bleak_error(env):
    await env.coord._async_ensure_connected()
    env.clients[-1].raise_on_write = True
    with pytest.raises(HomeAssistantError):
        await env.coord.async_send_switch_command(coord_mod.SwitchModes.ON)
    assert env.establish_mock.call_count == 1  # ensure-first still ran


async def test_switch_command_device_not_found_raises_home_assistant_error(env):
    env.lookup_mock.return_value = None  # unreachable -> _async_ensure_connected raises UpdateFailed
    with pytest.raises(HomeAssistantError) as exc_info:
        await env.coord.async_send_switch_command(coord_mod.SwitchModes.ON)
    # Must be the wrapped error, not the raw UpdateFailed (which is also a HomeAssistantError).
    assert not isinstance(exc_info.value, UpdateFailed)
    assert "Failed to send switch command" in str(exc_info.value)


async def test_operation_lock_serializes_switch_write(env):
    await env.coord._async_ensure_connected()
    await env.coord._operation_lock.acquire()
    task = asyncio.create_task(env.coord.async_send_switch_command(coord_mod.SwitchModes.ON))
    await asyncio.sleep(0.01)
    assert env.clients[-1].written == []  # write blocked by held lock

    env.coord._operation_lock.release()
    await task
    assert any(w[2] == 0x03 for w in env.clients[-1].written)  # switch write happened


async def test_operation_lock_released_before_measure_await(env, monkeypatch):
    monkeypatch.setattr(coord_mod, "MEASURE_TIMEOUT", 1.0)
    env._set_default(None)  # do not auto-deliver; we deliver manually
    update_task = asyncio.create_task(env.coord._async_update_data())
    await asyncio.sleep(0.02)  # let the cycle reach the measure-event await

    assert not env.coord._operation_lock.locked()
    await asyncio.wait_for(env.coord._operation_lock.acquire(), 0.1)
    env.coord._operation_lock.release()

    await env.clients[-1].deliver_measure(measure_frame())
    data = await update_task
    assert isinstance(data, VoltcraftData)
