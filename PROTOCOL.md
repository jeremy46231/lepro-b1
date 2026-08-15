# Lepro BLE protocol (v2)

Reversed from `com.lepro.home` 1.0.9.262, specifically `lib/arm64-v8a/libiot-core.so`.
Verified live against a Lepro B1 bulb (`44:1D:64:12:EA:D6`).

The Kotlin in `com.lepro.iotCore.ble` is the older v1 / WiFi-provisioning path. Actual
bulb control goes through JNI into `libiot-core.so`, which is where everything below
comes from. Useful symbols are not stripped: `le_msg_tx_data_ext`, `le_msg_rx`,
`le_aes_128_encrypt_cbc`, `init_msg`, `dpValue`, `getDpState`, `requestBond`.

## GATT

| UUID | Role |
| --- | --- |
| `1e2aa501-7292-4263-a8f1-be907f039a1f` | service |
| `1e2aa502-7292-4263-a8f1-be907f039a1f` | write / write-without-response |
| `1e2aa503-7292-4263-a8f1-be907f039a1f` | notify / indicate |

Advertised service UUID for scanning is the 16-bit alias `0000a501-...`, local name `LP`.

## Advertisement

Manufacturer data, company ID `0x504C` (ASCII "LP"). With the company ID prepended, the
full AD payload is:

```
[0:2]   4c 50            company id
[2]     flags            bit7 = bonded, bit6 = requesting, bit5 = supports fragmentation
[3]     version code     BCD-ish, parsed as decimal; 2 on the B1
[4]     cipher           1 = encrypted
[5:11]  MAC              big-endian, as printed
[11:15] short pid        uint32 BE
```

The MAC matters: macOS/CoreBluetooth hides device MACs, but it is right here in the
advertisement, and it is the only per-device input to the encryption key.

## Transport framing

Built by `le_msg_tx_data_ext`. All multi-byte fields big-endian.

```
[0:2]   CRC16 over bytes[2:]
[2]     0x5a magic
[3]     fragment type: 0x50 single, 0x51 first, 0x52 middle, 0x53 last
[4:6]   sn, increments per message
[6:8]   cmd
[8:10]  payload length
[10:]   payload (encrypted)
```

CRC is CRC-16/ARC (reflected poly 0xA001, init 0), emitted big-endian. Fragmentation
kicks in above `mtu - 3` bytes per packet; with a 512-byte MTU nothing realistic fragments.

## Encryption

AES-128-CBC, PKCS7, with a fixed IV. Both key and IV come from `.rodata` in
`libiot-core.so`, combined with three bytes of the MAC in `init_msg`:

```
const = 34aeb43522130ecff4d2525e6c290bf9      (0x4cbba)
iv    = 50e23b111cc0ae8731f5f3e773abc476      (0x4cbda)
key   = MAC[3:6] || const[3:16]
```

So for `44:1D:64:12:EA:D6` the key is `12ead6 3522130ecff4d2525e6c290bf9`.

There is no pairing handshake, no cloud key, and no per-session negotiation needed to
read or write state. Knowing the MAC is sufficient, and the MAC is broadcast in the clear.
`init_msg` does also build a second key that mixes in a locally generated random, but the
bulb accepts the plain MAC-derived key for both directions.

Plaintext is the JSON body plus a NUL terminator, then PKCS7 to a 16-byte boundary.

## Commands

Requests are `0x1xxx`, responses `0x2xxx`, and `0x1100` is answered with `0x1101`.

| cmd | meaning |
| --- | --- |
| `0x1000` | searchDeviceInfo |
| `0x1002` | requestBond |
| `0x1006` | getDeviceApInfo |
| `0x1008` | sendWifiMqttInfo |
| `0x100a` | getDeviceState |
| `0x1050` | quitOnboardingMode |
| `0x1100` | dpValue (set) |
| `0x1101` | dpValue ack, empty payload |
| `0x1102` | getDpState |
| `0x2100` | state report, pushed unprompted on connect and on change |
| `0x1010` / `0x1012` | OTA start / process |
| `0x3010` / `0x3013` | image transfer |

## Data points

The JSON schema matches `com.lepro.iotCore.ble.protocol.SetRequest`.

| key | meaning |
| --- | --- |
| `d1` | on/off, 0 or 1 |
| `d2` | work mode: 0 white, 1 color, 2 scene, 3 music |
| `d3` | white brightness, 100..1000 |
| `d4` | white temperature, 0..1000, linear over 2700..6500 K (see below) |
| `d5` | HSV as three 4-digit hex fields: hue 0..360, sat 0..1000, val 0..1000 |
| `d6` | scene string |
| `d30` | trace id, 7 random chars; the app always sends it, the bulb does not require it |
| `d50` | scene string |
| `d52` | RGBIC max brightness, 100..1000 |

Red is `d5 = "000003E803E8"`, green `"007803E803E8"`, blue `"00F003E803E8"`.

`le_strip_t2color` in `libiot-core.so` converts `d4` to a colour temperature as
`kelvin = d4 * 3800 / 1000 + 2700`, with `d4 == 1000` special-cased to 6500 K. So the
scale is exactly linear from 2700 K at 0 to 6500 K at 1000, higher being cooler. That
matches the range printed on the B1 packaging. The app's `@IntRange` annotation claims a
floor of 100 for this data point, but bulbs report 0, so 0 is valid.

## Notes for a Home Assistant integration

- The bulb pushes `0x2100` with full state on connect, so no polling is needed for an
  initial value.
- It accepts one connection at a time, so the phone app must not be holding it.
- On Linux, bleak addresses devices by MAC, which is also the key input; on macOS you get
  a CoreBluetooth UUID and have to read the MAC out of the manufacturer data.
- Writes use write-without-response and are acked at the protocol layer by `0x1101`.
