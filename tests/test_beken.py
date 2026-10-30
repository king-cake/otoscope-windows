import json
import math
import threading
import time

import pytest

from otoscope_win import beken as b
from otoscope_win.beken_session import BekenSession, jpeg_size, parse_info

from .fake_beken import INFO, JPEG, FakeBekenCamera, chunks_of


def feed_all(asm, packets):
    out = None
    for p in packets:
        out = asm.feed(p)
    return out


def test_assembles_frame_from_chunks():
    frame = feed_all(b.FrameAssembler(), chunks_of(7, JPEG))
    assert isinstance(frame, b.BekenFrame)
    assert frame.jpeg == JPEG and frame.frame_id == 7


def test_chunks_arrive_out_of_order_and_duplicated():
    packets = list(chunks_of(3, JPEG))
    asm = b.FrameAssembler()
    assert asm.feed(packets[1]) is None
    assert asm.feed(packets[1]) is None
    assert asm.feed(packets[0]).jpeg == JPEG
    # a late duplicate of the finished frame is ignored, not counted as a lost frame
    assert asm.feed(packets[1]) is None
    assert asm.feed(next(iter(chunks_of(4, JPEG)))) is None


def test_incomplete_frame_is_dropped_when_next_starts():
    asm = b.FrameAssembler()
    first = list(chunks_of(1, JPEG))
    assert feed_all(asm, first[:-1]) is None
    assert asm.feed(next(iter(chunks_of(2, JPEG)))) is b.DROPPED


def test_missing_chunk_never_completes():
    packets = list(chunks_of(5, JPEG))
    assert feed_all(b.FrameAssembler(), packets[:1] + packets[2:]) is None


def test_non_jpeg_payload_is_dropped():
    packets = list(chunks_of(9, b"not a jpeg at all, just bytes"))
    assert feed_all(b.FrameAssembler(), packets) is b.DROPPED


def test_ignores_runts_and_chunk_zero():
    asm = b.FrameAssembler()
    assert asm.feed(b"") is None
    assert asm.feed(bytes([1, 0, 1, 4])) is None
    assert asm.feed(bytes([1, 0, 0, 4]) + b"data") is None


def test_sensor_axes_are_signed_bytes_with_noise_between():
    # A real packet from a still camera: x y z at offsets 0, 2, 4; angle -87 at 18.
    packet = bytes.fromhex("ff0b fa07 c209 0000 0000 0000 0000 0000 0000 ffa9 0000 0000")
    assert len(packet) == 24
    assert b.parse_sensor(packet) == (-1, -6, -62)
    assert b.parse_angle(packet) == -87
    assert b.parse_sensor(b"\x00\x01") is None and b.parse_angle(bytes(19)) is None
    # about 1 g at 64 counts per g
    assert math.sqrt(sum(v * v for v in (-1, -6, -62))) == pytest.approx(64, rel=0.05)


def test_rotation_is_the_cameras_own_angle():
    assert b.rotation_degrees(-89) == 271
    assert b.rotation_degrees(-95) == 265
    assert b.rotation_degrees(-6) == 354
    assert b.rotation_degrees(-360) == 0


def test_parse_info_reads_the_camera_json():
    info = parse_info(json.dumps(INFO, indent="\t").encode() + bytes(20))
    assert info.product == "HND-NE3" and info.firmware == "1.0.0"
    assert info.battery_percent is None
    assert parse_info(b"garbage") is None


def test_jpeg_size_reads_sof():
    assert jpeg_size(JPEG) == (480, 480)
    assert jpeg_size(b"\xff\xd8\xff\xd9") == (480, 480)


class Recorder:
    def __init__(self) -> None:
        self.frames = []
        self.devinfos = []
        self.got_frames = threading.Event()

    def on_frame(self, frame, rotation):
        self.frames.append((frame, rotation))
        if len(self.frames) >= 5:
            self.got_frames.set()

    def on_devinfo(self, info):
        self.devinfos.append(info)

    def on_led(self, on):  # pragma: no cover - never called for this camera
        raise AssertionError("no light on this camera")


@pytest.fixture
def camera():
    cam = FakeBekenCamera().start()
    yield cam
    cam.stop()


def make_session(rec, cam):
    return BekenSession(
        rec, "127.0.0.1", cam.port("info"), cam.port("video"), cam.port("sensor"), 0, 0)


def test_session_streams_frames_with_rotation(camera):
    rec = Recorder()
    session = make_session(rec, camera)
    session.start()
    try:
        assert rec.got_frames.wait(5), "no frames arrived"
        frame, rotation = rec.frames[-1]
        assert frame.jpeg == JPEG and (frame.width, frame.height) == (480, 480)
        assert rotation == b.rotation_degrees(-95) == 265
        assert rec.devinfos[0].product == "HND-NE3"
        assert session.devinfo.firmware == "1.0.0"
        assert not session.supports_led
        session.set_led(True)  # a no-op, must not raise
    finally:
        session.close()


def test_close_sends_stop_and_ends_threads(camera):
    rec = Recorder()
    session = make_session(rec, camera)
    session.start()
    assert rec.got_frames.wait(5)
    started = time.monotonic()
    session.close()
    assert time.monotonic() - started < 3
    assert not any(t.is_alive() for t in session._threads)
    deadline = time.monotonic() + 2
    while camera.video_stops == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert camera.video_stops == 1


def test_video_start_is_resent_when_stream_stalls(camera):
    rec = Recorder()
    session = make_session(rec, camera)
    session.start()
    try:
        assert rec.got_frames.wait(5)
        camera._video_client = None  # the camera forgets us
        deadline = time.monotonic() + 5
        starts = camera.video_starts
        while camera.video_starts == starts and time.monotonic() < deadline:
            time.sleep(0.05)
        assert camera.video_starts > starts
    finally:
        session.close()
