"""HND NE3 (Beken BK7231U, "TAIXIN MJPEG") wire protocol: commands and frame reassembly.

Reverse-engineered from the HND app (``com.xiaozhen.beauty.hnd`` 1.0.51,
``T0.d`` = ``BekenTxinSdk``). The camera is an access point at ``192.168.1.1``;
it also answers on ``192.168.1.255``. It is a different design from the
Bouffalo "NE3" that shares the SSID prefix ``HNDEC_``: UDP/8800 is closed here.

Three UDP ports on the camera:

========  ====================  ===============================================
port      send                  meaning
========  ====================  ===============================================
46526     ``66 30 01 01``       device info: the reply is a JSON object
44506     ``20 36`` / ``20 37`` start / stop video; JPEG chunks come back from
                                44506 to the same local port
52219     ``86 06 01``          start sensor data; 24-byte packets come back from
                                52219 to the same local port
========  ====================  ===============================================

The camera answers to the *source* port the request came from, and the vendor
app binds 44506 and 52219 locally, so we do the same.

Video packet: 4-byte header, then a slice of a complete JPEG (the first chunk
starts with ``FF D8``)::

    0   u8   frame id, wraps at 256
    1   u8   1 on the last chunk of the frame, else 0
    2   u8   chunk number, starting at 1; on the last chunk it is the chunk count
    3   u8   always 4 so far
    4..      JPEG bytes

The camera re-sends some chunks, so chunks are kept by number. The image is
480x480.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

CAMERA_IP = "192.168.1.1"
INFO_PORT = 46526
VIDEO_PORT = 44506
SENSOR_PORT = 52219

CMD_INFO = bytes([0x66, 0x30, 0x01, 0x01])
CMD_VIDEO_START = bytes([0x20, 0x36])
CMD_VIDEO_STOP = bytes([0x20, 0x37])
CMD_SENSOR_START = bytes([0x86, 0x06, 0x01])

VIDEO_HEADER = 4
SENSOR_PACKET = 24
MAX_CHUNKS = 255


@dataclass(frozen=True)
class BekenFrame:
    jpeg: bytes
    frame_id: int


class Dropped:
    """A frame was replaced by a newer one before all its chunks arrived."""


DROPPED = Dropped()


class FrameAssembler:
    def __init__(self) -> None:
        self._frame_id = -1
        self._done_id = -1
        self._total = 0
        self._chunks: dict[int, bytes] = {}

    def feed(self, packet: bytes) -> BekenFrame | Dropped | None:
        """A BekenFrame when one completes, DROPPED when an unfinished one is
        replaced, None while building and for packets that aren't video."""
        if len(packet) <= VIDEO_HEADER:
            return None
        frame_id, last, number = packet[0], packet[1], packet[2]
        if number == 0:
            return None

        if frame_id == self._done_id:
            return None  # a re-sent chunk of the frame we just finished
        outcome: Dropped | None = None
        if frame_id != self._frame_id:
            if self._chunks:
                outcome = DROPPED
            self._frame_id = frame_id
            self._total = 0
            self._chunks = {}
        self._chunks[number] = packet[VIDEO_HEADER:]
        if last == 1:
            self._total = number
        if not self._total or len(self._chunks) < self._total:
            return outcome
        if any(i not in self._chunks for i in range(1, self._total + 1)):
            return outcome

        data = b"".join(self._chunks[i] for i in range(1, self._total + 1))
        self._frame_id = -1
        self._done_id = frame_id
        self._chunks = {}
        if not data.startswith(b"\xff\xd8"):
            return DROPPED
        return BekenFrame(data, frame_id)


def parse_sensor(packet: bytes) -> tuple[int, int, int] | None:
    """The accelerometer axes ``(x, y, z)`` of a sensor packet: signed 8 bit at
    offsets 0, 2 and 4, 64 counts per g. The bytes between them are noise.
    ``x`` runs along the lens axis, ``y`` and ``z`` lie in the picture plane."""
    if len(packet) < 6:
        return None
    return tuple(struct.unpack_from("b", packet, i)[0] for i in (0, 2, 4))


def parse_angle(packet: bytes) -> int | None:
    """The roll angle the camera computes itself, degrees in [-360, 0): signed
    16 bit big endian at offset 18."""
    if len(packet) < 20:
        return None
    return struct.unpack_from(">h", packet, 18)[0]


def rotation_degrees(angle: int) -> float:
    """Image rotation in degrees from the camera's own roll angle, which is
    passed straight through. Checked on a real camera: with an extra half turn
    the picture stands on its head."""
    return angle % 360.0
