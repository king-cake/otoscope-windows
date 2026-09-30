# i4season Wi-Fi otoscope protocol (SoulEar)

Reverse-engineered from `libWifiCamera.so` in **SoulEar 1.0.120** (`com.i4season.bkCamera_soulear`, arm64), decompiled with Ghidra 12.1.4. Target hardware: Hopefox *Find T*. **Status: verified 2026-09-30 on a Find T** (vendor `YPC`, product `BK7231U-XRH-FBPRO`, fw `HKV41B`, SSID `Soulear-318eb`, camera at `192.168.1.1`): discovery, devinfo, licence get and OpenVideo work; MJPEG at ~17 fps. The JPEGs are 480×480 even though the video header says 640×480. `tools/i4season_probe.py` is a stdlib-only test client built from these notes.

The Java layer (`com.jni.WifiCamera`) is a thin JNI wrapper around `caInit()`/`caStart()`. All networking is native. The library speaks three protocol families at once. This document covers the main one (`newcamera_*` / `pro_handle`). The other two are listed at the end.

## Transport

| Direction | Port | Purpose |
|---|---|---|
| app → cam | UDP **10005** | request/response: devinfo, licence, parameters, time |
| app → cam | UDP **10006** | open video, change port, low-power |
| cam → app | UDP **10007** (app binds it) | announce (`type 3`) and events (`type 1`, `2`) |
| cam → app | UDP *ephemeral* (`picport`) | video, sent to the port given in *open video* |

Discovery: the app starts with the camera IP set to the broadcast address `192.168.1.255` and sends requests there with `SO_BROADCAST`. It switches to the IP the camera answers or announces from.

## Control packet (little-endian)

```
off size
 0   4   magic 0xFFEEFFEE          (bytes EE FF EE FF)
 4   2   seq                        request counter; reply echoes it
 6   2   cmd
 8   1   0x01 in requests
 9   1   status in replies (0 = OK)
10   2   payload length
12   …   payload
```

The app accepts a reply when magic, `seq` and `cmd` match. It retries up to 20 times with a 100 ms timeout (8 × 500 ms for some commands), using a fresh socket for each request.

| cmd | port | name | request payload | reply payload |
|---|---|---|---|---|
| 0x0001 | 10005 | DevinfoGet | – | 0x80 B, see below |
| 0x0002 | 10005 | LicGet | – | 0xB0 B: SN (32) + licence. Only the app checks it; the camera does not enforce it |
| 0x0004 | 10006 | **OpenVideo** | `u16 picport, u16 audioport, u32 client_id` (len 8, unicast); len 2 or 4 when broadcast | – |
| 0x000D | 10005 | ScreenParamsGet | – | 0x38 B (only if `capacity & 0x40`) |
| 0x000F | 10005 | set notify port | `u32 port` | – |
| 0x0014 | 10006 | ChangePort | `u16 proport` | – (only if `flags & 0x02`) |
| 0x0015 | 10006 | LowPowerMode | `u8 1` | – |
| 0x001A | 10005 | TimeSet | `u16 year, u8 mon, day, h, m, s` | – (only if `flags & 0x10`) |
| 0x001B | 10005 | RecordStatusSync | `u8` | – |
| 0x000A | 10005 | LED | `u8 op, u8 status, u8 brightness`; op = LED id 1, plus `0x10` for write; status 0 off / 1 on / 2 blink / 3 breathe | same 3 bytes = resulting state |
| 0x000C | ? | cameraCmd; 0x0003/0x0010/0x0012 control; 0x000E conf (resolution) set; 0x0011 exposure; 0x0013 parameters | | |

`client_id` is `time(NULL)` taken once at session start. The camera uses it to tell clients apart (`isusedbyother`).

### Devinfo payload (0x80 B)

```
0x00  u8      protocol/version (behaviour branches on <4, <5)
0x01  char[32] vendor
0x21  char[32] product
0x41  char[16] firmware version
0x51  char[32] ssid
0x71  u8[6]   mac  (bytes 4..5 = "gMac", repeated in every video header)
0x77  u16     status: bits9..15 = battery % (byte 0x78 >> 1; 100 % observed as 0xC801), bit8 charging?, bit0 ?
0x79  u8      capacity (bit6 → ScreenParamsGet required)
0x7C  u8      flags: b0 camera_control, b1 changeport, b2 lowpower, b3 isusedbyother, b4 timeset, b5 audio, b6 recordStatusSync
```

