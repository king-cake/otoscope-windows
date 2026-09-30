"""Entry point: logging, theme, main window."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow

STYLE = """
QLabel#title { font-size: 22px; font-weight: 600; }
QLabel#model { font-size: 16px; font-weight: 600; }
QLabel#section { color: #8fa4c8; font-weight: 600; margin-top: 8px; }
QLabel#diagnostics { color: #7d8594; font-size: 11px; }
QFrame#panel { background: #20242c; border-radius: 10px; padding: 8px; }
QFrame#banner { background: #4a3413; border: 1px solid #a8741f; border-radius: 8px; padding: 6px; }
QPushButton { padding: 8px 12px; border-radius: 6px; background: #2a2f38; border: 1px solid #3a404c; }
QPushButton:hover { background: #333a45; }
QPushButton:disabled { color: #5c6370; background: #22262d; }
QPushButton:checked { background: #34507f; border-color: #4a6aa5; }
QPushButton#primary { background: #2f6fed; border-color: #2f6fed; color: white; font-weight: 600; padding: 12px; }
QPushButton#primary:hover { background: #3b7bff; }
QPushButton#recording, QPushButton#recording:checked { background: #c62828; border-color: #c62828;
                                                       color: white; font-weight: 600; }
QListWidget { font-size: 15px; }
QListWidget::item { padding: 10px 6px; }
QLineEdit { padding: 6px; border-radius: 6px; border: 1px solid #3a404c; background: #1d2128; }
QToolButton#fullscreenExit { background: rgba(0,0,0,150); color: white; padding: 8px 14px;
                             border: 1px solid #555; border-radius: 6px; }
"""


def log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OtoscopeWin"
    base.mkdir(parents=True, exist_ok=True)
    return base / "otoscope.log"


def setup_logging() -> None:
    handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def dark_palette() -> QPalette:
    p = QPalette()
    window, base, text = QColor("#16191f"), QColor("#1d2128"), QColor("#e6e9ef")
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, window)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, QColor("#2a2f38"))
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Highlight, QColor("#2f6fed"))
    p.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#7d8594"))
    p.setColor(QPalette.ColorRole.ToolTipBase, base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5c6370"))
    return p


def app_icon() -> QIcon:
    """A lens: blue ring on a dark disc. Also used to generate the .exe icon."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2f6fed"))
        p.drawEllipse(0, 0, size, size)
        p.setBrush(QColor("#16191f"))
        inset = size * 0.16
        p.drawEllipse(int(inset), int(inset), int(size - 2 * inset), int(size - 2 * inset))
        p.setBrush(QColor("#9cc0ff"))
        dot = size * 0.2
        p.drawEllipse(int(size * 0.3), int(size * 0.3), int(dot), int(dot))
        p.end()
        icon.addPixmap(QPixmap.fromImage(image))
    return icon


def main() -> int:
    setup_logging()
    logging.getLogger(__name__).info("starting")
    app = QApplication(sys.argv)
    app.setOrganizationName("Otoscope")
    app.setApplicationName("OtoscopeWin")
    app.setApplicationDisplayName("Otoscope")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
