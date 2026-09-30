"""Streaming page: the round image plus the control panel."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .widgets import CameraView


class CameraPage(QWidget):
    photo_requested = Signal()
    record_toggled = Signal()
    light_toggled = Signal(bool)
    auto_rotate_toggled = Signal(bool)
    mirror_toggled = Signal(bool, bool)  # horizontal, vertical
    turn_requested = Signal(int)  # -90 or +90
    caption_changed = Signal(str)
    fullscreen_requested = Signal()
    disconnect_requested = Signal()
    open_latest = Signal()
    open_pictures = Signal()
    open_videos = Signal()
    firewall_fix_requested = Signal()
    full_view_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = CameraView()
        self.view.double_clicked.connect(self.fullscreen_requested)

        # Header: model, battery, disconnect
        self._model = QLabel("Otoskop")
        self._model.setObjectName("model")
        self._battery = QLabel("")
        disconnect = QPushButton("Trennen")
        disconnect.clicked.connect(self.disconnect_requested)
        header = QHBoxLayout()
        header.addWidget(self._model)
        header.addStretch(1)
        header.addWidget(self._battery)
        header.addSpacing(16)
        header.addWidget(disconnect)

        # Capture
        self._photo = QPushButton("Foto  (Leertaste)")
        self._photo.setObjectName("primary")
        self._photo.clicked.connect(self.photo_requested)
        self._record = QPushButton("Video starten  (R)")
        self._record.setCheckable(True)
        self._record.clicked.connect(lambda _: self.record_toggled.emit())

        # Camera controls
        self._light = QPushButton("Licht  (L)")
        self._light.setCheckable(True)
        self._light.setChecked(True)
        self._light.toggled.connect(self.light_toggled)
        self._auto_rotate = QCheckBox("Automatische Drehung")
        self._auto_rotate.toggled.connect(self.auto_rotate_toggled)
        self._mirror_h = QCheckBox("Horizontal spiegeln (links/rechts)")
        self._mirror_v = QCheckBox("Vertikal spiegeln (oben/unten)")
        for box in (self._mirror_h, self._mirror_v):
            box.setToolTip("Nur die Anzeige; Fotos und Videos werden nie gespiegelt.")
            box.toggled.connect(lambda _: self.mirror_toggled.emit(
                self._mirror_h.isChecked(), self._mirror_v.isChecked()))

        turn_left = QPushButton("↺ 90°")
        turn_left.setToolTip("Bild 90° nach links drehen (←)")
        turn_left.clicked.connect(lambda: self.turn_requested.emit(-90))
        turn_right = QPushButton("↻ 90°")
        turn_right.setToolTip("Bild 90° nach rechts drehen (→)")
        turn_right.clicked.connect(lambda: self.turn_requested.emit(90))
        self._turn_label = QLabel()
        self._turn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        turn_row = QHBoxLayout()
        turn_row.addWidget(turn_left)
        turn_row.addWidget(self._turn_label, 1)
        turn_row.addWidget(turn_right)
        self._full_view = QCheckBox("Ganzes Bild, ohne Kreis  (K)")
        self._full_view.setToolTip(
            "Zeigt und speichert das komplette Kamerabild inklusive Ecken, ohne Kreisausschnitt "
            "und ohne automatische Drehung.")
        self._full_view.toggled.connect(self._on_full_view)

        self._caption = QLineEdit()
        self._caption.setPlaceholderText("Beschriftung, z. B. „Linkes Ohr“")
        self._caption.setClearButtonEnabled(True)
        self._caption.textChanged.connect(self.caption_changed)
        self._caption.returnPressed.connect(self._caption.clearFocus)

        fullscreen = QPushButton("Vollbild  (F11)")
        fullscreen.clicked.connect(self.fullscreen_requested)

        # Latest capture and folders
        self._latest = QToolButton()
        self._latest.setIconSize(QSize(96, 96))
        self._latest.setFixedSize(QSize(104, 104))
        self._latest.setToolTip("Letzte Aufnahme öffnen")
        self._latest.setEnabled(False)
        self._latest.clicked.connect(self.open_latest)
        pictures = QPushButton("Fotos-Ordner")
        pictures.clicked.connect(self.open_pictures)
        videos = QPushButton("Video-Ordner")
        videos.clicked.connect(self.open_videos)
        folders = QVBoxLayout()
        folders.addWidget(pictures)
        folders.addWidget(videos)
        folders.addStretch(1)
        latest_row = QHBoxLayout()
        latest_row.addWidget(self._latest)
        latest_row.addLayout(folders)

        self._diagnostics = QLabel("")
        self._diagnostics.setObjectName("diagnostics")
        self._diagnostics.setWordWrap(True)

        panel = QVBoxLayout()
        panel.setSpacing(10)
        panel.addWidget(_section("Aufnahme"))
        panel.addWidget(self._photo)
        panel.addWidget(self._record)
        panel.addWidget(self._caption)
        panel.addWidget(_section("Kamera"))
        panel.addWidget(self._light)
        panel.addWidget(self._full_view)
        panel.addWidget(self._auto_rotate)
        panel.addLayout(turn_row)
        panel.addWidget(self._mirror_h)
        panel.addWidget(self._mirror_v)
        panel.addWidget(fullscreen)
        panel.addWidget(_section("Gespeichert"))
        panel.addLayout(latest_row)
        panel.addStretch(1)
        panel.addWidget(self._diagnostics)
        panel_widget = QFrame()
        panel_widget.setObjectName("panel")
        panel_widget.setLayout(panel)
        panel_widget.setFixedWidth(300)

        # Shown when no video arrives: the firewall is the usual culprit.
        self._banner_text = QLabel()
        self._banner_text.setWordWrap(True)
        self._banner_button = QPushButton("Firewall-Freigabe einrichten")
        self._banner_button.setObjectName("primary")
        self._banner_button.clicked.connect(self.firewall_fix_requested)
        banner_layout = QHBoxLayout()
        banner_layout.addWidget(self._banner_text, 1)
        banner_layout.addWidget(self._banner_button)
        self._banner = QFrame()
        self._banner.setObjectName("banner")
        self._banner.setLayout(banner_layout)
        self._banner.hide()

        body = QHBoxLayout()
        body.addWidget(self.view, 1)
        body.addWidget(panel_widget)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.addLayout(header)
        layout.addWidget(self._banner)
        layout.addLayout(body, 1)

    # -- state from the window -----------------------------------------------

    def set_model(self, text: str) -> None:
        self._model.setText(text)

    def set_battery(self, percent: int | None) -> None:
        self._battery.setText("" if percent is None else f"Akku {percent} %")

    def set_light(self, on: bool) -> None:
        self._light.blockSignals(True)
        self._light.setChecked(on)
        self._light.setText("Licht an  (L)" if on else "Licht aus  (L)")
        self._light.blockSignals(False)

    def toggle_light(self) -> None:
        self._light.toggle()

    def set_toggles(
        self,
        auto_rotate: bool,
        mirror_h: bool,
        mirror_v: bool,
        caption: str,
        full_view: bool = False,
        turn: int = 0,
    ) -> None:
        for box, value in (
            (self._auto_rotate, auto_rotate),
            (self._mirror_h, mirror_h),
            (self._mirror_v, mirror_v),
            (self._full_view, full_view),
        ):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self._caption.blockSignals(True)
        self._caption.setText(caption)
        self._caption.blockSignals(False)
        self._auto_rotate.setEnabled(not full_view)
        self.set_turn(turn)
        self.view.set_mirror(mirror_h, mirror_v)
        self.view.set_caption(caption)
        self.view.set_full(full_view)

    def set_turn(self, degrees: int) -> None:
        self._turn_label.setText(f"Drehung {degrees}°")

    def toggle_full_view(self) -> None:
        self._full_view.toggle()

    def _on_full_view(self, on: bool) -> None:
        # Rotation only applies to the circle, so its switch greys out here.
        self._auto_rotate.setEnabled(not on)
        self.full_view_toggled.emit(on)

    def set_recording(self, recording: bool, elapsed_s: float = 0.0) -> None:
        self._record.setChecked(recording)
        if recording:
            m, s = divmod(int(elapsed_s), 60)
            self._record.setText(f"● Aufnahme {m:02d}:{s:02d} – stoppen  (R)")
            self._record.setObjectName("recording")
        else:
            self._record.setText("Video starten  (R)")
            self._record.setObjectName("")
        self._record.style().unpolish(self._record)
        self._record.style().polish(self._record)

    def set_latest(self, path: Path | None, thumbnail: QImage | None) -> None:
        self._latest.setEnabled(path is not None)
        if thumbnail is not None and not thumbnail.isNull():
            pix = QPixmap.fromImage(thumbnail).scaled(
                96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._latest.setIcon(QIcon(pix))
        self._latest.setToolTip(f"Öffnen: {path.name}" if path else "Letzte Aufnahme öffnen")

    def show_firewall_banner(self, text: str, button_enabled: bool = True) -> None:
        self._banner_text.setText(text)
        self._banner_button.setEnabled(button_enabled)
        self._banner.show()

    def hide_firewall_banner(self) -> None:
        self._banner.hide()

    def firewall_banner_visible(self) -> bool:
        return self._banner.isVisible()

    def set_diagnostics(self, text: str) -> None:
        self._diagnostics.setText(text)


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("section")
    return label
