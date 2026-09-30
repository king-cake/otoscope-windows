"""End-to-end UI flow against the fake camera, offscreen, without real Wi-Fi."""

import time

import av
import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from otoscope_win import storage
from otoscope_win.session import CameraSession
from otoscope_win.ui.main_window import MainWindow
from otoscope_win.wifi import WifiError

from .fake_camera import FakeCamera


def real_jpeg() -> bytes:
    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(QColor("darkred"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", 80)
    return bytes(data)


def no_wifi():
    raise WifiError("test")


def pump(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "pictures_dir", lambda: tmp_path)
    monkeypatch.setattr(storage, "new_video_path", lambda: tmp_path / "clip.mp4")
    cam = FakeCamera(jpeg=real_jpeg()).start()
    win = MainWindow(
        wifi_factory=no_wifi,
        session_factory=lambda bridge: CameraSession(bridge, "127.0.0.1", None, cam.cmd_port, cam.video_cmd_port),
    )
    win._settings.clear()
    win.show()
    yield win, cam, tmp_path
    win.close()
    cam.stop()


def test_full_flow(window):
    win, cam, tmp = window
    win._connect_done("Soulear-test1", None)
    assert win._stack.currentWidget() is win.camera

    # Frames arrive, model and battery show up.
    assert pump(lambda: win._last_image is not None)
    assert pump(lambda: "Akku 80 %" in win.camera._battery.text())
    assert "BK7231U-XRH-FBPRO" in win.camera._model.text()
    assert win.camera.view._rotation == pytest.approx(344.6, abs=0.1)

    # Auto-rotate off: the view shows the image unrotated.
    win.camera._auto_rotate.setChecked(False)
    assert win.camera.view._rotation == 0.0
    win.camera._auto_rotate.setChecked(True)
    assert pump(lambda: win.camera.view._rotation > 300)

    # Photo.
    win._take_photo()
    photos = list(tmp.glob("otoscope-*.jpg"))
    assert len(photos) == 1

    # Video for about a second.
    win._toggle_recording()
    assert pump(lambda: win._recording and win._recording.frames_written >= 5)
    win._toggle_recording()
    with av.open(str(tmp / "clip.mp4")) as c:
        assert c.streams.video[0].codec_context.name == "h264"

    # Light off reaches the camera.
    win.camera.toggle_light()
    assert pump(lambda: bytes([0x11, 0, 0]) in cam.led_writes)

    # Fullscreen opens and closes.
    win._toggle_fullscreen()
    assert win._fullscreen is not None
    assert pump(lambda: win._fullscreen.view._image is not None)
    win._toggle_fullscreen()
    assert win._fullscreen is None

    # Disconnect returns to the start page.
    win._disconnect("Getrennt.")
    assert win._stack.currentWidget() is win.connect_page
    assert win._session is None
