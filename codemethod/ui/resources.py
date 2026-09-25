"""程序图标: 用 QPainter 现场绘制, 不依赖任何外部图片资源。"""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap


def _draw(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    margin = size * 0.06
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.22, size * 0.22)

    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#0E639C"))
    gradient.setColorAt(1.0, QColor("#1E1E1E"))
    painter.fillPath(path, gradient)

    painter.setPen(QPen(QColor("#4EC9B0"), max(1.0, size * 0.025)))
    painter.drawPath(path)

    # </> 尖括号
    pen = QPen(QColor("#D4D4D4"), max(1.5, size * 0.06))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    left = [QPointF(size * 0.36, size * 0.34), QPointF(size * 0.22, size * 0.50), QPointF(size * 0.36, size * 0.66)]
    right = [QPointF(size * 0.64, size * 0.34), QPointF(size * 0.78, size * 0.50), QPointF(size * 0.64, size * 0.66)]
    for points in (left, right):
        for a, b in zip(points, points[1:]):
            painter.drawLine(a, b)

    # 中间的斜杠
    painter.setPen(QPen(QColor("#CE9178"), max(1.5, size * 0.06)))
    painter.drawLine(QPointF(size * 0.56, size * 0.30), QPointF(size * 0.44, size * 0.70))

    painter.end()
    return pixmap


@lru_cache(maxsize=8)
def app_icon() -> QIcon:
    """应用图标 (多尺寸)。"""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw(size))
    return icon


def color_swatch(color: str, size: int = 14) -> QPixmap:
    """生成一个色块 (用于标签颜色预览)。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor(color))
    painter.setPen(QPen(QColor("#00000060"), 1))
    painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 3, 3)
    painter.end()
    return pixmap


__all__ = ["app_icon", "color_swatch"]
