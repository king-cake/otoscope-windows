"""Launcher for PyInstaller (it runs its entry script outside the package)."""

import sys

from otoscope_win.app import main

if __name__ == "__main__":
    sys.exit(main())
