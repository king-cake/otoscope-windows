"""Drawing the camera image: centre-crop to a square, rotate, circle mask, caption.

Shared by the live view and by saved captures, so both follow the same rules
as the Android app (CaptureGeometry.kt / FrameComposer.kt):

- the square is the frame's shorter side, centre-cropped;
- positive angles turn clockwise;
- the live view may be mirrored (horizontally and/or vertically), saved
  captures never are;
- the caption sits bottom-left, sized relative to the image.

In whole-image mode (no circle) the full frame is shown and saved instead,
fitted, turned only by the user's quarter turns; the caption rule is the same.

Mirroring is applied to the image before it's turned (rotation ∘ mirror), the
same map the Android viewport draws as mirror ∘ rotation(-angle), so turning
the probe clockwise still looks clockwise in a mirrored view.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath

CAPTION_MARGIN_RATIO = 0.05
CAPTION_TEXT_RATIO = 0.048
CAPTION_SHADOW_OFFSET_RATIO = 0.004
SHADOW_COLOR = QColor(0, 0, 0, 0xB0)


def crop_side(width: int, height: int) -> int:
    return min(width, height)


def video_side(width: int, height: int) -> int:
    """Rounded down to a multiple of 16: H.264 macroblocks."""
    return _macroblock(crop_side(width, height))


def turned_size(width: int, height: int, rotation: float) -> tuple[int, int]:
    """Size of a frame after [rotation] quarter turns (whole-image mode)."""
    return (height, width) if _is_sideways(rotation) else (width, height)


def video_size(width: int, height: int, full: bool, rotation: float = 0.0) -> tuple[int, int]:
    """Frame size of a recording: the circle's square, or the (turned) whole frame."""
    if full:
        w, h = turned_size(width, height, rotation)
        return _macroblock(w), _macroblock(h)
    side = video_side(width, height)
    return side, side


def _macroblock(n: int) -> int:
    return max(16, (n // 16) * 16)


def _is_sideways(rotation: float) -> bool:
    return round(rotation / 90) % 2 == 1


def _transform(painter: QPainter, centre: QPointF, rotation: float, mirror_h: bool, mirror_v: bool) -> None:
    painter.translate(centre)
    painter.rotate(rotation)
    painter.scale(-1 if mirror_h else 1, -1 if mirror_v else 1)


def draw_camera(
    painter: QPainter,
    image: QImage,
    rect: QRectF,
    rotation: float,
    background: QColor,
    mirror_h: bool = False,
    mirror_v: bool = False,
) -> None:
    """Paint [image] into the square [rect] as a rotated, circle-masked view."""
    crop = crop_side(image.width(), image.height())
    if crop <= 0 or rect.width() <= 0:
        return
    painter.save()
    painter.setClipRect(rect)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    _transform(painter, rect.center(), rotation, mirror_h, mirror_v)
    scale = rect.width() / crop
    painter.scale(scale, scale)
    painter.drawImage(QPointF(-image.width() / 2, -image.height() / 2), image)
    painter.restore()

    # Antialiased mask: fill the square minus the inscribed circle.
    ring = QPainterPath()
    ring.addRect(rect)
    ring.addEllipse(rect)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(ring, background)
    painter.restore()


def fit_rect(width: float, height: float, rect: QRectF) -> QRectF:
    """The largest width:height rectangle centred in [rect]."""
    if width <= 0 or height <= 0:
        return QRectF()
    scale = min(rect.width() / width, rect.height() / height)
    w, h = width * scale, height * scale
    return QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)


def draw_full(
    painter: QPainter,
    image: QImage,
    rect: QRectF,
    rotation: float = 0.0,
    mirror_h: bool = False,
    mirror_v: bool = False,
) -> QRectF:
    """The whole frame, turned by quarter turns and fitted into [rect]. Returns
    where it landed, for the caption. Captures pass no mirroring."""
    w, h = turned_size(image.width(), image.height(), rotation)
    target = fit_rect(w, h, rect)
    if target.isEmpty():
        return target
    scale = target.width() / w
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    _transform(painter, target.center(), rotation, mirror_h, mirror_v)
    painter.scale(scale, scale)
    painter.drawImage(QPointF(-image.width() / 2, -image.height() / 2), image)
    painter.restore()
    return target


def draw_caption(painter: QPainter, rect: QRectF, text: str) -> None:
    text = text.strip()
    if not text:
        return
    # Sized from the shorter side, so a rectangular frame and the circle's square match.
    side = min(rect.width(), rect.height())
    margin = side * CAPTION_MARGIN_RATIO
    font = QFont(painter.font())
    font.setPixelSize(max(1, round(side * CAPTION_TEXT_RATIO)))
    available = rect.width() - 2 * margin
    width = QFontMetricsF(font).horizontalAdvance(text)
    if width > available:
        # A long label shrinks to fit instead of running off the edge.
        font.setPixelSize(max(1, int(font.pixelSize() * available / width)))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(font)
    baseline = QPointF(rect.left() + margin, rect.bottom() - margin)
    offset = QPointF(0, max(1.0, side * CAPTION_SHADOW_OFFSET_RATIO))
    painter.setPen(SHADOW_COLOR)
    painter.drawText(baseline + offset, text)
    painter.setPen(Qt.GlobalColor.white)
    painter.drawText(baseline, text)
    painter.restore()


def compose_full_capture(
    image: QImage, caption: str, size: tuple[int, int] | None = None, rotation: float = 0.0
) -> QImage:
    """A saved still or video frame in whole-image mode: the full frame, turned
    by the user's quarter turns, never mirrored, with the caption."""
    width, height = size or turned_size(image.width(), image.height(), rotation)
    out = QImage(width, height, QImage.Format.Format_RGBA8888)
    out.fill(Qt.GlobalColor.black)
    painter = QPainter(out)
    try:
        placed = draw_full(painter, image, QRectF(0, 0, width, height), rotation)
        draw_caption(painter, placed, caption)
    finally:
        painter.end()
    return out


def compose_capture(image: QImage, rotation: float, caption: str, side: int | None = None) -> QImage:
    """A saved still or video frame: square, rotated, circle on black, caption,
    never mirrored. RGBA8888 so the video encoder can take the bytes directly."""
    if side is None:
        side = crop_side(image.width(), image.height())
    out = QImage(side, side, QImage.Format.Format_RGBA8888)
    out.fill(Qt.GlobalColor.black)
    painter = QPainter(out)
    try:
        rect = QRectF(0, 0, side, side)
        draw_camera(painter, image, rect, rotation, background=QColor(Qt.GlobalColor.black))
        draw_caption(painter, rect, caption)
    finally:
        painter.end()
    return out


def compose(image: QImage, rotation: float, caption: str, full: bool, size: tuple[int, int] | None = None) -> QImage:
    """Either capture style. [rotation] is the total angle in circle mode and
    the quarter turns in whole-image mode; [size] is the output size."""
    if full:
        return compose_full_capture(image, caption, size, rotation)
    return compose_capture(image, rotation, caption, size[0] if size else None)
