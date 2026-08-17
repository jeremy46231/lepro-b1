"""Lepro BLE integration."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_MAC, DOMAIN
from .device import LeproBulb

PLATFORMS = [Platform.LIGHT]

type LeproConfigEntry = ConfigEntry[LeproBulb]


async def async_setup_entry(hass: HomeAssistant, entry: LeproConfigEntry) -> bool:
    address = entry.unique_id
    assert address is not None

    # None when the bulb has not advertised yet, which is not a setup failure.
    # Raising ConfigEntryNotReady here would mean no entity at all until the
    # bulb turns up, so it would drop out of dashboards and break automations
    # that reference it. Set up regardless and let it report unavailable;
    # set_ble_device fills this in as soon as the bulb advertises.
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)

    bulb = LeproBulb(hass, ble_device, entry.data[CONF_MAC])
    entry.runtime_data = bulb

    entry.async_on_unload(bulb.async_disconnect)
    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            lambda service_info, change: bulb.set_ble_device(service_info.device),
            {"address": address, "connectable": True},
            # Passive. Everything this integration reads, the MAC in the
            # manufacturer data and the fact the bulb is alive, is in the
            # advertisement itself; nothing comes from a scan response. Asking
            # for active scanning made the adapter solicit responses it then
            # had to process while also holding GATT connections, which it
            # cannot keep up with.
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )

    # Not awaited: a bulb that is switched off, or in range but refusing
    # connections, takes minutes to fail, and blocking here blocks the whole of
    # Home Assistant starting. It is an unavailable entity, not a setup failure,
    # and it connects by itself once the bulb answers.
    bulb.start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LeproConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
