"""Config flow for Lepro BLE."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_MAC, DOMAIN
from .protocol import MANUFACTURER_ID, mac_from_manufacturer_data


def _mac_for(discovery: BluetoothServiceInfoBleak) -> str | None:
    """Prefer the MAC embedded in the advertisement over the adapter address.

    They are the same on Linux, but macOS reports a CoreBluetooth UUID, and the
    MAC is the only input to the encryption key.
    """
    data = discovery.manufacturer_data.get(MANUFACTURER_ID)
    if data and (mac := mac_from_manufacturer_data(data)):
        return mac
    if len(discovery.address.split(":")) == 6:
        return discovery.address
    return None


def _title(discovery: BluetoothServiceInfoBleak, mac: str) -> str:
    return f"Lepro {mac.replace(':', '')[-6:]}"


class LeproConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover bulbs over Bluetooth, or let the user pick one."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if _mac_for(discovery_info) is None:
            return self.async_abort(reason="no_mac")
        self._discovery = discovery_info
        self.context["title_placeholders"] = {
            "name": _title(discovery_info, _mac_for(discovery_info))
        }
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None
        mac = _mac_for(self._discovery)
        if user_input is not None:
            return self.async_create_entry(
                title=_title(self._discovery, mac),
                data={CONF_MAC: mac},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": _title(self._discovery, mac), "mac": mac},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input["address"]
            discovery = self._discovered[address]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            mac = _mac_for(discovery)
            return self.async_create_entry(
                title=_title(discovery, mac), data={CONF_MAC: mac}
            )

        current = self._async_current_ids()
        for discovery in async_discovered_service_info(self.hass, False):
            if discovery.address in current or discovery.address in self._discovered:
                continue
            if MANUFACTURER_ID in discovery.manufacturer_data:
                self._discovered[discovery.address] = discovery

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("address"): vol.In(
                        {
                            address: _title(discovery, _mac_for(discovery))
                            for address, discovery in self._discovered.items()
                        }
                    )
                }
            ),
        )
