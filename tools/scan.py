#!/usr/bin/env python3
"""Find Lepro bulbs and decode their advertisements.

    ./tools/scan.py [seconds]

Prints the MAC, the derived AES key, and the flags the bulb is advertising. On
Linux the adapter address is the MAC; on macOS it is a CoreBluetooth UUID, which
is why both are shown, and why `leproctl` takes them separately.
"""

from __future__ import annotations

import asyncio
import sys

from _protocol import protocol as p
from bleak import BleakScanner


def describe(data: bytes) -> dict:
    """Decode a 0x504C manufacturer payload (company id already stripped).

    Layout from BluetoothUtils.parseBroadcastData, shifted by the two company
    id bytes that bleak removes.
    """
    flags = data[0]
    return {
        "bonded": bool(flags & 0x80),
        "requesting": bool(flags & 0x40),
        "fragmentation": bool(flags & 0x20),
        "version": int(f"{data[1]:02x}"),
        "cipher": data[2],
        "mac": p.mac_from_manufacturer_data(data),
        "short_pid": int.from_bytes(data[9:13], "big") if len(data) >= 13 else None,
    }


async def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    found: dict[str, tuple] = {}

    def callback(device, adv):
        if p.MANUFACTURER_ID in adv.manufacturer_data:
            found[device.address] = (device, adv)

    print(f"scanning {seconds:g}s for manufacturer id {p.MANUFACTURER_ID:#06x} ...")
    async with BleakScanner(callback):
        await asyncio.sleep(seconds)

    if not found:
        print("no Lepro devices found (is the phone app holding the connection?)")
        return 1

    for device, adv in sorted(found.values(), key=lambda t: -t[1].rssi):
        info = describe(adv.manufacturer_data[p.MANUFACTURER_ID])
        mac = info["mac"]
        print(f"\n{adv.local_name or device.name or '?'}  rssi={adv.rssi}")
        print(f"  mac              {mac}")
        print(f"  ble address      {device.address}")
        if mac:
            print(f"  key              {p.device_key(mac).hex()}")
        print(f"  protocol version {info['version']}   cipher {info['cipher']}")
        print(
            f"  bonded {info['bonded']}   requesting {info['requesting']}   "
            f"fragmentation {info['fragmentation']}"
        )
        print(f"  short pid        {info['short_pid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
