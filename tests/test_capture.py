import time
from datetime import datetime

import av
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from otoscope_win import storage
from otoscope_win.compose import compose_capture, crop_side, video_side
from otoscope_win.recorder import Recording


def test_sides():
    assert crop_side(640, 480) == 480
    assert video_side(640, 480) == 480
    assert video_side(650, 490) == 480
    assert video_side(10, 10) == 16


def half_red_image(w=640, h=480) -> QImage:
    """Left half red, right half blue: shows rotation and mirroring."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("blue"))
    p = QPainter(img)
    p.fillRect(0, 0, w // 2, h, QColor("red"))
    p.end()
    return img


def colour_at(img: QImage, x: int, y: int) -> str:
    c = img.pixelColor(x, y)
    if c.red() > 200 and c.blue() < 60:
        return "red"
    if c.blue() > 200 and c.red() < 60:
        return "blue"
    if max(c.red(), c.green(), c.blue()) < 30:
        return "black"
    return f"other{c.getRgb()}"


def test_capture_is_square_circle_on_black(qapp):
    out = compose_capture(half_red_image(), rotation=0, caption="")
    assert (out.width(), out.height()) == (480, 480)
    assert colour_at(out, 2, 2) == "black"            # corner outside the circle
    assert colour_at(out, 100, 240) == "red"          # left inside the circle
    assert colour_at(out, 380, 240) == "blue"


def test_capture_rotates_clockwise_and_never_mirrors(qapp):
    out = compose_capture(half_red_image(), rotation=90, caption="")
    # Clockwise 90 degrees: the left (red) half ends up on top.
    assert colour_at(out, 240, 100) == "red"
    assert colour_at(out, 240, 380) == "blue"


def test_caption_is_burnt_in_bottom_left(qapp):
    plain = compose_capture(half_red_image(), 0, "")
    captioned = compose_capture(half_red_image(), 0, "Linkes Ohr")
    region = [(x, y) for x in range(20, 200) for y in range(440, 460)]
    assert any(plain.pixelColor(x, y) != captioned.pixelColor(x, y) for x, y in region)


def test_file_name_matches_android():
    at = datetime(2026, 8, 24, 19, 30, 12, 45000)
    assert storage.file_name(False, at) == "otoscope-20260824-193012-045.jpg"
    assert storage.file_name(True, at) == "otoscope-20260824-193012-045.mp4"


def test_save_photo(qapp, tmp_path):
    path = storage.save_photo(compose_capture(half_red_image(), 0, ""), tmp_path)
    assert path.exists() and path.suffix == ".jpg"
    assert QImage(str(path)).width() == 480


def test_recording_writes_playable_h264(qapp, tmp_path):
    rec = Recording(tmp_path / "clip.mp4")
    for i in range(12):
        rec.submit(half_red_image(), rotation=i * 10.0, caption="Test")
        time.sleep(0.05)
    path = rec.stop()
    assert rec.frames_written >= 5
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.name == "h264"
        assert (stream.width, stream.height) == (480, 480)
        frames = sum(1 for _ in container.decode(stream))
    assert frames == rec.frames_written


def test_recording_without_frames_reports_error(qapp, tmp_path):
    rec = Recording(tmp_path / "empty.mp4")
    with pytest.raises(OSError):
        rec.stop()
