import threading
import time

import pytest

from otoscope_win.session import CameraSession

from .fake_camera import JPEG, FakeCamera


class Recorder:
    def __init__(self) -> None:
        self.frames = []
        self.devinfos = []
        self.leds = []
        self.got_frames = threading.Event()

    def on_frame(self, frame, rotation):
        self.frames.append((frame, rotation))
        if len(self.frames) >= 5:
            self.got_frames.set()

    def on_devinfo(self, info):
        self.devinfos.append(info)

    def on_led(self, on):
        self.leds.append(on)


@pytest.fixture
def camera():
    cam = FakeCamera().start()
    yield cam
    cam.stop()


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_session_streams_frames_with_rotation(camera):
    rec = Recorder()
    session = CameraSession(rec, "127.0.0.1", None, camera.cmd_port, camera.video_cmd_port)
    session.start()
    try:
        assert rec.got_frames.wait(5), "no frames arrived"
        frame, rotation = rec.frames[-1]
        assert frame.jpeg == JPEG
        assert (frame.width, frame.height) == (640, 480)
        # 164.6 from the accelerometer + 180 for FBPRO products
        assert rotation == pytest.approx(344.6, abs=0.1)
        assert rec.devinfos[0].product == "BK7231U-XRH-FBPRO"
        assert rec.devinfos[0].battery_percent == 80
        # Light switched on at connect, like the vendor app.
        assert camera.led_writes[0] == bytes([0x11, 1, 100])
        assert rec.leds[0] is True
    finally:
        session.close()


def test_led_command_reaches_camera(camera):
    rec = Recorder()
    session = CameraSession(rec, "127.0.0.1", None, camera.cmd_port, camera.video_cmd_port)
    session.start()
    try:
        assert wait_for(lambda: camera.led_writes)
        session.set_led(False)
        assert wait_for(lambda: bytes([0x11, 0, 0]) in camera.led_writes)
        assert wait_for(lambda: rec.leds and rec.leds[-1] is False)
    finally:
        session.close()


def test_close_stops_threads_quickly(camera):
    session = CameraSession(Recorder(), "127.0.0.1", None, camera.cmd_port, camera.video_cmd_port)
    session.start()
    time.sleep(0.3)
    started = time.monotonic()
    session.close()
    assert time.monotonic() - started < 3
    assert not any(t.is_alive() for t in session._threads)
