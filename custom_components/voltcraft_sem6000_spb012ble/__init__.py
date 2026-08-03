from __future__ import annotations

import logging
from datetime import datetime
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import VoltcraftDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.TIME,
    Platform.TEXT,
    Platform.BUTTON,
]

_SERVICE_MARKER = "_services_registered"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_ACTION = "action"
CONF_SECONDS = "seconds"
CONF_AT = "at"
CONF_SLOT = "slot"
CONF_ACTIVE = "active"
CONF_WEEKDAYS = "weekdays"
CONF_START = "start"
CONF_END = "end"
CONF_NEW_PIN = "new_pin"
CONF_CONFIRM = "confirm"


def _local_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return dt_util.as_local(value).replace(tzinfo=None)


def _coordinator_for_call(
    hass: HomeAssistant, call: ServiceCall
) -> VoltcraftDataUpdateCoordinator:
    domain_data = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(CONF_CONFIG_ENTRY_ID)
    if entry_id:
        coordinator = domain_data.get(entry_id)
        if isinstance(coordinator, VoltcraftDataUpdateCoordinator):
            return coordinator
        raise HomeAssistantError(f"Unknown Voltcraft config entry: {entry_id}")

    coordinators = [
        value
        for key, value in domain_data.items()
        if key != _SERVICE_MARKER and isinstance(value, VoltcraftDataUpdateCoordinator)
    ]
    if len(coordinators) == 1:
        return coordinators[0]
    if not coordinators:
        raise HomeAssistantError("No loaded Voltcraft SEM6000 config entry")
    raise HomeAssistantError("config_entry_id is required when multiple plugs are loaded")


async def _async_register_services(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_SERVICE_MARKER):
        return

    common = {vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string}

    async def handle_set_timer(call: ServiceCall) -> None:
        coordinator = _coordinator_for_call(hass, call)
        turn_on = call.data[CONF_ACTION] == "on"
        seconds = call.data.get(CONF_SECONDS)
        target = call.data.get(CONF_AT)
        if (seconds is None) == (target is None):
            raise HomeAssistantError("Specify exactly one of seconds or at")
        if seconds is not None:
            await coordinator.async_set_timer_delay(turn_on, seconds)
        else:
            await coordinator.async_set_timer(turn_on, _local_naive(target))

    async def handle_stop_timer(call: ServiceCall) -> None:
        await _coordinator_for_call(hass, call).async_stop_timer()

    async def handle_set_schedule(call: ServiceCall) -> None:
        coordinator = _coordinator_for_call(hass, call)
        await coordinator.async_set_schedule(
            slot=call.data.get(CONF_SLOT),
            active=call.data[CONF_ACTIVE],
            turn_on=call.data[CONF_ACTION] == "on",
            weekdays=call.data.get(CONF_WEEKDAYS, []),
            when=_local_naive(call.data[CONF_AT]),
        )

    async def handle_remove_schedule(call: ServiceCall) -> None:
        await _coordinator_for_call(hass, call).async_remove_schedule(call.data[CONF_SLOT])

    async def handle_set_random(call: ServiceCall) -> None:
        coordinator = _coordinator_for_call(hass, call)
        from .protocol import weekday_mask

        await coordinator.async_set_random(
            call.data[CONF_ACTIVE],
            weekday_mask(call.data.get(CONF_WEEKDAYS, [])),
            call.data[CONF_START],
            call.data[CONF_END],
        )

    async def handle_change_pin(call: ServiceCall) -> None:
        await _coordinator_for_call(hass, call).async_change_pin(call.data[CONF_NEW_PIN])

    async def handle_reset_pin(call: ServiceCall) -> None:
        if not call.data[CONF_CONFIRM]:
            raise HomeAssistantError("Set confirm to true to reset the device PIN")
        await _coordinator_for_call(hass, call).async_reset_pin()

    async def handle_reset_consumption(call: ServiceCall) -> None:
        if not call.data[CONF_CONFIRM]:
            raise HomeAssistantError("Set confirm to true to reset consumption data")
        await _coordinator_for_call(hass, call).async_reset_consumption()

    async def handle_factory_reset(call: ServiceCall) -> None:
        if not call.data[CONF_CONFIRM]:
            raise HomeAssistantError("Set confirm to true to factory-reset the plug")
        await _coordinator_for_call(hass, call).async_factory_reset()

    async def handle_refresh_all(call: ServiceCall) -> None:
        await _coordinator_for_call(hass, call).async_refresh_all()

    hass.services.async_register(
        DOMAIN,
        "set_timer",
        handle_set_timer,
        schema=vol.Schema(
            {
                **common,
                vol.Required(CONF_ACTION): vol.In(("on", "off")),
                vol.Optional(CONF_SECONDS): vol.All(vol.Coerce(int), vol.Range(min=1, max=31_536_000)),
                vol.Optional(CONF_AT): cv.datetime,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "stop_timer",
        handle_stop_timer,
        schema=vol.Schema(common),
    )
    hass.services.async_register(
        DOMAIN,
        "set_schedule",
        handle_set_schedule,
        schema=vol.Schema(
            {
                **common,
                vol.Optional(CONF_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
                vol.Optional(CONF_ACTIVE, default=True): cv.boolean,
                vol.Required(CONF_ACTION): vol.In(("on", "off")),
                vol.Optional(CONF_WEEKDAYS, default=[]): vol.Any(cv.string, [cv.string]),
                vol.Required(CONF_AT): cv.datetime,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "remove_schedule",
        handle_remove_schedule,
        schema=vol.Schema(
            {
                **common,
                vol.Required(CONF_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "set_random_mode",
        handle_set_random,
        schema=vol.Schema(
            {
                **common,
                vol.Required(CONF_ACTIVE): cv.boolean,
                vol.Optional(CONF_WEEKDAYS, default=[]): vol.Any(cv.string, [cv.string]),
                vol.Required(CONF_START): cv.time,
                vol.Required(CONF_END): cv.time,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "change_pin",
        handle_change_pin,
        schema=vol.Schema(
            {
                **common,
                vol.Required(CONF_NEW_PIN): vol.All(cv.string, vol.Match(r"^\d{4}$")),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "reset_pin",
        handle_reset_pin,
        schema=vol.Schema({**common, vol.Required(CONF_CONFIRM): cv.boolean}),
    )
    hass.services.async_register(
        DOMAIN,
        "reset_consumption",
        handle_reset_consumption,
        schema=vol.Schema({**common, vol.Required(CONF_CONFIRM): cv.boolean}),
    )
    hass.services.async_register(
        DOMAIN,
        "factory_reset",
        handle_factory_reset,
        schema=vol.Schema({**common, vol.Required(CONF_CONFIRM): cv.boolean}),
    )
    hass.services.async_register(
        DOMAIN,
        "refresh_all",
        handle_refresh_all,
        schema=vol.Schema(common),
    )
    domain_data[_SERVICE_MARKER] = True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    mac_address = entry.data[CONF_MAC]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, mac_address, connectable=True
    )

    coordinator = VoltcraftDataUpdateCoordinator(
        hass,
        entry,
        mac_address,
        ble_device.name if ble_device is not None else None,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady as err:
        _LOGGER.warning(
            "Initial Voltcraft connection unavailable; loading entities and retrying: %s",
            err,
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if isinstance(coordinator, VoltcraftDataUpdateCoordinator):
            await coordinator.async_shutdown()
    return unload_ok
