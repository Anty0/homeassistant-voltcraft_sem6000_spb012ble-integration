"""Tests for the coordinator's update/timeout/reconnect/login state machine.

Driven with a fake BleakClient and the Home Assistant test harness. The
module-level MEASURE_TIMEOUT / LOGIN_TIMEOUT are patched small so timed-out cycles
are fast.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bleak.exc import BleakError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_MAC
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.voltcraft_sem6000_spb012ble import coordinator as coord_mod
from custom_components.voltcraft_sem6000_spb012ble.const import DOMAIN
from custom_components.voltcraft_sem6000_spb012ble.coordinator import (
    VoltcraftData,
    VoltcraftDataUpdateCoordinator,
)
from custom_components.voltcraft_sem6000_spb012ble.protocol import (
    MeasureNotifyPayload,
    LoginCommand,
)
from tests.ble_harness import (
    FORMATTED_MAC,
    LOGIN_FAILURE_FRAME,
    LOGIN_SUCCESS_FRAME,
    RAW_MAC,
    FakeClient,
    measure_frame,
    switch_frame,
    written_commands,
)


def reauth_flows(hass, entry) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == SOURCE_REAUTH and flow["context"].get("entry_id") == entry.entry_id
    ]


@dataclass
class Env:
    coord: VoltcraftDataUpdateCoordinator
    entry: MockConfigEntry
    ble_device: SimpleNamespace
    establish_mock: AsyncMock
    lookup_mock: MagicMock
    clients: list[FakeClient]


@pytest.fixture
def env(hass, monkeypatch):
    ble_device = SimpleNamespace(name="Test Plug", address=RAW_MAC)
    clients: list[FakeClient] = []
    state = {"default_frame": None, "default_login_frame": None}

    def _establish(*args, **kwargs):
        client = FakeClient()
        client.auto_measure_frame = state["default_frame"]
        client.auto_login_frame = state["default_login_frame"]
        clients.append(client)
        return client

    establish_mock = AsyncMock(side_effect=_establish)
    lookup_mock = MagicMock(return_value=ble_device)

    monkeypatch.setattr(coord_mod, "MEASURE_TIMEOUT", 0.1)
    monkeypatch.setattr(coord_mod, "LOGIN_TIMEOUT", 0.1)
    monkeypatch.setattr(coord_mod, "establish_connection", establish_mock)
    monkeypatch.setattr(coord_mod.bluetooth, "async_ble_device_from_address", lookup_mock)

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: RAW_MAC}, unique_id=FORMATTED_MAC)
    entry.add_to_hass(hass)

    coord = VoltcraftDataUpdateCoordinator(hass, entry, RAW_MAC, ble_device, None)

    holder = Env(coord, entry, ble_device, establish_mock, lookup_mock, clients)

    def _set_default(frame):
        state["default_frame"] = frame

    def _set_default_login(frame):
        state["default_login_frame"] = frame

    def _build_coord(pin):
        return VoltcraftDataUpdateCoordinator(hass, entry, RAW_MAC, ble_device, pin)

    holder._set_default = _set_default  # type: ignore[attr-defined]
    holder._set_default_login = _set_default_login  # type: ignore[attr-defined]
    holder.build_coord = _build_coord  # type: ignore[attr-defined]
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
    assert client.is_connected is False
    assert env.coord.client is None


async def test_async_shutdown_is_idempotent(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    await env.coord.async_shutdown()
    await env.coord.async_shutdown()  # second call early-returns at the None guard, no raise
    assert env.coord.client is None


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

    await env.clients[-1].deliver_frame(measure_frame())
    data = await update_task
    assert isinstance(data, VoltcraftData)


# --- PIN / login ---------------------------------------------------------------------------------


async def test_no_pin_sends_no_login(env):
    env._set_default(measure_frame())
    data = await env.coord._async_update_data()  # coord built with pin=None
    assert isinstance(data, VoltcraftData)
    assert all(w[2] != 0x17 for w in env.clients[-1].written)


async def test_pin_login_success_then_measure(env):
    coord = env.build_coord("1234")
    env._set_default(measure_frame())
    env._set_default_login(LOGIN_SUCCESS_FRAME)
    data = await coord._async_update_data()

    client = env.clients[-1]
    assert client.written[0] == bytes(LoginCommand.build_payload("1234"))
    assert written_commands(client).index(0x17) < written_commands(client).index(0x04)  # login before measure
    assert isinstance(data, VoltcraftData)
    assert coord.measurement_count == 1


async def test_login_sent_once_per_live_connection(env):
    coord = env.build_coord("1234")
    env._set_default(measure_frame())
    env._set_default_login(LOGIN_SUCCESS_FRAME)
    await coord._async_update_data()
    await coord._async_update_data()  # same live connection

    client = env.clients[-1]
    assert written_commands(client).count(0x17) == 1
    assert coord.measurement_count == 2


async def test_login_timeout_governed_by_login_timeout(env, monkeypatch):
    # LOGIN_TIMEOUT small, MEASURE_TIMEOUT large: a regression wiring login to
    # MEASURE_TIMEOUT would block ~2s and blow the wall-clock bound.
    monkeypatch.setattr(coord_mod, "LOGIN_TIMEOUT", 0.1)
    monkeypatch.setattr(coord_mod, "MEASURE_TIMEOUT", 2.0)
    coord = env.build_coord("1234")
    env._set_default_login(None)  # no login frame -> the login wait times out

    start = time.monotonic()
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
    elapsed = time.monotonic() - start
    assert elapsed < 1.0


async def test_login_failure_frame_raises_auth_failed(env):
    coord = env.build_coord("1234")
    env._set_default_login(LOGIN_FAILURE_FRAME)
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_ensure_connected()
    assert coord.client is None
    assert env.clients[-1].is_connected is False  # orphan disconnected


async def test_reauth_wiring_on_failure_frame(hass, env):
    coord = env.build_coord("1234")
    env._set_default_login(LOGIN_FAILURE_FRAME)
    await coord.async_refresh()
    await hass.async_block_till_done()  # async_start_reauth inits the flow on a task
    assert coord.last_update_success is False
    assert len(reauth_flows(hass, env.entry)) == 1


async def test_reauth_non_accumulation(hass, env):
    coord = env.build_coord("1234")
    env._set_default_login(LOGIN_FAILURE_FRAME)
    await coord.async_refresh()
    await hass.async_block_till_done()
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert len(reauth_flows(hass, env.entry)) == 1  # async_start_reauth is idempotent


async def test_login_timeout_does_not_start_reauth(hass, env):
    coord = env.build_coord("1234")
    env._set_default_login(None)  # silent firmware: login times out
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert coord.last_update_success is False
    assert reauth_flows(hass, env.entry) == []  # transient -> no reauth


async def test_login_write_bleak_error_does_not_start_reauth(hass, env):
    coord = env.build_coord("1234")

    def _establish(*args, **kwargs):
        client = FakeClient()
        client.raise_on_login_write = True
        env.clients.append(client)
        return client

    env.establish_mock.side_effect = _establish
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert coord.last_update_success is False
    assert reauth_flows(hass, env.entry) == []
    assert coord.client is None
    assert env.clients[-1].is_connected is False
    assert not coord._operation_lock.locked()


async def test_login_timeout_warning_gated(env, caplog):
    coord = env.build_coord("1234")
    env._set_default_login(None)
    import logging

    with caplog.at_level(logging.WARNING):
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()  # first connect: warning + UpdateFailed
        coord.client = None  # force a fresh connect next cycle
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()  # second timeout, same episode
    assert sum("Login to" in r.message for r in caplog.records) == 1


async def test_login_timeout_warning_rearmed_after_success(env, caplog):
    coord = env.build_coord("1234")
    import logging

    with caplog.at_level(logging.WARNING):
        env._set_default_login(None)
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()  # warning #1

        env._set_default_login(LOGIN_SUCCESS_FRAME)
        env._set_default(measure_frame())
        await coord._async_update_data()  # success clears the gate

        env._set_default_login(None)
        coord.client.is_connected = False
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()  # warning #2 (re-armed)
    assert sum("Login to" in r.message for r in caplog.records) == 2


async def test_no_pin_timeout_logs_no_warning(env, caplog):
    env._set_default(None)  # device never answers MEASURE; no PIN configured
    import logging

    with caplog.at_level(logging.WARNING):
        with pytest.raises(UpdateFailed):
            await env.coord._async_update_data()
    assert not any("Login to" in r.message for r in caplog.records)


async def test_login_timeout_on_reconnect_immediate(env):
    coord = env.build_coord("1234")
    env._set_default(measure_frame())
    env._set_default_login(LOGIN_SUCCESS_FRAME)
    await coord._async_update_data()  # seed one good measurement
    assert coord._missed_updates == 0

    prior = coord.client
    coord.client.is_connected = False
    env._set_default_login(None)  # reconnect login goes silent
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()  # immediate, no missed-update tolerance
    assert coord._missed_updates == 0
    assert coord.client is prior  # stale client kept (replaced on the next reconnect)
    assert env.clients[-1].is_connected is False  # fresh orphan disconnected


async def test_invalid_stored_pin_raises_auth_failed(env):
    coord = env.build_coord("12")  # malformed stored PIN
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_ensure_connected()


async def test_stale_login_result_not_reused(env):
    coord = env.build_coord("1234")
    env._set_default(measure_frame())
    env._set_default_login(LOGIN_SUCCESS_FRAME)
    await coord._async_update_data()  # _login_result True, _login_count 1

    coord.client.is_connected = False
    env._set_default_login(None)  # no login frame on reconnect
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()  # must time out, not re-read the stale True


async def test_malformed_login_frame_mid_login(env):
    coord = env.build_coord("1234")

    def _establish(*args, **kwargs):
        client = FakeClient()
        client.auto_login_frame = bytearray([0x0F, 0x02, 0x17, 0x00, 0xFF, 0xFF])  # garbage
        env.clients.append(client)
        return client

    env.establish_mock.side_effect = _establish
    with pytest.raises(UpdateFailed):
        await coord._async_ensure_connected()
    assert coord.client is None


async def test_reconnect_resends_login_before_measure(env):
    coord = env.build_coord("1234")
    env._set_default(measure_frame())
    env._set_default_login(LOGIN_SUCCESS_FRAME)
    await coord._async_update_data()

    coord.client.is_connected = False
    await coord._async_update_data()  # reconnect
    client = env.clients[-1]  # the fresh client
    assert written_commands(client).index(0x17) < written_commands(client).index(0x04)


async def test_steady_state_reauth_two_cycles(hass, env):
    coord = env.build_coord("1234")
    env._set_default(measure_frame())
    env._set_default_login(LOGIN_SUCCESS_FRAME)
    await coord._async_update_data()  # one good measurement
    prior_client = coord.client

    env._set_default_login(LOGIN_FAILURE_FRAME)
    for _ in range(2):
        coord.client.is_connected = False
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()
        assert coord._missed_updates == 0
        assert coord.client is prior_client  # retained: not nulled, not the orphan
        assert env.clients[-1].is_connected is False  # fresh orphan disconnected


async def test_switch_happy_path_with_pin(env):
    coord = env.build_coord("1234")
    env._set_default_login(LOGIN_SUCCESS_FRAME)
    await coord.async_send_switch_command(coord_mod.SwitchModes.ON)
    client = env.clients[-1]
    assert written_commands(client).index(0x17) < written_commands(client).index(0x03)


async def test_switch_auth_failure_wrapped_no_reauth(hass, env):
    coord = env.build_coord("1234")
    env._set_default_login(LOGIN_FAILURE_FRAME)
    with pytest.raises(HomeAssistantError) as exc_info:
        await coord.async_send_switch_command(coord_mod.SwitchModes.ON)
    await hass.async_block_till_done()
    assert not isinstance(exc_info.value, ConfigEntryAuthFailed)
    assert "Failed to send switch command" in str(exc_info.value)
    assert reauth_flows(hass, env.entry) == []  # switch path does not spawn reauth


async def test_switch_transient_login_failure_wrapped(hass, env):
    coord = env.build_coord("1234")

    def _establish(*args, **kwargs):
        client = FakeClient()
        client.raise_on_login_write = True
        env.clients.append(client)
        return client

    env.establish_mock.side_effect = _establish
    with pytest.raises(HomeAssistantError) as exc_info:
        await coord.async_send_switch_command(coord_mod.SwitchModes.ON)
    await hass.async_block_till_done()
    assert not isinstance(exc_info.value, ConfigEntryAuthFailed)
    assert not isinstance(exc_info.value, UpdateFailed)
    assert reauth_flows(hass, env.entry) == []
    assert not coord._operation_lock.locked()


async def test_operation_lock_released_while_login_parked(env):
    coord = env.build_coord("1234")
    env._set_default_login(None)  # deliver manually so login parks on the cond
    task = asyncio.create_task(coord._async_ensure_connected())
    await asyncio.sleep(0.02)

    assert not coord._operation_lock.locked()  # released before the cond await
    await env.clients[-1].deliver_frame(LOGIN_SUCCESS_FRAME)
    await task
    assert coord.client is not None


async def test_cross_isolation_measure_does_not_satisfy_login(env, monkeypatch):
    monkeypatch.setattr(coord_mod, "LOGIN_TIMEOUT", 1.0)
    coord = env.build_coord("1234")
    env._set_default_login(None)
    task = asyncio.create_task(coord._async_ensure_connected())
    await asyncio.sleep(0.02)  # login parked on _login_cond

    login_count_before = coord._login_count
    await coord._handle_notify(None, measure_frame())  # a MEASURE frame, not LOGIN
    await asyncio.sleep(0.02)
    assert coord._login_count == login_count_before  # login NOT satisfied
    assert not task.done()
    assert coord.measurement_count == 1  # routed to the measure path

    await env.clients[-1].deliver_frame(LOGIN_SUCCESS_FRAME)
    await task  # login now completes


async def test_cross_isolation_login_does_not_satisfy_measure(env, monkeypatch):
    monkeypatch.setattr(coord_mod, "MEASURE_TIMEOUT", 0.2)
    env._set_default(measure_frame())
    await env.coord._async_update_data()  # connect (no PIN)
    count_before = env.coord.measurement_count

    env.clients[-1].auto_measure_frame = None
    task = asyncio.create_task(env.coord._async_update_data())
    await asyncio.sleep(0.02)
    await env.coord._handle_notify(None, LOGIN_SUCCESS_FRAME)  # a LOGIN frame mid measure-wait

    result = await task
    assert result is not None  # measure timed out -> last data
    assert env.coord.measurement_count == count_before  # login frame did not complete the measure


async def test_stray_login_frame_is_harmless(env):
    env._set_default(measure_frame())
    await env.coord._async_update_data()
    count_before = env.coord.measurement_count
    login_count_before = env.coord._login_count

    await env.coord._handle_notify(None, LOGIN_SUCCESS_FRAME)  # no login pending
    assert env.coord._login_count == login_count_before + 1
    assert env.coord.measurement_count == count_before
