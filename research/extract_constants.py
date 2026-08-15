#!/usr/bin/env python3
"""Recover the Lepro AES constants from libiot-core.so and prove they are right.

    ./research/extract_constants.py path/to/libiot-core.so

The two constants live in .rodata and are combined with three bytes of the
bulb's MAC in init_msg (see disassembly.md). Rather than trusting the offsets
from one build of the app, this searches for them and verifies against a real
captured packet, so it still works if a future release moves them.

How the search works: in CBC, every block after the first decrypts using only
the key and the previous ciphertext block, with no dependence on the IV. So a
candidate key can be checked against blocks 2..n alone, and once the key is
known the IV falls straight out of the first block as D(C1) xor P1.
"""

from __future__ import annotations

import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Captured from a Lepro B1 at 44:1D:64:12:EA:D6 on connect (cmd 0x2100).
SAMPLE_MAC = "44:1D:64:12:EA:D6"
SAMPLE_FRAME = bytes.fromhex(
    "f29b5a50000621000040"
    "bcd72712d197933bbefb66ae2b104f8fbe1b1301dc5f9be62378"
    "4b76026687ab4bde9d993994d4c11ef7cb1bd59d80778146f5a3"
    "08ffe1e37b321ac2ec504d49"
)
SAMPLE_PLAINTEXT = b'{"d1":1,"d2":0,"d3":800,"d4":134,"d5":"012C01C203E8"}\x00' + b"\x0a" * 10

KNOWN_CONST_OFFSET = 0x4CBBA
KNOWN_IV_OFFSET = 0x4CBDA


def key_from(const: bytes, mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))[3:6] + const[3:16]


def _ecb_decrypt_block(key: bytes, block: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(block) + dec.finalize()


def key_matches(key: bytes, cipher_text: bytes, plaintext: bytes) -> bool:
    """Check blocks 2..n, which are independent of the IV."""
    for i in range(16, len(cipher_text), 16):
        got = _ecb_decrypt_block(key, cipher_text[i : i + 16])
        got = bytes(a ^ b for a, b in zip(got, cipher_text[i - 16 : i]))
        if got != plaintext[i : i + 16]:
            return False
    return True


def iv_from(key: bytes, cipher_text: bytes, plaintext: bytes) -> bytes:
    block = _ecb_decrypt_block(key, cipher_text[:16])
    return bytes(a ^ b for a, b in zip(block, plaintext[:16]))


def main(path: str) -> int:
    blob = open(path, "rb").read()
    cipher_text = SAMPLE_FRAME[10:]

    const = blob[KNOWN_CONST_OFFSET : KNOWN_CONST_OFFSET + 16]
    if key_matches(key_from(const, SAMPLE_MAC), cipher_text, SAMPLE_PLAINTEXT):
        offset = KNOWN_CONST_OFFSET
        print(f"key constant found at the expected offset {offset:#x}")
    else:
        print("expected offset did not verify, searching the whole file ...")
        offset = None
        for candidate in range(len(blob) - 16):
            key = key_from(blob[candidate : candidate + 16], SAMPLE_MAC)
            if key_matches(key, cipher_text, SAMPLE_PLAINTEXT):
                offset, const = candidate, blob[candidate : candidate + 16]
                break
        if offset is None:
            print("no key constant in this binary; wrong file or the scheme changed")
            return 1
        print(f"key constant relocated to {offset:#x}")

    key = key_from(const, SAMPLE_MAC)
    iv = iv_from(key, cipher_text, SAMPLE_PLAINTEXT)

    print(f"\nkey constant   {const.hex()}   at {offset:#x}")
    print(f"iv             {iv.hex()}")
    print(f"derived key    {key.hex()}   for {SAMPLE_MAC}")

    found_iv_at = blob.find(iv)
    print(f"iv located in .rodata at {found_iv_at:#x}" if found_iv_at >= 0 else "iv not a stored constant")

    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = dec.update(cipher_text) + dec.finalize()
    assert plain == SAMPLE_PLAINTEXT, plain
    print(f"\nverified against the captured packet:\n  {plain[:-10].rstrip(chr(0).encode()).decode()}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
