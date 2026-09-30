"""H.264 MP4 recording with PyAV (libx264 ships in the PyAV wheel).

The camera's frame rate drifts (roughly 12 to 20 fps), so every frame is
stamped with its real capture time in milliseconds instead of assuming a fixed
rate. Composing and encoding run on a worker thread fed through a one-slot
queue: if the encoder falls behind, recorded frames are skipped and the live
view is never held up.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from fractions import Fraction
from pathlib import Path

import av
from PySide6.QtGui import QImage

from .compose import compose, video_size

log = logging.getLogger(__name__)

_TIME_BASE = Fraction(1, 1000)


class VideoWriter:
    """RGBA frames in, MP4 out. Not thread-safe; one writer thread."""

    def __init__(self, path: Path, width: int, height: int) -> None:
        self.path = path
        self.width = width
        self.height = height
        self._container = av.open(str(path), mode="w")
        self._stream = self._container.add_stream("libx264", rate=30)
        self._stream.width = width
        self._stream.height = height
        self._stream.pix_fmt = "yuv420p"
        self._stream.time_base = _TIME_BASE
        self._stream.codec_context.time_base = _TIME_BASE
        self._stream.options = {"crf": "20", "preset": "veryfast"}
        self._last_pts = -1

    def write(self, rgba: QImage, t_ms: int) -> None:
        if t_ms <= self._last_pts:
            return  # pts must strictly increase
        self._last_pts = t_ms
        rgba_frame = av.VideoFrame.from_bytes(bytes(rgba.constBits()), self.width, self.height, format="rgba")
        frame = rgba_frame.reformat(format="yuv420p")
        frame.pts = t_ms
        frame.time_base = _TIME_BASE
        for packet in self._stream.encode(frame):
            self._container.mux(packet)

    def close(self) -> None:
        for packet in self._stream.encode(None):
            self._container.mux(packet)
        self._container.close()


class Recording:
    """One clip in progress. Feed it with :meth:`submit`, end with :meth:`stop`.
    [full] picks the whole-image style over the circle; it's fixed for the clip
    because the frame size can't change mid-file."""

    def __init__(self, path: Path, full: bool = False) -> None:
        self.path = path
        self.full = full
        self.started = time.monotonic()
        self.frames_written = 0
        self.error: str | None = None
        self._queue: queue.Queue[tuple[QImage, float, str, float] | None] = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._run, name="otoscope-recorder", daemon=True)
        self._thread.start()

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    def submit(self, image: QImage, rotation: float, caption: str) -> None:
        item = (image, rotation, caption, time.monotonic())
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # Encoder busy: replace the waiting frame with the newer one.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                pass

    def stop(self) -> Path:
        self._queue.put(None)
        self._thread.join()
        if self.error:
            raise OSError(self.error)
        return self.path

    def _run(self) -> None:
        writer: VideoWriter | None = None
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                image, rotation, caption, at = item
                if writer is None:
                    # Sized from the first frame, like the Android recorder.
                    width, height = video_size(image.width(), image.height(), self.full)
                    writer = VideoWriter(self.path, width, height)
                    log.info("recording %dx%d to %s", width, height, self.path)
                composed = compose(image, rotation, caption, self.full, (writer.width, writer.height))
                writer.write(composed, int((at - self.started) * 1000))
                self.frames_written += 1
        except Exception as e:  # noqa: BLE001
            log.exception("recording failed")
            self.error = str(e)
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception as e:  # noqa: BLE001
                    log.exception("closing recording failed")
                    self.error = self.error or str(e)
            elif self.error is None:
                self.error = "Keine Bilder aufgenommen"