### Verified on the Find T

- **LED**: write `11 01 64` = on, `11 00 00` = off. The camera stores and echoes any brightness 0..100, but the LED doesn't dim. The vendor app only ever sends 100 or 0.
- **Resolution** is fixed (480×480 JPEGs; the header claims 640×480). The resolution list (`0x000D`) and resolution set (`0x000E`) are only used when `capacity & 0x40`. The Find T reports `capacity = 0x03`, so the vendor library refuses (`cameraConfGet error3`).

## Session flow (as done by `pro_handle`)

1. DevinfoGet → broadcast `:10005` (retry until answered).
2. Optional TimeSet, ScreenParamsGet, RecordStatusSync, depending on the flags.
3. LicGet.
4. OpenVideo → `:10006` with the local `picport`.
5. ChangePort, if `flags & 0x02` (repeat every 16 s while no notify arrives).
6. Every second: if no video packet arrived since the last check, send OpenVideo again. This doubles as the keepalive. Re-read Devinfo on errors, and restart if `gMac` changes.

## Video packets (to `picport`)

Each UDP packet carries a JPEG chunk. The byte at offset 0 is the type: `1` = 16-byte header, `6` = 28-byte header, `5` = PCM audio, `2` = ?.

```
off
 0  u8   type
 1  u8   packet seq (global, +1 per packet; a gap drops the frame)
 2  u8   frame id
 3  u8   last-chunk flag
 4  u8   chunk count of the frame (checked on the last chunk)
 5  u8   flags: b0 has-gsensor (→ packed 3-axis in bytes 6..9), b1..2 ?, b3 ?   (Find T: 0x01)
 6  u32  gsensor (type 1: packed accel, see below)
10  u8[2] gMac
12  i16  width
14  i16  height
16  u8[12] (type 6 only) 6-axis data
```

Concatenate the chunks of one frame id. A frame is complete when `last != 0` and the number of chunks equals `count`. It must start with `FF D8`. The last chunk is zero-padded after `FF D9`, which decoders ignore. On the Find T a frame is ~12–14 chunks of ≤1400 B; about 7 % of frames were lost to Wi-Fi packet loss (sequence gaps) on a laptop.

### Packed accelerometer (flags bit0 set)

```
x = (g >> 20) & 0x1FF   xs = (g >> 29) & 1     (9-bit magnitude + sign bit, ~130 counts per g)
y = (g >> 10) & 0x1FF   ys = (g >> 19) & 1
z =  g        & 0x1FF   zs = (g >>  9) & 1
angle = atan2(±y, ±z) mod 360      (roll around the probe's long axis x)
slope = atan2(±x, hypot(y, z))
```

The vendor computes `atan(y/z)` and fixes the quadrant from the sign bits, which is equivalent to `atan2`. Verified on the device: a full turn around the long axis sweeps `angle` through 0–360° continuously, with |(y,z)| ≈ 110–145 and x ≈ 0–15.

If `z == 0`, the app keeps the previous angle. The app rotates the image by `angle`, plus 180° for products containing `FBPRO` or `R1` (the Find T reports `…-FBPRO`) and plus 90° for `w50` devices.

## Status push on UDP 10007

About once per second the camera sends a 29-byte packet with cmd `0x0009` and payload type `0x02` to the app's port 10007, e.g. `02 c8 01 00 00 00 00 6e b0 …`. Byte 1 `0xC8 >> 1` = battery 100 %, and bytes 7..8 are gMac. The type `0x03` payload is a discovery announcement; the camera's IP is the packet source.

## Other families in the same library (not analysed further)

- **B**: binds local UDP 44506/52219/52220 and broadcasts `20 36` → :44506, `86 06 01` → :52219, `66 30 01 01` → :46526. Video on 44506 uses a 4-byte header (frame id, last flag, chunk seq, ?).
- **C**: `23 36 00 00 00 00 36 99` → 192.168.1.1:6080 and `66 00 01 00 00 00 01 99` → :6090.
- **Novatek HTTP** (`192.168.1.254/?custom=1&cmd=3014 …`, class `cameraHttpApi`, "Tieniu").
