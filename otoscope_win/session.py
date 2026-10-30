"""Live connection to one Soulear camera (port of I4seasonSession.kt).

Two UDP sockets, each driven by its own thread:

- **Control**: request/response datagrams (see :mod:`protocol`). Reads device
  info (model, firmware, battery), then sends "open video" with the video
  socket's local port. There is no separate heartbeat: like the vendor app we
  re-send "open video" whenever no video packet arrived for a second, and poll
  device info every 10 s for the battery level. LED commands are queued and
  sent from this thread so requests never interleave.
- **Video**: JPEG chunks pushed by the camera to the announced port,
  reassembled by :class:`FrameAssembler`. Each frame carries an accelerometer
  sample that becomes the rotation angle.

The session is UI-agnostic: it reports through a :class:`SessionListener`
whose methods are called from the worker threads.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from typing import Protocol

from . import protocol as p
from .assembler import DROPPED, Frame, FrameAssembler

log = logging.getLogger(__name__)

VIDEO_STALL_S = 1.0
DEVINFO_INTERVAL_S = 10.0
POLL_INTERVAL_S = 0.25
REPLY_TIMEOUT_S = 0.1


class SessionListener(Protocol):
    def on_frame(self, frame: Frame, rotation: float) -> None: ...
    def on_devinfo(self, info: p.DevInfo) -> None: ...
    def on_led(self, on: bool) -> None: ...


class CameraSession:
    supports_led = True

    def __init__(
        self,
        listener: SessionListener,
        camera_ip: str = "192.168.1.1",
        broadcast_ip: str | None = "192.168.1.255",
        cmd_port: int = p.CMD_PORT,
        video_cmd_port: int = p.VIDEO_CMD_PORT,
    ) -> None:
        self._listener = listener
        self.camera_ip = camera_ip
        self._broadcast_ip = broadcast_ip
        self._cmd_port = cmd_port
        self._video_cmd_port = video_cmd_port

        self._stop = threading.Event()
        self._led_queue: queue.Queue[bool] = queue.Queue()
        self._seq = 0
        # Identifies this client to the camera ("in use by another phone").
        self._client_id = int(time.time())
        self._mount_offset = 0.0
        self._rotation = 0.0

        self.packets = 0
        self.frames = 0
        self.dropped = 0
        self.last_packet_at = 0.0
        self.devinfo: p.DevInfo | None = None

        self._video = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._video.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self._video.bind(("0.0.0.0", 0))
        self._video.settimeout(1.0)
        self._control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._control.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._control.settimeout(REPLY_TIMEOUT_S)
        self._threads = [
            threading.Thread(target=self._run_video, name="otoscope-video", daemon=True),
            threading.Thread(target=self._run_control, name="otoscope-control", daemon=True),
        ]

    @property
    def video_port(self) -> int:
        return self._video.getsockname()[1]

    def start(self) -> None:
        log.info("start: camera=%s video_port=%d", self.camera_ip, self.video_port)
        for t in self._threads:
            t.start()

    def close(self) -> None:
        self._stop.set()
        for s in (self._video, self._control):
            try:
                s.close()
            except OSError:
                pass
        for t in self._threads:
            if t.is_alive() and t is not threading.current_thread():
                t.join(timeout=2.0)

    def set_led(self, on: bool) -> None:
        self._led_queue.put(on)

    def seconds_since_last_packet(self) -> float | None:
        return None if self.last_packet_at == 0 else time.monotonic() - self.last_packet_at

    # -- control ---------------------------------------------------------------

    def _run_control(self) -> None:
        try:
            # Keep asking until the camera answers: right after joining its
            # Wi-Fi, DHCP may not have given us an address yet.
            while not self._stop.is_set() and not self._query_devinfo(discover=True):
                self._stop.wait(0.5)
            if self._stop.is_set():
                return
            # The vendor app always reads the licence before opening video.
            self._request(p.CMD_LICGET, port=self._cmd_port, attempts=3)
            # Like the vendor app, switch the light on at connect time.
            self._send_led(True)

            open_video = p.open_video_payload(self.video_port, self._client_id)
            last_open = 0.0
            last_devinfo = time.monotonic()
            while not self._stop.is_set():
                now = time.monotonic()
                if now - self.last_packet_at > VIDEO_STALL_S and now - last_open > VIDEO_STALL_S:
                    reply = self._request(p.CMD_OPEN_VIDEO, open_video, self._video_cmd_port)
                    log.info("open video -> %s", "no reply" if reply is None else f"status={reply.status}")
                    last_open = now
                if now - last_devinfo >= DEVINFO_INTERVAL_S:
                    self._query_devinfo()
                    last_devinfo = now
                try:
                    while True:
                        self._send_led(self._led_queue.get_nowait())
                except queue.Empty:
                    pass
                self._stop.wait(POLL_INTERVAL_S)
        except Exception:  # noqa: BLE001 - keep the app alive, log for diagnosis
            if not self._stop.is_set():
                log.exception("control loop crashed")

    def _query_devinfo(self, discover: bool = False) -> bool:
        reply = self._request(p.CMD_DEVINFO, port=self._cmd_port)
        if reply is None and discover and self._broadcast_ip:
            # Fall back to the broadcast address and adopt whoever answers.
            reply = self._request(p.CMD_DEVINFO, port=self._cmd_port, target=self._broadcast_ip)
        info = p.parse_devinfo(reply.payload) if reply else None
        if info is None:
            return False
        if self.devinfo is None:
            log.info("devinfo from %s: %s", self.camera_ip, info)
        self.devinfo = info
        self._mount_offset = p.mount_offset_degrees(info.product)
        self._listener.on_devinfo(info)
        return True

    def _send_led(self, on: bool) -> None:
        reply = self._request(p.CMD_LED, p.led_write_payload(on), self._cmd_port)
        state = p.parse_led(reply.payload) if reply else None
        log.info("led on=%s -> %s", on, state)
        self._listener.on_led(state.on if state else on)

    def _request(
        self,
        cmd: int,
        payload: bytes = b"",
        port: int = p.CMD_PORT,
        attempts: int = 10,
        target: str | None = None,
    ) -> p.Reply | None:
        """Send one command and wait for the reply with the matching sequence
        number. Only called from the control thread."""
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        packet = p.request(seq, cmd, payload)
        dest = (target or self.camera_ip, port)
        for _ in range(attempts):
            if self._stop.is_set():
                return None
            try:
                self._control.sendto(packet, dest)
                while True:
                    data, addr = self._control.recvfrom(4096)
                    reply = p.parse_reply(data)
                    # Late replies to earlier retries carry an older seq.
                    if reply and reply.seq == seq and reply.cmd == cmd:
                        if target is not None and addr[0] != self.camera_ip:
                            log.info("camera answered from %s", addr[0])
                            self.camera_ip = addr[0]
                        return reply
            except socket.timeout:
                continue
            except OSError as e:
                # Network not up yet, or Windows reporting ICMP port-unreachable
                # as a reset on UDP sockets.
                if self._stop.is_set():
                    return None
                log.debug("cmd 0x%04x: %s", cmd, e)
                self._stop.wait(REPLY_TIMEOUT_S)
        return None

    # -- video -----------------------------------------------------------------

    def _run_video(self) -> None:
        assembler = FrameAssembler()
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
                log.info("first video packet %d bytes: %s", len(data), data[:32].hex(" "))
            outcome = assembler.feed(data)
            if outcome is DROPPED:
                self.dropped += 1
            elif isinstance(outcome, Frame):
                if outcome.gsensor is not None:
                    roll = p.roll_degrees(outcome.gsensor)
                    # Keep the previous angle when roll is undefined.
                    if roll is not None:
                        self._rotation = (roll + self._mount_offset) % 360.0
                self.frames += 1
                try:
                    self._listener.on_frame(outcome, self._rotation)
                except Exception:  # noqa: BLE001
                    log.exception("frame listener failed")
