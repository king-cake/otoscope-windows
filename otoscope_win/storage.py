"""Where captures go: Pictures\\Otoscope and Videos\\Otoscope, Android naming."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QImage

ALBUM = "Otoscope"
JPEG_QUALITY = 95


def file_name(is_video: bool, at: datetime | None = None) -> str:
    """``otoscope-20260824-193012-045.jpg``. Milliseconds so two shots within
    a second don't collide."""
    at = at or datetime.now()
    stamp = at.strftime("%Y%m%d-%H%M%S-") + f"{at.microsecond // 1000:03d}"
    return f"otoscope-{stamp}{'.mp4' if is_video else '.jpg'}"


def _album(location: QStandardPaths.StandardLocation) -> Path:
    base = QStandardPaths.writableLocation(location) or str(Path.home())
    path = Path(base) / ALBUM
    path.mkdir(parents=True, exist_ok=True)
    return path


def pictures_dir() -> Path:
    return _album(QStandardPaths.StandardLocation.PicturesLocation)


def videos_dir() -> Path:
    return _album(QStandardPaths.StandardLocation.MoviesLocation)


def new_video_path() -> Path:
    return videos_dir() / file_name(is_video=True)


def save_photo(image: QImage, directory: Path | None = None) -> Path:
    path = (directory or pictures_dir()) / file_name(is_video=False)
    if not image.save(str(path), "JPG", JPEG_QUALITY):
        raise OSError(f"Konnte {path} nicht speichern")
    return path
