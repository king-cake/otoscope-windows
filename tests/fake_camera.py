"""A minimal Soulear camera on localhost for integration tests."""

from __future__ import annotations

import select
import socket
import struct
import threading
import time

from otoscope_win import protocol as p

JPEG = b"\xff\xd8" + bytes(range(256)) * 12 + b"\xff\xd9"
# y=+35, z=-127 -> roll 164.6 degrees (a real Find T sample)
GSENSOR = 0x00908E7F


def devinfo_payload(battery: int = 80) -> bytes:
    b = bytearray(0x80)
    b[0x01:0x04] = b"YPC"
    b[0x21:0x21 + 17] = b"BK7231U-XRH-FBPRO"
    b[0x41:0x47] = b"HKV41B"
    b[0x51:0x5E] = b"Soulear-test1"
    b[0x77:0x79] = (battery << 9).to_bytes(2, "little")
    return bytes(b)


class FakeCamera:
    def __init__(self, cmd_port: int = 0, video_cmd_port: int = 0, jpeg: bytes = JPEG, stream: bool = True) -> None:
        self.jpeg = jpeg
        # stream=False behaves like a firewall eating the video: control only.
        self.stream = stream
        self.cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.cmd.bind(("127.0.0.1", cmd_port))
        self.vcmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.vcmd.bind(("127.0.0.1", video_cmd_port))
        self.led_writes: list[bytes] = []
        self.open_video_count = 0
        self._video_dest: tuple[str, int] | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def cmd_port(self) -> int:
        return self.cmd.getsockname()[1]

    @property
    def video_cmd_port(self) -> int:
        return self.vcmd.getsockname()[1]

    def start(self) -> "FakeCamera":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.cmd.close()
        self.vcmd.close()

    def _reply(self, sock, addr, req: p.Reply, payload: bytes = b"") -> None:
        sock.sendto(struct.pack("<IHHBBH", p.MAGIC, req.seq, req.cmd, 1, 0, len(payload)) + payload, addr)

    def _run(self) -> None:
        video = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        seq = frame_id = 0
        next_frame = 0.0
        while not self._stop.is_set():
            ready, _, _ = select.select([self.cmd, self.vcmd], [], [], 0.01)
            for s in ready:
                try:
                    data, addr = s.recvfrom(4096)
                except OSError:
                    continue
                req = p.parse_reply(data)
                if req is None:
                    continue
                if req.cmd == p.CMD_DEVINFO:
                    self._reply(s, addr, req, devinfo_payload())
                elif req.cmd == p.CMD_LICGET:
                    self._reply(s, addr, req, bytes(0xB0))
                elif req.cmd == p.CMD_LED:
                    self.led_writes.append(req.payload)
                    self._reply(s, addr, req, req.payload)
                elif req.cmd == p.CMD_OPEN_VIDEO:
                    self.open_video_count += 1
                    port = struct.unpack_from("<H", req.payload, 0)[0]
                    self._video_dest = ("127.0.0.1", port)
                    self._reply(s, addr, req)
            if self.stream and self._video_dest and time.monotonic() >= next_frame:
                next_frame = time.monotonic() + 0.05
                chunks = [self.jpeg[i:i + 1000] for i in range(0, len(self.jpeg), 1000)]
                for i, chunk in enumerate(chunks):
                    header = struct.pack(
                        "<BBBBBBIHhh", 1, seq & 0xFF, frame_id & 0xFF,
                        1 if i == len(chunks) - 1 else 0, len(chunks), 1, GSENSOR, 0xB06E, 640, 480,
                    )
                    video.sendto(header + chunk, self._video_dest)
                    seq += 1
                frame_id += 1
        video.close()
