# Where the protocol comes from

Annotated excerpts from `libiot-core.so` (arm64) in `com.lepro.home` 1.0.9.262,
covering what you need to reimplement the protocol: the key, the framing, the
command numbers, and the colour temperature scale. Everything here is
reproducible from a public APK with the steps below.

Only short excerpts are quoted, and only the parts load-bearing for
interoperability. The app's own decompiled source is deliberately not checked in.

## Reproducing

```sh
apkeep -a com.lepro.home -d apk-pure .
unzip -q com.lepro.home.xapk -d xapk
unzip -q xapk/config.arm64_v8a.apk -d lib 'lib/arm64-v8a/libiot-core.so'
unzip -q xapk/com.lepro.home.apk -d base 'classes*.dex'

objdump -d lib/lib/arm64-v8a/libiot-core.so > iot.asm   # symbols are intact
jadx -q --no-res -d jadx_out base/classes3.dex base/classes4.dex
```

The Kotlin under `com.lepro.iotCore.ble` in the dex is the v1 / WiFi provisioning
path and does not control the bulb. It is still worth reading for
`BleConstants` (the GATT UUIDs) and `protocol.SetRequest` (the data point names,
which is how `d1`..`d52` got their meanings). Everything else below is native.

Relevant exported symbols, none stripped:

```
le_msg_ctx_alloc  le_msg_rx  le_msg_tx_data  le_msg_tx_data_ext  le_msg_set_tx_sn
le_aes_128_encrypt_cbc  le_aes_128_decrypt_cbc
init_msg  dpValue  getDpState  requestBond  searchDeviceInfo  receiveCmd
```

## The key, in `init_msg`

The MAC arrives as a string, gets split on `:` and parsed byte by byte onto the
stack, then three of those bytes are pasted over the front of a 16-byte constant.

```asm
92110: bl   time     ; srand(time(0)), then rand() -> w22
92118: bl   rand
92154: bl   strtok   ; split the MAC string on ":" (delimiter at 0x490e0)
92170: bl   strtol   ; base 16
92174: strb w0, [x23], #0x1      ; MAC bytes land at x29-0x20 .. x29-0x1a

92188: adrp x8, ...              ; x8 = 0x4cbba, the 16-byte constant
9218c: ldurb w9,  [x29, #-0x1b]  ; MAC[5]
92198: ldr   q0,  [x8]           ; load the constant
9219c: ldurh w8,  [x29, #-0x1d]  ; MAC[3..4]
921a0: stur  q0,  [x21, #0x8]    ; ctx+0x08 = constant
921a4: stur  q0,  [x21, #0x19]   ; ctx+0x19 = constant
921a8: strh  w8,  [x21, #0x8]    ; ctx+0x08 = MAC[3..4]
921ac: strb  w9,  [x21, #0xa]    ; ctx+0x0a = MAC[5]
921b0: sturh w8,  [x21, #0x19]   ; ctx+0x19 = MAC[3..4]
921b4: strb  w9,  [x21, #0x1b]   ; ctx+0x1b = MAC[5]
```

So the key at `ctx+0x19` is `MAC[3:6] || const[3:16]`. That is the whole secret.

`init_msg` also seeds a random and stores it nearby, which looks like the start of
a session key, but the bulb accepts the plain MAC-derived key in both directions,
so nothing needs to be negotiated. `research/extract_constants.py` confirms this
against a real captured packet rather than taking the disassembly's word for it.

## The framing, in `le_msg_tx_data_ext`

Fields go out big-endian, via these helpers:

```asm
LMSU16:  rev w8, w1 / lsr w8, w8, #16 / strh w8, [x0]   ; store u16 big-endian
LMSU32:  rev w8, w1 / str w8, [x0]                      ; store u32 big-endian
```

The header is assembled back to front, with the CRC written last because it
covers everything after it:

```asm
8cda0: mov  w9, #0x5a
8cda8: strb w9, [x25, #0x2]!   ; [2] = 0x5a magic; x25 now points at [2]
8cdb8: strh w8, [x20, #0x4]    ; [4:6] = sn
8cdfc: strh w10,[x20, #0x8]    ; [8:10] = payload length
8ce00: strh w8, [x20, #0x6]    ; [6:8] = cmd
8ce14: mov  w8, #0x50
8ce18: strb w8, [x20, #0x3]    ; [3] = 0x50, single-packet fragment type
8cec8: bl   memcpy             ; payload to [10:]

8cee4: ldrb  w10, [x25], #0x1  ; CRC over bytes[2:], table at 0x4a6f0
8cef8: ldrh  w10, [x28, w10, uxtw #1]
8cefc: eor   w9, w10, w9, lsr #8
8cd78: strh  w8, [x20]         ; [0:2] = CRC
```

The other fragment type bytes are `0x51` first, `0x52` middle, `0x53` last, with
`0x54`/`0xd1`/`0xd2` variants selected by flag bits. Fragmentation only kicks in
above `mtu - 3` bytes, so with a negotiated 512-byte MTU nothing realistic splits.

## The decrypt path, in `receiveCmd`

Confirms the payload offset and that key and IV are exactly the two constants:

```asm
92454: add x22, x20, #0x19    ; key  = ctx+0x19
92458: mov x0, x25            ; x25  = packet+8
9245c: bl  LMGU16             ; length field at [8:10]
92460: add x20, x21, #0xa     ; payload starts at [10:]
92464: adrp x1, ...           ; x1 = 0x4cbda, the IV
9247c: bl  le_aes_128_decrypt_cbc   ; (key, iv, len, in, out), decrypts in place
```

## The colour temperature scale, in `le_strip_t2color`

```asm
8a544: cmp  w1, #0x3e8         ; d4 == 1000?
8a54c: mov  w1, #0x1964        ;   then 6500 K exactly
8a554: mov  w8, #0xed8         ; else 3800
8a560: mul  w8, w1, w8         ;   d4 * 3800
8a558: mov  w9, #0x4dd3        ; 0x10624dd3, the magic number for
8a55c: movk w9, #0x1062, lsl #16
8a564: smull x8, w8, w9        ;   ... division by 1000
8a56c: asr   x8, x8, #38
8a574: add   w1, w8, #0xa8c    ;   + 2700
```

`kelvin = d4 * 3800 / 1000 + 2700`, so 0..1000 maps linearly onto 2700..6500 K.

## The command numbers

Each protocol call ends in `le_msg_tx_data`, whose second argument is the command.
Listing the call sites next to the enclosing symbol gives the table in
`PROTOCOL.md`:

```sh
grep -nE 'bl .* <le_msg_tx_data' iot.asm | while IFS=: read n _; do
  sed -n "$((n-12)),${n}p" iot.asm | grep -E 'mov\s+w1, #'
done
```

`dpValue` sends `0x1100` and the bulb answers `0x1101`; unsolicited state reports
arrive as `0x2100`.
