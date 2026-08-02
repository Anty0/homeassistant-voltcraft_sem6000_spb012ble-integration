from __future__ import annotations

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

    ble_device = bluetooth.async_ble_device_from_address(
        hass,
        mac_address,
        connectable=True,
    )
    if not ble_device:
        raise ConfigEntryNotReady(f"Device {mac_address} not found")

    coord = VoltcraftDataUpdateCoordinator(
        hass,
        entry,
        mac_address,
        ble_device.name,
    )

    # The coordinator now owns the BLE connection and reconnects it when needed.
    await coord.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coord

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coord: VoltcraftDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coord.async_shutdown()

    return unload_ok
