from __future__ import annotations

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import VoltcraftDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    mac_address = entry.data[CONF_MAC]
    ble_device = bluetooth.async_ble_device_from_address(hass, mac_address)
    if not ble_device:
        raise ConfigEntryNotReady(f"Device {mac_address} not found")

    client = await establish_connection(
        BleakClient,
        ble_device,
        entry.entry_id,
    )

    coord = VoltcraftDataUpdateCoordinator(
        hass,
        client,
        mac_address,
        ble_device.name,
    )

    # Setup coordinator (start notifications)
    await coord.async_setup()

    # Perform initial data fetch
    await coord.async_config_entry_first_refresh()

    # Store coordinator in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coord

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coord: VoltcraftDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coord.async_shutdown()

    return unload_ok
