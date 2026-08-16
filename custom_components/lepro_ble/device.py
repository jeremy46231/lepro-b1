"""Connection handling for a single Lepro bulb."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from time import monotonic
from typing import TYPE_CHECKING

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

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

# A backstop, not a policy. establish_connection is allowed its full retry
# budget, which is four attempts of 20s each, so this only exists so a wedged
# connect cannot sit on the lock forever. Do not tighten it to bound how long
# connecting takes: BlueZ regularly fails the first attempt after a scan and
# needs the retries, and nothing waits on this any more.
_CONNECT_TIMEOUT = 150.0

# A bulb switched off at the wall cannot be reached at all, so back off rather
# than hammering the adapter.
_RECONNECT_FIRST_DELAY = 5.0
_RECONNECT_MAX_DELAY = 300.0

# A bulb in range advertises several times a second, so a single advert says
# nothing new. Only one arriving after a gap this long means it has come back.
_ABSENT_AFTER = 60.0


# One adapter cannot bring up two LE connections at once while it is also
# scanning. Overlapping attempts busy each other out, which the kernel reports
# as "Opcode 0x200e failed: -16" on the connection cancel, and both time out.
# With two bulbs retrying forever that deadlocks: neither ever wins. Holding
# this across the whole attempt means a wedged bulb can delay the other by the
# retry budget, which is the cheaper problem, since a bulb only needs the lock
# until it connects and then holds its connection without it.
_ADAPTER = asyncio.Lock()


async def _disconnect_quietly(client: BleakClientWithServiceCache) -> None:
    with suppress(Exception):
        await client.disconnect()


class BulbNotSeen(RuntimeError):
    """The bulb has not advertised yet, so there is nothing to connect to."""


class LeproBulb:
    """Talks to one bulb, reconnecting on its own when the bulb comes back."""

    def __init__(
        self, hass: HomeAssistant, ble_device: BLEDevice | None, mac: str
    ) -> None:
        self._hass = hass
        self._ble_device = ble_device
        self._mac = mac
        self._key = device_key(mac)
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._sn = 1
        self._callbacks: list[Callable[[], None]] = []
        self._closing = False
        self._reconnect_task: asyncio.Task | None = None
        self._seen = asyncio.Event()
        # -inf so the first advert always counts as the bulb arriving, however
        # long the machine has been up.
        self._last_advert = float("-inf")
        self.state: dict = {}

    @property
    def mac(self) -> str:
        return self._mac

    @property
    def available(self) -> bool:
        return self._client is not None and self._client.is_connected

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Adopt a fresher BLEDevice, and note if the bulb has just come back.

        A power cycled bulb advertises again before anything else, so that is
        the earliest moment worth retrying. But a bulb that is present and
        simply refusing connections advertises just as eagerly, and waking on
        every advert from one of those retries as fast as it broadcasts and
        starves the adapter, so only a gap ending here counts.
        """
        now = monotonic()
        came_back = now - self._last_advert > _ABSENT_AFTER
        self._last_advert = now
        self._ble_device = ble_device
        if self.available:
            return
        if came_back:
            self._seen.set()
        self._schedule_reconnect()

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
        self._schedule_reconnect()

    async def _connect(self) -> BleakClientWithServiceCache:
        if self._client is not None and self._client.is_connected:
            return self._client
        if self._ble_device is None:
            raise BulbNotSeen(f"{self._mac} has not advertised yet")
        _LOGGER.debug("%s: connecting", self._mac)
        client: BleakClientWithServiceCache | None = None
        try:
            # Lock first, so time spent queued behind the other bulb does not
            # count against this attempt's budget.
            async with _ADAPTER, asyncio.timeout(_CONNECT_TIMEOUT):
                # Leave max_attempts at its default. BlueZ often aborts the
                # first connect after a scan, and those retries are what make
                # it work at all; capping them to one attempt made both bulbs
                # permanently unavailable. Setup no longer waits on this, so
                # the attempts cost nothing but time in a background task.
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self._mac,
                    self._handle_disconnect,
                    use_services_cache=True,
                )
                await client.start_notify(NOTIFY_UUID, self._handle_notify)
        except BaseException:
            # Half open connections are the expensive failure here: the bulb
            # only has one slot, and it would hold it for a client we have
            # thrown away. Hand the cleanup to a task, since this scope may
            # already be cancelled and could not await anything itself.
            if client is not None:
                self._hass.async_create_background_task(
                    _disconnect_quietly(client), f"lepro_ble cleanup {self._mac}"
                )
            raise
        self._client = client
        _LOGGER.debug("%s: connected", self._mac)
        self._notify_listeners()
        return client

    def _schedule_reconnect(self) -> None:
        if self._closing or self._reconnect_task is not None:
            return
        self._reconnect_task = self._hass.async_create_background_task(
            self._reconnect(), f"lepro_ble reconnect {self._mac}"
        )

    async def _reconnect(self) -> None:
        """Retry until the bulb answers, backing off while it stays silent."""
        delay = _RECONNECT_FIRST_DELAY
        try:
            while not self._closing and not self.available:
                self._seen.clear()
                try:
                    async with self._lock:
                        if self._closing:
                            return
                        await self._connect()
                    return
                except Exception as err:  # noqa: BLE001 - any failure means retry
                    _LOGGER.debug("%s: reconnect failed (%s)", self._mac, err)
                try:
                    async with asyncio.timeout(delay):
                        await self._seen.wait()
                except TimeoutError:
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                else:
                    # It was away and is back, so the last failure says nothing
                    # about this attempt. Start the backoff over.
                    delay = _RECONNECT_FIRST_DELAY
        except asyncio.CancelledError:
            raise
        finally:
            self._reconnect_task = None

    def start(self) -> None:
        """Begin connecting in the background.

        Returns immediately. An unreachable bulb can take minutes to fail, and
        setup must not hold up the rest of Home Assistant waiting for a radio.
        """
        self._schedule_reconnect()

    async def async_disconnect(self) -> None:
        self._closing = True
        self._seen.set()
        if (task := self._reconnect_task) is not None:
            task.cancel()
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
