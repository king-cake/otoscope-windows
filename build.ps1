# Builds dist\Otoscope.exe: a single, self-contained executable.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Creates .venv on first run, runs the tests, then packages with PyInstaller.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    py -3.13 -m venv .venv
    .\.venv\Scripts\python -m pip install --upgrade pip
}
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

.\.venv\Scripts\python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "tests failed" }

# The .exe icon is drawn by the app itself, so it can't drift from the window icon.
New-Item -ItemType Directory -Force assets | Out-Null
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python -c "from PySide6.QtWidgets import QApplication; a = QApplication([]); from otoscope_win.app import app_icon; app_icon().pixmap(256, 256).save('assets/otoscope.ico')"
Remove-Item Env:\QT_QPA_PLATFORM

.\.venv\Scripts\python -m PyInstaller `
    --noconfirm --clean --onefile --windowed `
    --name Otoscope `
    --icon assets\otoscope.ico `
    --exclude-module tkinter `
    --exclude-module PySide6.QtNetwork `
    --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtPdf `
    otoscope.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Get-Item dist\Otoscope.exe | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
