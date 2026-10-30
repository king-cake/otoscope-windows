"""Live connection to one HND NE3 camera (Beken BK7231U, ``HNDEC_*`` access point).

Same public surface as :class:`session.CameraSession`, so the UI treats both
alike. Three UDP sockets, each with its own thread, see :mod:`beken` for the
protocol:

- **Video**: bound to 44506, JPEG chunks arrive from the camera's port 44506.
- **Sensor**: bound to 52219, 24-byte accelerometer packets become the rotation.
- **Control**: reads the device-info JSON, then starts sensor and video. There
  is no heartbeat; like the vendor app we send the start command again
  whenever the stream stalls.

The camera has no light control and no battery readout that we know of, so
:attr:`supports_led` is false and the battery stays unknown.
"""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time

from . import beken as b
from . import protocol as p
from .assembler import Frame

log = logging.getLogger(__name__)

STALL_S = 1.0
INFO_TIMEOUT_S = 0.5
INFO_REFRESH_S = 30.0
POLL_INTERVAL_S = 0.25
DEFAULT_SIZE = (480, 480)


class BekenSession:
    supports_led = False

    def __init__(
        self,
        listener,
        camera_ip: str = b.CAMERA_IP,
        info_port: int = b.INFO_PORT,
        video_port: int = b.VIDEO_PORT,
        sensor_port: int = b.SENSOR_PORT,
        local_video_port: int = b.VIDEO_PORT,
        local_sensor_port: int = b.SENSOR_PORT,
    ) -> None:
        self._listener = listener
        self.camera_ip = camera_ip
        self._info_port = info_port
        self._video_port = video_port
        self._sensor_port = sensor_port

        self._stop = threading.Event()
        self._rotation = 0.0
        self.packets = 0
        self.frames = 0
        self.dropped = 0
        self.last_packet_at = 0.0
        self.last_sensor_at = 0.0
        self.devinfo: p.DevInfo | None = None

        self._video = _udp_socket(local_video_port)
        self._sensor = _udp_socket(local_sensor_port)
        self._info = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._info.bind(("0.0.0.0", 0))
        self._info.settimeout(INFO_TIMEOUT_S)
        self._threads = [
            threading.Thread(target=self._run_video, name="otoscope-video", daemon=True),
            threading.Thread(target=self._run_sensor, name="otoscope-sensor", daemon=True),
            threading.Thread(target=self._run_control, name="otoscope-control", daemon=True),
        ]

    def start(self) -> None:
        log.info("start: camera=%s", self.camera_ip)
        for t in self._threads:
            t.start()

    def close(self) -> None:
        self._stop.set()
        if self.devinfo is not None or self.last_packet_at:
            self._send(self._video, b.CMD_VIDEO_STOP, self._video_port)
        for s in (self._video, self._sensor, self._info):
            try:
                s.close()
            except OSError:
                pass
        for t in self._threads:
            if t.is_alive() and t is not threading.current_thread():
                t.join(timeout=2.0)

    def set_led(self, on: bool) -> None:
        """No light control on this camera."""

    def seconds_since_last_packet(self) -> float | None:
        return None if self.last_packet_at == 0 else time.monotonic() - self.last_packet_at

    # -- control ---------------------------------------------------------------

    def _send(self, sock: socket.socket, data: bytes, port: int) -> None:
        try:
            sock.sendto(data, (self.camera_ip, port))
        except OSError as e:
            # Network not up yet, or the socket was closed under us.
            if not self._stop.is_set():
                log.debug("send to %s:%d: %s", self.camera_ip, port, e)

    def _run_control(self) -> None:
        try:
            # Right after joining its Wi-Fi, DHCP may not have given us an
            # address yet: keep asking until the camera answers.
            while not self._stop.is_set() and not self._query_info():
                self._stop.wait(0.5)
            if self._stop.is_set():
                return
            last_info = time.monotonic()
            last_video = last_sensor = 0.0
            while not self._stop.is_set():
                now = time.monotonic()
                if now - self.last_packet_at > STALL_S and now - last_video > STALL_S:
                    self._send(self._video, b.CMD_VIDEO_START, self._video_port)
                    log.info("video start sent")
                    last_video = now
                if now - self.last_sensor_at > STALL_S and now - last_sensor > STALL_S:
                    self._send(self._sensor, b.CMD_SENSOR_START, self._sensor_port)
                    last_sensor = now
                if now - last_info >= INFO_REFRESH_S:
                    self._query_info()
                    last_info = now
                self._stop.wait(POLL_INTERVAL_S)
        except Exception:  # noqa: BLE001 - keep the app alive, log for diagnosis
            if not self._stop.is_set():
                log.exception("control loop crashed")

    def _query_info(self) -> bool:
        self._send(self._info, b.CMD_INFO, self._info_port)
        try:
            data, _addr = self._info.recvfrom(4096)
        except socket.timeout:
            return False
        except OSError:
            return False
        info = parse_info(data)
        if info is None:
            return False
        if self.devinfo is None:
            log.info("device info from %s: %s", self.camera_ip, info)
        self.devinfo = info
        self._listener.on_devinfo(info)
        return True

    # -- sensor ----------------------------------------------------------------

    def _run_sensor(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._sensor.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            axes, angle = b.parse_sensor(data), b.parse_angle(data)
            if axes is None or angle is None:
                continue
            self.last_sensor_at = time.monotonic()
            _x, y, z = axes
            # Lens pointing straight up or down: the in-plane axes are near zero
            # and the roll angle is noise, keep the last one.
            if abs(y) > FLAT_THRESHOLD or abs(z) > FLAT_THRESHOLD:
                self._rotation = b.rotation_degrees(angle)

    # -- video -----------------------------------------------------------------

    def _run_video(self) -> None:
        assembler = b.FrameAssembler()
        first = True
        while not self._stop.is_set():
            try:
                data = self._video.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            self.packets += 1
            self.last_packet_at = time.monotonic()
            if first:
                first = False
                log.info("first video packet %d bytes: %s", len(data), data[:16].hex(" "))
            outcome = assembler.feed(data)
            if outcome is b.DROPPED:
                self.dropped += 1
            elif isinstance(outcome, b.BekenFrame):
                width, height = jpeg_size(outcome.jpeg)
                self.frames += 1
                try:
                    self._listener.on_frame(Frame(outcome.jpeg, None, width, height), self._rotation)
                except Exception:  # noqa: BLE001
                    log.exception("frame listener failed")


# Accelerometer counts below which the roll angle is not meaningful
# (the sensor reads about 64 per g).
FLAT_THRESHOLD = 8


def _udp_socket(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    s.bind(("0.0.0.0", port))
    s.settimeout(1.0)
    return s


def parse_info(data: bytes) -> p.DevInfo | None:
    """The camera's device-info JSON as a DevInfo. The battery is unknown."""
    try:
        text = data.split(b"\x00", 1)[0].decode("utf-8")
        info = json.loads(text)
        return p.DevInfo(
            vendor=str(info.get("manufacturer") or info.get("designed_by") or ""),
            product=str(info.get("brand") or info.get("model") or ""),
            firmware=str(info.get("firmware", "")),
            ssid="",
            battery_percent=None,
        )
    except (UnicodeDecodeError, ValueError, AttributeError):
        return None


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the SOF marker, or the camera's usual 480x480."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack_from(">HH", data, i + 5)
            return width, height
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        i += 2 + struct.unpack_from(">H", data, i + 2)[0]
    return DEFAULT_SIZE
