"""Windows Firewall allow rule for the camera's video stream.

Control replies get through on their own (they answer our own requests), but
the camera pushes video to a port we only announced, and Windows silently drops
that unsolicited UDP. It doesn't prompt for UDP either. So the app offers to add
one narrow inbound rule: this program only, UDP only, local subnet only. Adding
it needs admin rights, hence a single UAC prompt through an elevated
PowerShell.
"""

from __future__ import annotations

import base64
import ctypes
import logging
import subprocess
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

RULE_NAME = "Otoscope (Kamera-Video)"

_CREATE_NO_WINDOW = 0x08000000
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_ERROR_CANCELLED = 1223
_WAIT_TIMEOUT_MS = 60_000


def program_path() -> str:
    """The executable the rule must name: Otoscope.exe when packaged (for a
    PyInstaller one-file build that's sys.executable), python.exe from source."""
    return sys.executable


def rule_script(program: str) -> str:
    """PowerShell that replaces our rule. Single quotes are doubled for PS."""
    quoted_program = program.replace("'", "''")
    name = RULE_NAME.replace("'", "''")
    return (
        f"Remove-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue; "
        f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow "
        f"-Program '{quoted_program}' -Protocol UDP -RemoteAddress LocalSubnet -Profile Any "
        "-Description 'Videobild des Soulear-Otoskops empfangen' | Out-Null"
    )


def encoded_command(script: str) -> str:
    """-EncodedCommand takes base64 of UTF-16LE, which sidesteps all quoting."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def rule_exists(program: str | None = None) -> bool:
    """True when our rule is present for this program. Reading rules needs no
    admin rights."""
    program = program or program_path()
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE_NAME}", "verbose"],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("netsh failed: %s", e)
        return False
    if result.returncode != 0:
        return False
    output = result.stdout.decode("mbcs", errors="ignore").lower()
    return program.lower() in output


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


class FirewallError(Exception):
    pass


def add_rule_elevated() -> None:
    """Shows the UAC prompt, runs the rule script elevated and waits for it.
    Raises FirewallError when the user declines or the rule isn't there after."""
    program = program_path()
    params = (
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden "
        f"-EncodedCommand {encoded_command(rule_script(program))}"
    )
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = "powershell.exe"
    info.lpParameters = params
    info.nShow = _SW_HIDE
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == _ERROR_CANCELLED:
            raise FirewallError("Die Freigabe wurde abgebrochen.")
        raise FirewallError(f"Konnte die Freigabe nicht starten (Fehler {err}).")
    try:
        kernel32.WaitForSingleObject(info.hProcess, _WAIT_TIMEOUT_MS)
    finally:
        kernel32.CloseHandle(info.hProcess)
    if not rule_exists(program):
        raise FirewallError("Die Firewall-Regel wurde nicht angelegt.")
    log.info("firewall rule added for %s", program)
