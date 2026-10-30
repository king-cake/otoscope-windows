"""Which session talks to which camera, decided by the access point's name."""

from __future__ import annotations

from .beken_session import BekenSession
from .session import CameraSession


def create_session(listener, ssid: str):
    """HND NE3 cameras advertise ``HNDEC_*``; everything else is a Soulear."""
    if ssid.lower().startswith("hndec_"):
        return BekenSession(listener)
    return CameraSession(listener)
