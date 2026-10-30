"""Start page: find Soulear access points and pick one."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..wifi import WifiNetwork


class ConnectPage(QWidget):
    scan_requested = Signal()
    connect_requested = Signal(str)
    open_location_settings = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel("Kamera verbinden")
        title.setObjectName("title")
        intro = QLabel(
            "Schalte das Otoskop ein. Die App sucht nach WLAN-Netzen, die mit "
            "„Soulear-“ oder „HNDEC_“ beginnen, und wechselt beim Verbinden in das Kamera-WLAN. "
            "Beim Trennen kehrt der Laptop in dein vorheriges WLAN zurück."
        )
        intro.setWordWrap(True)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self._connect())
        self._list.currentItemChanged.connect(lambda *_: self._update_buttons())

        self._scan = QPushButton("Suchen")
        self._scan.clicked.connect(self.scan_requested)
        self._connect_button = QPushButton("Verbinden")
        self._connect_button.setDefault(True)
        self._connect_button.clicked.connect(self._connect)
        self._location = QPushButton("Standort-Einstellungen öffnen")
        self._location.clicked.connect(self.open_location_settings)
        self._location.hide()

        buttons = QHBoxLayout()
        buttons.addWidget(self._scan)
        buttons.addStretch(1)
        buttons.addWidget(self._location)
        buttons.addWidget(self._connect_button)

        self._status = QLabel("")
        self._status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addWidget(self._list, 1)
        layout.addLayout(buttons)
        layout.addWidget(self._status)
        self._busy = False
        self._update_buttons()

    def set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        self._status.setText(status)
        self._update_buttons()

    def set_status(self, status: str) -> None:
        self._status.setText(status)

    def show_location_hint(self, show: bool) -> None:
        self._location.setVisible(show)

    def set_networks(self, networks: list[WifiNetwork], current_ssid: str | None) -> None:
        self._list.clear()
        for n in networks:
            label = f"{n.ssid}    {n.signal} %"
            if n.ssid == current_ssid:
                label += "    (verbunden)"
            if n.secured:
                label += "    (passwortgeschützt, nicht unterstützt)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, n.ssid)
            if n.secured:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._update_buttons()

    def _connect(self) -> None:
        item = self._list.currentItem()
        if item and not self._busy and item.flags() & Qt.ItemFlag.ItemIsEnabled:
            self.connect_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def _update_buttons(self) -> None:
        item = self._list.currentItem()
        selectable = item is not None and bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
        self._scan.setEnabled(not self._busy)
        self._connect_button.setEnabled(not self._busy and selectable)
        self._list.setEnabled(not self._busy)
