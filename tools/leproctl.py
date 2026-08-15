#!/usr/bin/env python3
"""Drive a Lepro bulb from the command line, without Home Assistant.

    ./tools/leproctl.py 44:1D:64:12:EA:D6 --on --brightness 800
    ./tools/leproctl.py 44:1D:64:12:EA:D6 --color 0,1000,1000
    ./tools/leproctl.py 44:1D:64:12:EA:D6 --raw '{"d1":1,"d2":0,"d3":500}'
    ./tools/leproctl.py 44:1D:64:12:EA:D6 --watch

The bulb pushes its state on connect, so every run prints the current state
first. On macOS pass --address with the CoreBluetooth UUID from scan.py; the MAC
is still required because it is what the key is derived from.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from _protocol import protocol as p
from bleak import BleakClient, BleakScanner


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mac", help="bulb MAC, as printed by scan.py")
    ap.add_argument("--address", help="BLE address to connect to, if not the MAC")
    ap.add_argument("--on", dest="switch", action="store_const", const=1)
    ap.add_argument("--off", dest="switch", action="store_const", const=0)
    ap.add_argument("--brightness", type=int, help="white brightness, 100-1000")
    ap.add_argument("--temperature", type=int, help="white temperature, 100-1000")
    ap.add_argument("--color", help="hue,sat,val as 0-360,0-1000,0-1000")
    ap.add_argument("--raw", help="raw data point JSON, merged last")
    ap.add_argument("--watch", action="store_true", help="stay connected and print reports")
    return ap.parse_args()


def build_dps(args: argparse.Namespace) -> dict:
    dps: dict = {}
    if args.switch is not None:
        dps["d1"] = args.switch
    if args.brightness is not None:
        dps["d2"] = 0
        dps["d3"] = args.brightness
    if args.temperature is not None:
        dps["d2"] = 0
        dps["d4"] = args.temperature
    if args.color:
        hue, sat, val = (int(part) for part in args.color.split(","))
        dps["d2"] = 1
        dps["d5"] = p.encode_hsv(hue, sat, val)
    if args.raw:
        dps.update(json.loads(args.raw))
    return dps


async def main() -> int:
    args = parse_args()
    key = p.device_key(args.mac)
    target = args.address or args.mac

    device = await BleakScanner.find_device_by_address(target, timeout=20.0)
    if device is None:
        print(f"not found: {target}")
        return 1

    def on_notify(_char, data: bytearray):
        try:
            cmd, payload = p.parse_frame(bytes(data), key)
        except p.ProtocolError as err:
            print(f"<- unparseable ({err}): {bytes(data).hex()}")
            return
        if cmd == p.CMD_DP_VALUE_ACK:
            print("<- ack")
        else:
            print(f"<- {cmd:#06x} {payload}")

    async with BleakClient(device) as client:
        await client.start_notify(p.NOTIFY_UUID, on_notify)
        await asyncio.sleep(1.5)

        dps = build_dps(args)
        if dps:
            print(f"-> {dps}")
            await client.write_gatt_char(
                p.WRITE_UUID, p.build_dp_frame(dps, key, 1), response=False
            )
            await asyncio.sleep(1.5)

        if args.watch:
            print("watching, ctrl-c to stop")
            while True:
                await asyncio.sleep(3600)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
