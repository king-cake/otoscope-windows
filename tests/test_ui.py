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
        session_factory=lambda bridge, ssid: CameraSession(bridge, "127.0.0.1", None, cam.cmd_port, cam.video_cmd_port),
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

    # Whole-image mode: no rotation, auto-rotate greyed out, 4:3 photo.
    win.camera.toggle_full_view()
    assert win.camera.view._full and win.camera.view._rotation == 0.0
    assert not win.camera._auto_rotate.isEnabled()
    for old in photos:
        old.unlink()
    win._take_photo()
    (full_photo,) = tmp.glob("otoscope-*.jpg")
    assert QImage(str(full_photo)).size().toTuple() == (640, 480)
    win.camera.toggle_full_view()
    assert win.camera._auto_rotate.isEnabled()
    assert pump(lambda: win.camera.view._rotation > 300)

    # Quarter turns add to the auto angle and wrap; the label follows.
    win._turn_by(90)
    assert pump(lambda: win.camera.view._rotation == pytest.approx((344.6 + 90) % 360, abs=0.1))
    win._turn_by(-90)
    win._turn_by(-90)
    assert win._turn == 270
    assert "270" in win.camera._turn_label.text()
    win._turn_by(90)

    # Both mirror axes reach the view.
    win.camera._mirror_v.setChecked(True)
    assert (win.camera.view._mirror_h, win.camera.view._mirror_v) == (win._mirror_h, True)
    win.camera._mirror_v.setChecked(False)

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


def test_firewall_banner_when_video_is_blocked(qapp, monkeypatch):
    import otoscope_win.ui.main_window as mw

    monkeypatch.setattr(mw, "NO_VIDEO_S", 0.5)
    cam = FakeCamera(stream=False).start()
    win = MainWindow(
        wifi_factory=no_wifi,
        session_factory=lambda bridge, ssid: CameraSession(bridge, "127.0.0.1", None, cam.cmd_port, cam.video_cmd_port),
    )
    win.show()
    try:
        win._connect_done("Soulear-test1", None)
        assert pump(lambda: win.camera.firewall_banner_visible(), timeout=4)
        assert win._session is not None  # still connected, waiting for the fix
    finally:
        win.close()
        cam.stop()
