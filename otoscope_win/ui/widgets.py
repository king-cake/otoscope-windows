"""Reusable widgets and the Qt bridge for the camera session."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..assembler import Frame
from ..compose import draw_camera, draw_caption, draw_full, fit_rect
from ..protocol import DevInfo

log = logging.getLogger(__name__)


class SessionBridge(QObject):
    """Receives session callbacks on worker threads and re-emits them as Qt
    signals, which Qt delivers on the GUI thread. JPEG decoding happens here,
    off the GUI thread."""

    frame_ready = Signal(QImage, float)
    devinfo_changed = Signal(object)
    led_changed = Signal(bool)

    def on_frame(self, frame: Frame, rotation: float) -> None:
        image = QImage.fromData(frame.jpeg, "JPG")
        if not image.isNull():
            self.frame_ready.emit(image, rotation)

    def on_devinfo(self, info: DevInfo) -> None:
        self.devinfo_changed.emit(info)

    def on_led(self, on: bool) -> None:
        self.led_changed.emit(on)


class Background(QObject):
    """Runs blocking work (Wi-Fi scans and switching) on one worker thread, in
    order, and hands the result back on the GUI thread. The thread is a daemon
    so a scan still waiting on the radio never holds up quitting the app."""

    _finished = Signal(object, object, object)  # callback, result, error

    def __init__(self) -> None:
        super().__init__()
        self._tasks: queue.Queue[Callable[[], None]] = queue.Queue()
        self._closed = False
        self._finished.connect(self._deliver)
        threading.Thread(target=self._work, name="otoscope-bg", daemon=True).start()

    def run(self, fn: Callable[[], object], done: Callable[[object, Exception | None], None]) -> None:
        def task() -> None:
            try:
                result, error = fn(), None
            except Exception as e:  # noqa: BLE001
                log.exception("background task failed")
                result, error = None, e
            self._finished.emit(done, result, error)

        self._tasks.put(task)

    def run_blocking(self, fn: Callable[[], object], timeout_s: float) -> None:
        """For shutdown: queue behind any running task and wait, never forever."""
        done = threading.Event()

        def task() -> None:
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("shutdown task failed")
            finally:
                done.set()

        self._tasks.put(task)
        done.wait(timeout_s)

    def close(self) -> None:
        """Results that arrive after this are dropped (the window is gone)."""
        self._closed = True

    def _work(self) -> None:
        while True:
            self._tasks.get()()

    def _deliver(self, done, result, error) -> None:
        if not self._closed:
            done(result, error)


class CameraView(QWidget):
    """The camera image: round (default) or the whole frame. Mirroring applies
    here only, never to captures."""

    double_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._rotation = 0.0
        self._mirror = True
        self._caption = ""
        self._full = False
        self._placeholder = "Warte auf Bild …"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 240)

    def set_frame(self, image: QImage, rotation: float) -> None:
        self._image = image
        self._rotation = rotation
        self.update()

    def clear(self, placeholder: str = "Warte auf Bild …") -> None:
        self._image = None
        self._placeholder = placeholder
        self.update()

    def set_mirror(self, on: bool) -> None:
        self._mirror = on
        self.update()

    def set_caption(self, text: str) -> None:
        self._caption = text
        self.update()

    def set_full(self, on: bool) -> None:
        """Whole frame, fitted and unrotated, instead of the rotated circle."""
        self._full = on
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        background = self.palette().window().color()
        painter.fillRect(self.rect(), background)
        if self._image is None:
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
        elif self._full:
            rect = fit_rect(self._image, QRectF(self.rect()))
            draw_full(painter, self._image, rect, self._mirror)
            draw_caption(painter, rect, self._caption)
        else:
            side = min(self.width(), self.height())
            rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
            draw_camera(painter, self._image, rect, self._rotation, self._mirror, background)
            draw_caption(painter, rect, self._caption)
        painter.end()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self.double_clicked.emit()
