"""Lepro BLE integration."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_MAC, DOMAIN
from .device import LeproBulb

PLATFORMS = [Platform.LIGHT]

type LeproConfigEntry = ConfigEntry[LeproBulb]


async def async_setup_entry(hass: HomeAssistant, entry: LeproConfigEntry) -> bool:
    address = entry.unique_id
    assert address is not None

    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(f"Could not find Lepro bulb at {address}")

    bulb = LeproBulb(ble_device, entry.data[CONF_MAC])
    entry.runtime_data = bulb

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            lambda service_info, change: bulb.set_ble_device(service_info.device),
            {"address": address, "connectable": True},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    try:
        await bulb.async_connect()
    except Exception as err:
        raise ConfigEntryNotReady(f"Could not connect to {address}: {err}") from err

    entry.async_on_unload(bulb.async_disconnect)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LeproConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
