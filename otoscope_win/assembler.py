"""Reassembles i4season video frames from UDP chunks (port of FrameAssembler.kt).

Per-packet wire format (type 1; type 6 adds 12 bytes of 6-axis data before the
payload, which we skip)::

    offset  size  field
      0      u8   packet type: 1 = 16-byte header, 6 = 28-byte header
      1      u8   packet sequence, +1 per datagram across frames
      2      u8   frame id
      3      u8   last-chunk flag
      4      u8   number of chunks in the frame
      5      u8   flags; bit0 = accelerometer sample present
      6-9    u32  packed accelerometer sample, see protocol.roll_degrees
     10-11   u8x2 last two bytes of the camera MAC
     12-13   i16  width
     14-15   i16  height
     16..    ...  JPEG chunk; the last chunk is zero-padded after FFD9

There is no per-chunk index, only the global sequence number. Like the vendor
library, a frame is dropped on a sequence gap or when the number of received
chunks doesn't match the count carried by the last chunk.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_V1 = 16
HEADER_V6 = 28


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    gsensor: int | None
    width: int
    height: int


class Dropped:
    """A frame was lost (sequence gap, missing chunks, not a JPEG)."""


DROPPED = Dropped()


class FrameAssembler:
    def __init__(self) -> None:
        self._frame_id = -1
        self._last_seq = -1
        self._broken = False
        self._chunks: list[bytes] = []

    def feed(self, packet: bytes) -> Frame | Dropped | None:
        """Returns a Frame when one completes, DROPPED when one is lost, and
        None while building or for packets that aren't video."""
        if not packet:
            return None
        header = {1: HEADER_V1, 6: HEADER_V6}.get(packet[0])
        if header is None or len(packet) < header:
            return None

        seq, frame_id, last, count, flags = packet[1], packet[2], packet[3], packet[4], packet[5]

        outcome: Frame | Dropped | None = None
        if frame_id != self._frame_id:
            if self._chunks:
                # A frame still in flight never saw its last chunk.
                outcome = DROPPED
            self._frame_id = frame_id
            self._chunks = []
            self._broken = False
        elif seq != (self._last_seq + 1) & 0xFF:
            self._broken = True
        self._last_seq = seq
        self._chunks.append(bytes(packet[header:]))
        if not last:
            return outcome

        complete = not self._broken and len(self._chunks) == count
        data = b"".join(self._chunks) if complete else b""
        self._frame_id = -1
        self._chunks = []
        if not data.startswith(b"\xff\xd8"):
            return DROPPED
        (g,) = struct.unpack_from("<I", packet, 6)
        width, height = struct.unpack_from("<hh", packet, 12)
        return Frame(
            jpeg=trim_after_eoi(data),
            gsensor=g if flags & 1 else None,
            width=width,
            height=height,
        )


def trim_after_eoi(data: bytes) -> bytes:
    """Cut the zero padding the firmware appends after the JPEG's EOI marker."""
    end = data.rfind(b"\xff\xd9")
    return data[: end + 2] if end >= 2 else data
