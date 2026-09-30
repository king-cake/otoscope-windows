"""Main window: ties Wi-Fi switching, the camera session, captures and the UI together."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QImage, QKeyEvent, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from .. import firewall, storage
from ..compose import compose
from ..protocol import DevInfo
from ..recorder import Recording
from ..session import CameraSession
from ..wifi import LocationAccessDenied, Wifi, WifiError, WifiSwitcher, soulear_networks
from .camera_page import CameraPage
from .connect_page import ConnectPage
from .widgets import Background, CameraView, SessionBridge

log = logging.getLogger(__name__)

NO_ANSWER_S = 15.0       # camera never answered device info
NO_VIDEO_S = 6.0         # answered, but no video packets (firewall?)
LOST_S = 10.0            # video stopped mid-session
AUTO_SCAN_MS = 10_000


class FullscreenWindow(QWidget):
    closed = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("Otoscope – Vollbild")
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.view = CameraView()
        self.view.setPalette(palette)
        self.view.double_clicked.connect(self.close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self._exit = QToolButton(self)
        self._exit.setText("✕  Vollbild beenden (Esc)")
        self._exit.setObjectName("fullscreenExit")
        self._exit.clicked.connect(self.close)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._exit.adjustSize()
        self._exit.move(self.width() - self._exit.width() - 24, 24)
        super().resizeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self.closed.emit()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, wifi_factory=Wifi, session_factory=CameraSession) -> None:
        """The factories are seams for tests: no real Wi-Fi, a fake camera."""
        super().__init__()
        self._session_factory = session_factory
        self.setWindowTitle("Otoscope")
        self.resize(1180, 760)

        self._settings = QSettings()  # organisation/app name set in app.main()
        self._auto_rotate = self._settings.value("autoRotate", True, type=bool)
        self._mirror_h = self._settings.value("mirror", True, type=bool)
        self._mirror_v = self._settings.value("mirrorVertical", False, type=bool)
        # Manual quarter turns on top of (or, in whole-image mode, instead of)
        # the accelerometer angle: 0, 90, 180 or 270.
        self._turn = self._settings.value("turn", 0, type=int) % 360
        self._caption = self._settings.value("caption", "", type=str)
        self._full_view = self._settings.value("fullView", False, type=bool)

        self._bg = Background()
        self._wifi: Wifi | None = None
        self._switcher: WifiSwitcher | None = None
        self._wifi_error: str | None = None
        try:
            self._wifi = wifi_factory()
            self._switcher = WifiSwitcher(self._wifi)
        except WifiError as e:
            self._wifi_error = str(e)

        self._bridge = SessionBridge()
        self._bridge.frame_ready.connect(self._on_frame)
        self._bridge.devinfo_changed.connect(self._on_devinfo)
        self._bridge.led_changed.connect(lambda on: self.camera.set_light(on))
        self._session: CameraSession | None = None
        self._connected_at = 0.0
        self._ssid = ""
        self._recording: Recording | None = None
        self._last_image: QImage | None = None
        self._last_rotation = 0.0
        self._latest_path: Path | None = None
        self._fullscreen: FullscreenWindow | None = None
        self._fps_frames = 0
        self._fps_at = time.monotonic()
        self._fps = 0.0

        self.connect_page = ConnectPage()
        self.connect_page.scan_requested.connect(self._scan)
        self.connect_page.connect_requested.connect(self._connect)
        self.connect_page.open_location_settings.connect(
            lambda: QDesktopServices.openUrl(QUrl("ms-settings:privacy-location")))

        self.camera = CameraPage()
        self.camera.set_toggles(self._auto_rotate, self._mirror_h, self._mirror_v, self._caption,
                                self._full_view, self._turn)
        self.camera.turn_requested.connect(self._turn_by)
        self.camera.full_view_toggled.connect(self._set_full_view)
        self.camera.photo_requested.connect(self._take_photo)
        self.camera.record_toggled.connect(self._toggle_recording)
        self.camera.light_toggled.connect(self._set_light)
        self.camera.auto_rotate_toggled.connect(self._set_auto_rotate)
        self.camera.mirror_toggled.connect(self._set_mirror)
        self.camera.caption_changed.connect(self._set_caption)
        self.camera.fullscreen_requested.connect(self._toggle_fullscreen)
        self.camera.disconnect_requested.connect(lambda: self._disconnect("Getrennt."))
        self.camera.open_latest.connect(lambda: self._open(self._latest_path))
        self.camera.open_pictures.connect(lambda: self._open(storage.pictures_dir()))
        self.camera.open_videos.connect(lambda: self._open(storage.videos_dir()))
        self.camera.firewall_fix_requested.connect(self._fix_firewall)
        self._firewall_busy = False

        self._stack = QStackedWidget()
        self._stack.addWidget(self.connect_page)
        self._stack.addWidget(self.camera)
        self.setCentralWidget(self._stack)

        # Application-wide, so they also work in the fullscreen window. A focused
        # text field still gets its own keys first (Qt's shortcut override).
        for key, action in (
            ("Space", self._take_photo),
            ("R", self._toggle_recording),
            ("L", self.camera.toggle_light),
            ("K", self.camera.toggle_full_view),
            ("Left", lambda: self._turn_by(-90)),
            ("Right", lambda: self._turn_by(90)),
            ("F11", self._toggle_fullscreen),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda a=action: self._if_streaming(a))

        self._health = QTimer(self)
        self._health.timeout.connect(self._check_health)
        self._health.start(1000)
        self._auto_scan = QTimer(self)
        self._auto_scan.timeout.connect(self._maybe_auto_scan)
        self._auto_scan.start(AUTO_SCAN_MS)
        self._scanning = False

        if self._wifi_error:
            self.connect_page.set_status(f"WLAN nicht verfügbar: {self._wifi_error}")
        else:
            QTimer.singleShot(0, self._scan)

    # -- Wi-Fi -------------------------------------------------------------------

    def _scan(self) -> None:
        if not self._wifi or self._scanning:
            return
        self._scanning = True
        self.connect_page.set_status("Suche nach Kameras …")
        wifi = self._wifi
        self._bg.run(lambda: (wifi.scan(3.0), wifi.current()), self._scan_done)

    def _scan_done(self, result, error) -> None:
        self._scanning = False
        if isinstance(error, LocationAccessDenied):
            self.connect_page.show_location_hint(True)
            self.connect_page.set_status(
                "Windows gibt die WLAN-Liste nicht frei. Erlaube unter "
                "Einstellungen › Datenschutz › Standort den Zugriff für Desktop-Apps.")
            return
        if error:
            self.connect_page.set_status(f"Suche fehlgeschlagen: {error}")
            return
        self.connect_page.show_location_hint(False)
        networks, current = result
        cameras = soulear_networks(networks)
        log.info("scan: %d networks, cameras: %s, current: %s", len(networks), [c.ssid for c in cameras], current)
        self.connect_page.set_networks(cameras, current.ssid if current else None)
        self.connect_page.set_status(
            f"{len(cameras)} Kamera(s) gefunden." if cameras
            else "Keine Kamera gefunden. Ist das Otoskop eingeschaltet?")

    def _maybe_auto_scan(self) -> None:
        if self._stack.currentWidget() is self.connect_page and self._session is None:
            self._scan()

    def _connect(self, ssid: str) -> None:
        if not self._switcher:
            return
        self.connect_page.set_busy(True, f"Verbinde mit {ssid} …")
        switcher = self._switcher
        self._bg.run(lambda: switcher.switch_to(ssid), lambda _r, e: self._connect_done(ssid, e))

    def _connect_done(self, ssid: str, error: Exception | None) -> None:
        if error:
            self.connect_page.set_busy(False, f"Verbindung fehlgeschlagen: {error}")
            return
        self.connect_page.set_busy(False)
        self._ssid = ssid
        self._last_image = None
        self.camera.set_model(ssid)
        self.camera.set_battery(None)
        self.camera.set_light(True)
        self.camera.set_diagnostics("")
        self.camera.view.clear("Warte auf Kamera …")
        self.camera.hide_firewall_banner()
        self._session = self._session_factory(self._bridge)
        self._session.start()
        self._connected_at = time.monotonic()
        self._stack.setCurrentWidget(self.camera)

    def _disconnect(self, message: str) -> None:
        self._stop_recording()
        if self._fullscreen:
            self._fullscreen.close()
        if self._session:
            self._session.close()
            self._session = None
        self._stack.setCurrentWidget(self.connect_page)
        if self._switcher and self._switcher.camera_ssid:
            self.connect_page.set_busy(True, f"{message} Stelle vorheriges WLAN wieder her …")
            switcher = self._switcher
            self._bg.run(switcher.restore, lambda _r, _e: self._restored(message))
        else:
            self._restored(message)

    def _restored(self, message: str) -> None:
        self.connect_page.set_busy(False, message)
        self._scan()

    # -- session events -------------------------------------------------------------

    def _on_frame(self, image: QImage, rotation: float) -> None:
        if self._last_image is None and self.camera.firewall_banner_visible() and not self._firewall_busy:
            self.camera.hide_firewall_banner()
        self._last_image = image
        self._last_rotation = rotation
        self._fps_frames += 1
        angle = self._effective_rotation()
        self.camera.view.set_frame(image, angle)
        if self._fullscreen:
            self._fullscreen.view.set_frame(image, angle)
        if self._recording:
            self._recording.submit(image, angle, self._caption)

    def _on_devinfo(self, info: DevInfo) -> None:
        self.camera.set_model(f"{info.product or info.ssid}  ·  {self._ssid}")
        self.camera.set_battery(info.battery_percent)
        if self._last_image is None:
            self.camera.view.clear("Warte auf Bild …")

    def _effective_rotation(self) -> float:
        # The accelerometer only drives the circle; the manual quarter turns
        # apply in both views.
        auto = self._last_rotation if self._auto_rotate and not self._full_view else 0.0
        return (auto + self._turn) % 360.0

    def _check_health(self) -> None:
        now = time.monotonic()
        if now - self._fps_at >= 1.0:
            self._fps = self._fps_frames / (now - self._fps_at)
            self._fps_frames = 0
            self._fps_at = now
        session = self._session
        if session is None:
            return
        if self._recording:
            self.camera.set_recording(True, self._recording.elapsed_s)
        since = session.seconds_since_last_packet()
        elapsed = now - self._connected_at
        info = session.devinfo
        parts = [f"{self._fps:.0f} fps", f"{session.frames} Bilder", f"{session.dropped} verworfen"]
        if info:
            parts.append(f"Firmware {info.firmware}")
        self.camera.set_diagnostics("  ·  ".join(parts))
        if info is None and elapsed > NO_ANSWER_S:
            self.camera.view.clear("Die Kamera antwortet nicht.\nIst sie eingeschaltet und in Reichweite?")
        elif info is not None and since is None and elapsed > NO_VIDEO_S:
            self.camera.view.clear("Keine Videodaten – die Windows-Firewall blockiert das Kamerabild.")
            if not self.camera.firewall_banner_visible():
                self.camera.show_firewall_banner(
                    "Die Kamera antwortet, aber Windows lässt ihr Videobild nicht durch. "
                    "Die Freigabe gilt nur für diese App und nur für das lokale Kamera-Netz; "
                    "sie braucht einmal eine Administrator-Bestätigung.")
        elif since is not None and since > LOST_S:
            log.warning("camera lost (%.0f s without video)", since)
            self._disconnect("Verbindung zur Kamera verloren.")

    # -- controls ---------------------------------------------------------------------

    def _fix_firewall(self) -> None:
        if self._firewall_busy:
            return
        self._firewall_busy = True
        self.camera.show_firewall_banner("Bitte die Windows-Abfrage bestätigen …", button_enabled=False)
        self._bg.run(firewall.add_rule_elevated, self._firewall_done)

    def _firewall_done(self, _result, error: Exception | None) -> None:
        self._firewall_busy = False
        if error:
            self.camera.show_firewall_banner(f"{error} Du kannst es erneut versuchen.")
            return
        self.camera.show_firewall_banner(
            "Freigabe eingerichtet. Das Bild sollte in wenigen Sekunden erscheinen.", button_enabled=False)

    def _if_streaming(self, action) -> None:
        if self._session is not None:
            action()

    def _take_photo(self) -> None:
        if self._last_image is None:
            self.statusBar().showMessage("Noch kein Bild.", 3000)
            return
        try:
            still = compose(self._last_image, self._effective_rotation(), self._caption, self._full_view)
            path = storage.save_photo(still)
        except OSError as e:
            self.statusBar().showMessage(f"Foto konnte nicht gespeichert werden: {e}", 6000)
            return
        self._latest_path = path
        self.camera.set_latest(path, still)
        self.statusBar().showMessage(f"Foto gespeichert: {path}", 5000)

    def _toggle_recording(self) -> None:
        if self._recording:
            self._stop_recording()
        else:
            self._recording = Recording(storage.new_video_path(), full=self._full_view)
            self.camera.set_recording(True, 0)
            self.statusBar().showMessage("Videoaufnahme läuft …")

    def _stop_recording(self) -> None:
        recording, self._recording = self._recording, None
        if recording is None:
            return
        self.camera.set_recording(False)
        try:
            path = recording.stop()
        except OSError as e:
            self.statusBar().showMessage(f"Video konnte nicht gespeichert werden: {e}", 6000)
            return
        self._latest_path = path
        thumb = compose(self._last_image, self._effective_rotation(), self._caption, recording.full) \
            if self._last_image is not None else None
        self.camera.set_latest(path, thumb)
        self.statusBar().showMessage(f"Video gespeichert: {path}", 5000)

    def _set_light(self, on: bool) -> None:
        self.camera.set_light(on)
        if self._session:
            self._session.set_led(on)

    def _set_auto_rotate(self, on: bool) -> None:
        self._auto_rotate = on
        self._settings.setValue("autoRotate", on)
        if self._last_image is not None:
            self._on_frame(self._last_image, self._last_rotation)

    def _set_mirror(self, horizontal: bool, vertical: bool) -> None:
        self._mirror_h, self._mirror_v = horizontal, vertical
        self._settings.setValue("mirror", horizontal)
        self._settings.setValue("mirrorVertical", vertical)
        self.camera.view.set_mirror(horizontal, vertical)
        if self._fullscreen:
            self._fullscreen.view.set_mirror(horizontal, vertical)

    def _turn_by(self, degrees: int) -> None:
        self._turn = (self._turn + degrees) % 360
        self._settings.setValue("turn", self._turn)
        self.camera.set_turn(self._turn)
        if self._last_image is not None:
            self._on_frame(self._last_image, self._last_rotation)

    def _set_full_view(self, on: bool) -> None:
        self._full_view = on
        self._settings.setValue("fullView", on)
        self.camera.view.set_full(on)
        if self._fullscreen:
            self._fullscreen.view.set_full(on)
        if self._last_image is not None:
            self._on_frame(self._last_image, self._last_rotation)

    def _set_caption(self, text: str) -> None:
        self._caption = text
        self._settings.setValue("caption", text)
        self.camera.view.set_caption(text)
        if self._fullscreen:
            self._fullscreen.view.set_caption(text)

    def _toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self._fullscreen.close()
            return
        window = FullscreenWindow()
        window.view.set_mirror(self._mirror_h, self._mirror_v)
        window.view.set_caption(self._caption)
        window.view.set_full(self._full_view)
        if self._last_image is not None:
            window.view.set_frame(self._last_image, self._effective_rotation())
        window.closed.connect(self._fullscreen_closed)
        self._fullscreen = window
        window.showFullScreen()

    def _fullscreen_closed(self) -> None:
        self._fullscreen = None
        self.activateWindow()

    def _open(self, path: Path | None) -> None:
        if path is not None:
            os.startfile(str(path))  # noqa: S606 - opening our own capture/folder

    # -- shutdown ---------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._health.stop()
        self._auto_scan.stop()
        self._stop_recording()
        if self._fullscreen:
            self._fullscreen.close()
        if self._session:
            self._session.close()
            self._session = None
        self._bg.close()
        if self._switcher and self._switcher.camera_ssid:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self.statusBar().showMessage("Stelle vorheriges WLAN wieder her …")
            QApplication.processEvents()
            self._bg.run_blocking(self._switcher.restore, timeout_s=20)
            QApplication.restoreOverrideCursor()
        if self._wifi:
            # On the worker, behind any scan still using the handle.
            self._bg.run_blocking(self._wifi.close, timeout_s=5)
        super().closeEvent(event)
