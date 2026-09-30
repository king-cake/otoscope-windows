"""Control-channel codec for i4season (Soulear) otoscopes.

Reverse-engineered from the Soulear app's ``libWifiCamera.so``; a port of
``I4seasonProtocol.kt`` from the Android app.

Every control datagram (both directions) starts with a 12-byte little-endian
header::

    offset  size  field
      0      u32  magic 0xFFEEFFEE
      4      u16  sequence number; the reply echoes it
      6      u16  command
      8      u8   0x01 in requests
      9      u8   status in replies, 0 = OK
     10      u16  payload length
     12..    ...  payload

Commands used here:

- ``0x0001`` device info, to UDP/10005. The reply payload is 0x80 bytes, see
  :func:`parse_devinfo`.
- ``0x0002`` licence info, to UDP/10005. Only the vendor app checks it; the
  camera streams regardless. Sent once to mirror the vendor's sequence.
- ``0x0004`` open video, to UDP/10006. Payload ``u16 pic_port, u16 audio_port,
  u32 client_id``. Re-sent whenever the stream goes quiet (the keepalive).
- ``0x000A`` LED, to UDP/10005. Payload ``u8 op, u8 status, u8 brightness``;
  ``op`` is the LED id (1) with ``0x10`` set for "write". The camera echoes any
  brightness but the Find T's LED doesn't dim, so we only send 100 or 0.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

MAGIC = 0xFFEEFFEE
HEADER_SIZE = 12

CMD_PORT = 10005
VIDEO_CMD_PORT = 10006

CMD_DEVINFO = 0x0001
CMD_LICGET = 0x0002
CMD_OPEN_VIDEO = 0x0004
CMD_LED = 0x000A

_LED_CAMERA = 1
_LED_WRITE = 0x10
_DEVINFO_SIZE = 0x80

_HEADER = struct.Struct("<IHHBBH")


def request(seq: int, cmd: int, payload: bytes = b"") -> bytes:
    return _HEADER.pack(MAGIC, seq & 0xFFFF, cmd, 1, 0, len(payload)) + payload


def open_video_payload(pic_port: int, client_id: int) -> bytes:
    return struct.pack("<HHI", pic_port, 0, client_id & 0xFFFFFFFF)


def led_read_payload() -> bytes:
    return bytes([_LED_CAMERA, 0, 0])


def led_write_payload(on: bool) -> bytes:
    return bytes([_LED_WRITE | _LED_CAMERA, 1 if on else 0, 100 if on else 0])


@dataclass(frozen=True)
class Reply:
    seq: int
    cmd: int
    status: int
    payload: bytes


def parse_reply(data: bytes) -> Reply | None:
    """None for anything that isn't a well-formed control datagram."""
    if len(data) < HEADER_SIZE:
        return None
    magic, seq, cmd, _direction, status, _length = _HEADER.unpack_from(data)
    if magic != MAGIC:
        return None
    return Reply(seq=seq, cmd=cmd, status=status, payload=bytes(data[HEADER_SIZE:]))


@dataclass(frozen=True)
class DevInfo:
    vendor: str
    product: str
    firmware: str
    ssid: str
    battery_percent: int


def parse_devinfo(payload: bytes) -> DevInfo | None:
    """Device-info reply payload::

        0x01  char[32] vendor        ("YPC" on the Find T)
        0x21  char[32] product       ("BK7231U-XRH-FBPRO")
        0x41  char[16] firmware      ("HKV41B")
        0x51  char[32] ssid
        0x71  u8[6]    mac
        0x77  u16      status; bits 9..15 = battery percent
    """
    if len(payload) < _DEVINFO_SIZE:
        return None
    (status,) = struct.unpack_from("<H", payload, 0x77)
    return DevInfo(
        vendor=_cstring(payload, 0x01, 32),
        product=_cstring(payload, 0x21, 32),
        firmware=_cstring(payload, 0x41, 16),
        ssid=_cstring(payload, 0x51, 32),
        battery_percent=max(0, min(100, status >> 9)),
    )


@dataclass(frozen=True)
class LedState:
    on: bool
    brightness: int


def parse_led(payload: bytes) -> LedState | None:
    if len(payload) < 3:
        return None
    return LedState(on=payload[1] != 0, brightness=payload[2])


def mount_offset_degrees(product: str) -> float:
    """The vendor app adds a fixed 180 degrees for these products; their sensor
    is mounted upside down relative to the lens."""
    return 180.0 if ("FBPRO" in product or "R1" in product) else 0.0


def roll_degrees(g: int) -> float | None:
    """Roll angle in [0, 360) from the packed accelerometer sample in video
    header bytes 6..9. Each axis is a 9-bit magnitude plus a separate sign bit::

        x = bits 20..28, sign bit 29   (probe long axis)
        y = bits 10..18, sign bit 19
        z = bits  0..8,  sign bit  9

    The vendor computes atan(y / z) and fixes the quadrant from the sign bits,
    which is atan2(y, z). None when y = z = 0 (probe vertical, roll undefined).
    """
    y = _axis(g, 10, 19)
    z = _axis(g, 0, 9)
    if y == 0 and z == 0:
        return None
    return math.degrees(math.atan2(y, z)) % 360.0


def _axis(g: int, shift: int, sign_bit: int) -> int:
    magnitude = (g >> shift) & 0x1FF
    return -magnitude if (g >> sign_bit) & 1 else magnitude


def _cstring(buf: bytes, offset: int, size: int) -> str:
    raw = buf[offset:offset + size].split(b"\0", 1)[0]
    return raw.decode("latin-1").strip()
