"""Connection handling for a single Lepro bulb."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .protocol import (
    CMD_STATE_REPORT,
    NOTIFY_UUID,
    WRITE_UUID,
    ProtocolError,
    build_dp_frame,
    device_key,
    parse_frame,
)

_LOGGER = logging.getLogger(__name__)

# The bulb accepts one connection at a time, so hold it rather than reconnecting
# per command; that also keeps push state reports flowing.
_COMMAND_TIMEOUT = 10.0


class LeproBulb:
    """Talks to one bulb, keeping a connection open while HA is running."""

    def __init__(self, ble_device: BLEDevice, mac: str) -> None:
        self._ble_device = ble_device
        self._mac = mac
        self._key = device_key(mac)
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._sn = 1
        self._callbacks: list[Callable[[], None]] = []
        self.state: dict = {}

    @property
    def mac(self) -> str:
        return self._mac

    @property
    def available(self) -> bool:
        return self._client is not None and self._client.is_connected

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Adopt a fresher BLEDevice from HA's bluetooth manager."""
        self._ble_device = ble_device

    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unregister() -> None:
            self._callbacks.remove(callback)

        return unregister

    def _notify_listeners(self) -> None:
        for callback in self._callbacks:
            callback()

    def _handle_notify(self, _char, data: bytearray) -> None:
        try:
            cmd, payload = parse_frame(bytes(data), self._key)
        except ProtocolError as err:
            _LOGGER.debug("%s: bad frame (%s): %s", self._mac, err, bytes(data).hex())
            return
        if cmd == CMD_STATE_REPORT and isinstance(payload, dict):
            self.state.update(payload)
            _LOGGER.debug("%s: state %s", self._mac, self.state)
            self._notify_listeners()

    def _handle_disconnect(self, _client) -> None:
        _LOGGER.debug("%s: disconnected", self._mac)
        self._client = None
        self._notify_listeners()

    async def _connect(self) -> BleakClientWithServiceCache:
        if self._client is not None and self._client.is_connected:
            return self._client
        _LOGGER.debug("%s: connecting", self._mac)
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self._mac,
            self._handle_disconnect,
            use_services_cache=True,
        )
        await client.start_notify(NOTIFY_UUID, self._handle_notify)
        self._client = client
        return client

    async def async_connect(self) -> None:
        async with self._lock:
            await self._connect()

    async def async_disconnect(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
            if client is not None:
                await client.disconnect()

    async def async_set_dp(self, **dps) -> None:
        """Send a data point update and optimistically fold it into state."""
        async with self._lock:
            client = await self._connect()
            frame = build_dp_frame(dps, self._key, self._sn)
            self._sn = (self._sn + 1) & 0xFFFF
            async with asyncio.timeout(_COMMAND_TIMEOUT):
                await client.write_gatt_char(WRITE_UUID, frame, response=False)
        self.state.update(dps)
        self._notify_listeners()
