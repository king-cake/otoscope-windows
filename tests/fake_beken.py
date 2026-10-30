"""A fake HND NE3 camera on localhost: device info, video chunks, sensor packets."""

import json
import socket
import struct
import threading
import time

from otoscope_win import beken as b

INFO = {
    "uuid": "3F0FFFFF0F0FFFFF02FFFC584A5130F2",
    "model": "EAR_CAMERA",
    "brand": "HND-NE3",
    "soc": "BK7231UQFN40",
    "firmware": "1.0.0",
    "manufacturer": "C-Chip Technology Group",
}
# A tiny but structurally real JPEG: SOI, SOF0 (480x480), EOI.
JPEG = (
    bytes.fromhex("ffd8")
    + bytes.fromhex("ffc0001108")
    + struct.pack(">HH", 480, 480)
    + bytes(10)
    + bytes.fromhex("ffd9")
)
CHUNK = 12


def chunks_of(frame_id: int, jpeg: bytes):
    parts = [jpeg[i:i + CHUNK] for i in range(0, len(jpeg), CHUNK)]
    for n, part in enumerate(parts, start=1):
        last = 1 if n == len(parts) else 0
        yield bytes([frame_id, last, n, 4]) + part


class FakeBekenCamera:
    """Answers like the camera: replies go back to whoever sent the request."""

    def __init__(self, accel=(-1, 11, -62), angle=-95) -> None:
        self.accel = accel
        self.angle = angle
        self.info_requests = 0
        self.video_starts = 0
        self.video_stops = 0
        self.sensor_starts = 0
        self._stop = threading.Event()
        self._video_client = None
        self._sensor_client = None
        self._socks = {name: self._bind() for name in ("info", "video", "sensor")}
        self._threads = []

    @staticmethod
    def _bind() -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        s.settimeout(0.05)
        return s

    def port(self, name: str) -> int:
        return self._socks[name].getsockname()[1]

    def start(self) -> "FakeBekenCamera":
        for target in (self._serve_info, self._serve_video, self._serve_sensor, self._stream):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        for s in self._socks.values():
            s.close()

    def _recv(self, name: str):
        try:
            return self._socks[name].recvfrom(256)
        except (socket.timeout, OSError):
            return None, None

    def _serve_info(self) -> None:
        while not self._stop.is_set():
            data, addr = self._recv("info")
            if data == b.CMD_INFO:
                self.info_requests += 1
                self._socks["info"].sendto(json.dumps(INFO, indent="\t").encode(), addr)

    def _serve_video(self) -> None:
        while not self._stop.is_set():
            data, addr = self._recv("video")
            if data == b.CMD_VIDEO_START:
                self.video_starts += 1
                self._video_client = addr
            elif data == b.CMD_VIDEO_STOP:
                self.video_stops += 1
                self._video_client = None

    def _serve_sensor(self) -> None:
        while not self._stop.is_set():
            data, addr = self._recv("sensor")
            if data == b.CMD_SENSOR_START:
                self.sensor_starts += 1
                self._sensor_client = addr

    def _stream(self) -> None:
        frame_id = 0
        while not self._stop.is_set():
            if self._sensor_client:
                x, y, z = self.accel
                packet = (
                    struct.pack("bBbBbB", x, 5, y, 9, z, 3)  # axes with a noise byte after each
                    + bytes(12)
                    + struct.pack(">h", self.angle)
                    + bytes(4)
                )
                self._socks["sensor"].sendto(packet, self._sensor_client)
            if self._video_client:
                for chunk in chunks_of(frame_id, JPEG):
                    self._socks["video"].sendto(chunk, self._video_client)
                frame_id = (frame_id + 1) % 256
            time.sleep(0.02)
