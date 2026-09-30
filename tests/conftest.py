import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    # Keep test settings away from the real app's registry key.
    app.setOrganizationName("OtoscopeTest")
    app.setApplicationName("OtoscopeWinTest")
    yield app
