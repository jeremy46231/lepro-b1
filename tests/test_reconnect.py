"""Reconnect policy tests, with a fake radio and no Home Assistant.

device.py only imports Home Assistant under TYPE_CHECKING, so it can be loaded
directly and driven with a stub hass. Run with:

    python -m venv .venv && .venv/bin/pip install bleak bleak-retry-connector cryptography
    .venv/bin/python tests/test_reconnect.py
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
from time import monotonic

_ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "lepro_ble"


def _load(name: str):
    """Load a module out of the component without importing the package."""
    pkg = sys.modules.setdefault("_lepro", types.ModuleType("_lepro"))
    pkg.__path__ = [str(_ROOT)]
    spec = importlib.util.spec_from_file_location(f"_lepro.{name}", _ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"_lepro.{name}"] = module
    spec.loader.exec_module(module)
    return module


D = _load("device")

MAC = "44:1D:64:14:EE:06"


class FakeHass:
    def async_create_background_task(self, coro, name):
        return asyncio.create_task(coro, name=name)


def shrink() -> None:
    """Production shape, test timescale."""
    D._RECONNECT_FIRST_DELAY = 0.10
    D._RECONNECT_MAX_DELAY = 0.80
    D._ABSENT_AFTER = 0.50
    D._CONNECT_TIMEOUT = 0.30


async def advertise(bulb, period: float, duration: float) -> None:
    end = monotonic() + duration
    while monotonic() < end:
        bulb.set_ble_device(object())
        await asyncio.sleep(period)


async def test_present_but_unconnectable() -> None:
    """A bulb in range that refuses connections must not be hammered.

    This is the regression: adverts used to short circuit the backoff, and
    since a bulb in range advertises several times a second the retry loop
    never actually backed off and saturated the adapter.
    """
    attempts: list[float] = []

    async def fail(*args, **kwargs):
        attempts.append(monotonic())
        raise OSError("le-connection-abort-by-local")

    D.establish_connection = fail
    bulb = D.LeproBulb(FakeHass(), object(), MAC)
    bulb.start()
    await advertise(bulb, 0.02, 2.0)
    await asyncio.sleep(0.05)
    await bulb.async_disconnect()

    assert len(attempts) <= 10, f"backoff not engaging: {len(attempts)} attempts"
    gaps = [round(b - a, 2) for a, b in zip(attempts, attempts[1:])]
    assert gaps == sorted(gaps), f"backoff not monotonic: {gaps}"
    print(f"  {len(attempts)} attempts from ~100 adverts, gaps {gaps}")


async def test_absent_then_returns() -> None:
    """A bulb switched off at the wall is picked up as soon as it is back."""
    attempts: list[float] = []

    async def fail(*args, **kwargs):
        attempts.append(monotonic())
        raise OSError("device not found")

    D.establish_connection = fail
    bulb = D.LeproBulb(FakeHass(), object(), MAC)
    bulb.start()
    await asyncio.sleep(2.0)  # silence, so the backoff reaches its ceiling
    settled = len(attempts)

    mark = monotonic()
    bulb.set_ble_device(object())
    await asyncio.sleep(0.08)
    woke = [t for t in attempts[settled:] if t >= mark]
    await bulb.async_disconnect()

    assert woke, "did not retry promptly when the bulb came back"
    print(f"  {settled} attempts while absent, retried within 80ms of return")


async def test_no_device_until_advert() -> None:
    """A bulb that has never advertised still sets up, then connects later.

    Setup must not fail in this case, or the entity would not exist at all and
    would drop out of dashboards and automations rather than going unavailable.
    """
    connected = asyncio.Event()

    class Client:
        is_connected = True

        async def start_notify(self, *args, **kwargs):
            connected.set()

        async def disconnect(self):
            pass

    async def ok(*args, **kwargs):
        return Client()

    D.establish_connection = ok
    bulb = D.LeproBulb(FakeHass(), None, MAC)
    bulb.start()
    await asyncio.sleep(0.25)
    assert not bulb.available, "cannot be available without ever seeing the bulb"

    bulb.set_ble_device(object())
    await asyncio.wait_for(connected.wait(), 1.0)
    await asyncio.sleep(0)
    assert bulb.available
    await bulb.async_disconnect()
    print("  unavailable with no device, connected once it advertised")


async def test_connect_timeout() -> None:
    """A wedged connect must not hold the lock and stall commands."""

    async def hang(*args, **kwargs):
        await asyncio.sleep(60)

    D.establish_connection = hang
    bulb = D.LeproBulb(FakeHass(), object(), MAC)
    start = monotonic()
    try:
        await bulb._connect()
        raise AssertionError("expected a timeout")
    except (TimeoutError, asyncio.TimeoutError):
        pass
    elapsed = monotonic() - start
    assert elapsed < D._CONNECT_TIMEOUT + 0.2, elapsed
    print(f"  abandoned after {elapsed:.2f}s")


async def test_half_open_cleanup() -> None:
    """A failed start_notify must release the bulb's single connection slot."""
    disconnected = asyncio.Event()

    class Client:
        is_connected = True

        async def start_notify(self, *args, **kwargs):
            raise OSError("notify failed")

        async def disconnect(self):
            disconnected.set()

    async def ok(*args, **kwargs):
        return Client()

    D.establish_connection = ok
    bulb = D.LeproBulb(FakeHass(), object(), MAC)
    try:
        await bulb._connect()
        raise AssertionError("expected start_notify to propagate")
    except OSError:
        pass
    await asyncio.wait_for(disconnected.wait(), 1.0)
    print("  half open connection was disconnected")


async def main() -> None:
    shrink()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        print(f"== {test.__name__}")
        await test()
        print("   PASS")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    asyncio.run(main())
