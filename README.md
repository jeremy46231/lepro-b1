# Lepro BLE

Local Bluetooth control for Lepro smart bulbs, as a Home Assistant custom integration.
No cloud account, no WiFi, no pairing step. See [PROTOCOL.md](PROTOCOL.md) for how the
protocol was reversed out of the Android app.

Developed against a pair of Lepro B1 bulbs (800 lm, 2700-6500 K). Other Lepro bulbs
advertising manufacturer ID `0x504C` with version code 2 should work, though only the
B1 has been tested.

```
custom_components/lepro_ble/   the Home Assistant integration
tools/                         command line scanner and controller
research/                      how the protocol was recovered, and a script that proves it
PROTOCOL.md                    the wire format
```

## Install

Copy `custom_components/lepro_ble` into your Home Assistant `config/custom_components/`
directory and restart. The bulbs are discovered automatically over Bluetooth, so you
should get a notification for each one; otherwise add the integration by hand from
Settings -> Devices & Services.

Your HA host needs a Bluetooth adapter within range of the bulbs. An ESPHome Bluetooth
proxy works too, since this uses HA's own Bluetooth stack.

Close the Lepro phone app first. The bulbs accept only one connection at a time, and
whoever gets there first keeps it.

## What is exposed

One light entity per bulb, with on/off, brightness, colour (HS), and colour temperature.

The bulb pushes its full state on connect and whenever something changes it, so the
entity stays in sync even when the bulb is changed from a physical switch or another app.

Colour temperature is exact, not guessed. `d4` runs 0-1000 and the app converts it with
`kelvin = d4 * 3800 / 1000 + 2700`, so the range is 2700-6500 K with higher values
cooler, which matches what the B1 packaging claims. If you have a model with a different
range, adjust `MIN_KELVIN` and `MAX_KELVIN` in `const.py`.

## Using the protocol without Home Assistant

`custom_components/lepro_ble/protocol.py` has no Home Assistant imports. It needs only
`cryptography`, and pairs with `bleak` for transport:

```python
import asyncio, json
from bleak import BleakClient
import protocol as p

MAC = "44:1D:64:12:EA:D6"
key = p.device_key(MAC)

async def main():
    async with BleakClient(MAC) as client:
        await client.start_notify(
            p.NOTIFY_UUID,
            lambda _c, d: print(p.parse_frame(bytes(d), key)),
        )
        frame = p.build_dp_frame({"d1": 1, "d2": 1, "d5": p.encode_hsv(0, 1000, 1000)}, key, 1)
        await client.write_gatt_char(p.WRITE_UUID, frame, response=False)
        await asyncio.sleep(2)

asyncio.run(main())
```

The `sn` argument is a per-connection sequence number; increment it for each message.

## Command line tools

```sh
pip install -r tools/requirements.txt

./tools/scan.py                       # find bulbs, print MAC, key and advertised flags
./tools/leproctl.py <mac> --on --brightness 800
./tools/leproctl.py <mac> --color 120,1000,1000
./tools/leproctl.py <mac> --raw '{"d1":1,"d2":0,"d3":500}'
./tools/leproctl.py <mac> --watch     # stay connected and print state reports
```

Both import the integration's `protocol.py` directly, so there is only ever one
implementation of the wire format. On macOS the adapter reports a CoreBluetooth UUID
instead of a hardware address, so pass `--address` with the value `scan.py` prints; the
MAC is still needed separately because the key derives from it.

## Verifying the reverse engineering

`research/extract_constants.py` takes `libiot-core.so` out of the APK and recovers the
AES constants from it, checking them against a packet captured from a real bulb. It does
not trust the offsets from one build: if they do not verify it searches the binary for
the key, then recovers the IV from the known plaintext.

```sh
./research/extract_constants.py path/to/libiot-core.so
```

`research/disassembly.md` has the annotated excerpts behind each claim in `PROTOCOL.md`,
plus the commands to regenerate everything from the published APK.

## A note on the security model

The encryption key is three bytes of the bulb's MAC address appended to a constant
compiled into the app, and the bulb broadcasts its own MAC in every advertisement. So
anyone in Bluetooth range can derive the key and take over the bulb. That is convenient
here and is why this integration needs no pairing, but it is worth knowing about.
