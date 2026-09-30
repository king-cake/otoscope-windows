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
    mirror_toggled = Signal(bool)
    caption_changed = Signal(str)
    fullscreen_requested = Signal()
    disconnect_requested = Signal()
    open_latest = Signal()
    open_pictures = Signal()
    open_videos = Signal()

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
        self._mirror = QCheckBox("Spiegeln (Selbstuntersuchung)")
        self._mirror.toggled.connect(self.mirror_toggled)

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
        panel.addWidget(self._auto_rotate)
        panel.addWidget(self._mirror)
        panel.addWidget(fullscreen)
        panel.addWidget(_section("Gespeichert"))
        panel.addLayout(latest_row)
        panel.addStretch(1)
        panel.addWidget(self._diagnostics)
        panel_widget = QFrame()
        panel_widget.setObjectName("panel")
        panel_widget.setLayout(panel)
        panel_widget.setFixedWidth(300)

        body = QHBoxLayout()
        body.addWidget(self.view, 1)
        body.addWidget(panel_widget)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.addLayout(header)
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

    def set_toggles(self, auto_rotate: bool, mirror: bool, caption: str) -> None:
        for box, value in ((self._auto_rotate, auto_rotate), (self._mirror, mirror)):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self._caption.blockSignals(True)
        self._caption.setText(caption)
        self._caption.blockSignals(False)
        self.view.set_mirror(mirror)
        self.view.set_caption(caption)

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

    def set_diagnostics(self, text: str) -> None:
        self._diagnostics.setText(text)


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("section")
    return label
