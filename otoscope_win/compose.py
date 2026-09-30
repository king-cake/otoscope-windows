"""Drawing the camera image: centre-crop to a square, rotate, circle mask, caption.

Shared by the live view and by saved captures, so both follow the same rules
as the Android app (CaptureGeometry.kt / FrameComposer.kt):

- the square is the frame's shorter side, centre-cropped;
- positive angles turn clockwise;
- the live view may be mirrored, saved captures never are;
- the caption sits bottom-left, sized relative to the square.
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
    return max(16, (crop_side(width, height) // 16) * 16)


def draw_camera(
    painter: QPainter,
    image: QImage,
    rect: QRectF,
    rotation: float,
    mirror: bool,
    background: QColor,
) -> None:
    """Paint [image] into the square [rect] as a rotated, circle-masked view."""
    crop = crop_side(image.width(), image.height())
    if crop <= 0 or rect.width() <= 0:
        return
    painter.save()
    painter.setClipRect(rect)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.translate(rect.center())
    if mirror:
        # mirror ∘ rotation(-angle): turning the probe clockwise still looks clockwise.
        painter.scale(-1, 1)
        painter.rotate(-rotation)
    else:
        painter.rotate(rotation)
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


def draw_caption(painter: QPainter, rect: QRectF, text: str) -> None:
    text = text.strip()
    if not text:
        return
    side = rect.width()
    margin = side * CAPTION_MARGIN_RATIO
    font = QFont(painter.font())
    font.setPixelSize(max(1, round(side * CAPTION_TEXT_RATIO)))
    available = side - 2 * margin
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
        draw_camera(painter, image, rect, rotation, mirror=False, background=QColor(Qt.GlobalColor.black))
        draw_caption(painter, rect, caption)
    finally:
        painter.end()
    return out
