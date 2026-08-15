"""Lepro BLE v2 wire protocol.

Reversed from libiot-core.so in com.lepro.home 1.0.9.262. See PROTOCOL.md.
No Home Assistant imports here on purpose, so this stays testable standalone.
"""

from __future__ import annotations

import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SERVICE_UUID = "1e2aa501-7292-4263-a8f1-be907f039a1f"
WRITE_UUID = "1e2aa502-7292-4263-a8f1-be907f039a1f"
NOTIFY_UUID = "1e2aa503-7292-4263-a8f1-be907f039a1f"

ADVERTISED_SERVICE_UUID = "0000a501-0000-1000-8000-00805f9b34fb"
MANUFACTURER_ID = 0x504C

# .rodata of libiot-core.so, 0x4cbba and 0x4cbda
_KEY_CONST = bytes.fromhex("34aeb43522130ecff4d2525e6c290bf9")
_IV = bytes.fromhex("50e23b111cc0ae8731f5f3e773abc476")

_MAGIC = 0x5A
_FRAG_SINGLE = 0x50

CMD_DP_VALUE = 0x1100
CMD_DP_VALUE_ACK = 0x1101
CMD_GET_DP_STATE = 0x1102
CMD_STATE_REPORT = 0x2100

HUE_MAX = 360
SAT_MAX = 1000
VAL_MAX = 1000
BRIGHTNESS_MIN = 100
BRIGHTNESS_MAX = 1000

# The app annotates d4 as 100..1000, but bulbs report 0, so trust the hardware.
TEMP_MIN = 0
TEMP_MAX = 1000

_CRC_TABLE: list[int] = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xA001 if _c & 1 else _c >> 1
    _CRC_TABLE.append(_c)


def crc16(data: bytes) -> int:
    """CRC-16/ARC, big-endian on the wire."""
    crc = 0
    for byte in data:
        crc = _CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc


def device_key(mac: str) -> bytes:
    """AES key for a bulb. Derived entirely from its MAC."""
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError(f"not a MAC: {mac!r}")
    return raw[3:6] + _KEY_CONST[3:16]


def mac_from_manufacturer_data(data: bytes) -> str | None:
    """Pull the MAC out of a 0x504C manufacturer payload (company id stripped).

    Needed on platforms where the adapter hides the hardware address.
    """
    if len(data) < 9:
        return None
    return ":".join(f"{b:02X}" for b in data[3:9])


def encrypt(plain: bytes, key: bytes) -> bytes:
    body = plain + b"\x00"
    pad = 16 - len(body) % 16
    body += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(_IV)).encryptor()
    return enc.update(body) + enc.finalize()


def decrypt(data: bytes, key: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(_IV)).decryptor()
    out = dec.update(data) + dec.finalize()
    if out and 1 <= out[-1] <= 16:
        out = out[: -out[-1]]
    return out.rstrip(b"\x00")


def build_frame(cmd: int, payload: bytes, sn: int) -> bytes:
    body = (
        bytes([_MAGIC, _FRAG_SINGLE])
        + (sn & 0xFFFF).to_bytes(2, "big")
        + cmd.to_bytes(2, "big")
        + len(payload).to_bytes(2, "big")
        + payload
    )
    return crc16(body).to_bytes(2, "big") + body


def build_dp_frame(dps: dict, key: bytes, sn: int) -> bytes:
    body = json.dumps(dps, separators=(",", ":")).encode()
    return build_frame(CMD_DP_VALUE, encrypt(body, key), sn)


class ProtocolError(Exception):
    """Malformed frame."""


def parse_frame(packet: bytes, key: bytes) -> tuple[int, dict | bytes]:
    """Return (cmd, decoded payload). Payload is a dict when it is JSON."""
    if len(packet) < 10:
        raise ProtocolError(f"runt frame: {packet.hex()}")
    if packet[2] != _MAGIC:
        raise ProtocolError(f"bad magic {packet[2]:#04x}")
    if int.from_bytes(packet[0:2], "big") != crc16(packet[2:]):
        raise ProtocolError("crc mismatch")

    cmd = int.from_bytes(packet[6:8], "big")
    plain = decrypt(packet[10:], key)
    if not plain:
        return cmd, b""
    try:
        return cmd, json.loads(plain)
    except ValueError:
        return cmd, plain


def encode_hsv(hue: int, sat: int, val: int) -> str:
    """d5 is three 4-digit hex fields: hue 0-360, sat 0-1000, val 0-1000."""
    return f"{hue:04X}{sat:04X}{val:04X}"


def decode_hsv(value: str) -> tuple[int, int, int]:
    if len(value) != 12:
        raise ValueError(f"bad d5: {value!r}")
    return int(value[0:4], 16), int(value[4:8], 16), int(value[8:12], 16)
