"""把对话框夹进屏幕可用区域的小工具.

问题: 很多对话框写死了 ``setMinimumSize(820, 620)`` / ``resize(1280, 860)``。
在小屏笔记本或高 DPI 缩放下, 这个尺寸可能**超过屏幕高度**, 于是底部的
「保存 / 取消」按钮跑到屏幕外面, 用户必须先手动把窗口改小才能点保存。

这里的做法是在对话框**显示时**统一检查一次: 如果它比屏幕可用区域还大, 就把它
(以及它的最小尺寸) 一起压到能放下的范围内, 并居中显示。最小尺寸也要一起压,
否则 Qt 会把窗口顶回那个放不下的大小。
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QWidget

# 最多占屏幕可用区域的这个比例, 四周留一点余地
SCREEN_MARGIN = 0.94

# 窗口装饰 (标题栏 + 边框) 的大致高度。可用区域算的是客户区之外的范围, 而
# frameGeometry 会把装饰也算进去 —— 不留出这点高度的话, 明明"放得下"的窗口
# 仍然会把底部按钮顶到屏幕外。
FRAME_ALLOWANCE = 48

# 屏幕再小也要能放下的下限 (低于这个就没法排版了, 交给布局去滚动/压缩)
FLOOR_WIDTH = 360
FLOOR_HEIGHT = 260


def available_geometry(widget: Optional[QWidget] = None) -> Optional[QRect]:
    """取 ``widget`` 所在屏幕的可用区域 (不含任务栏); 取不到时返回 ``None``。"""
    screen = None
    if widget is not None:
        handle = widget.windowHandle()
        if handle is not None:
            screen = handle.screen()
        if screen is None:
            center = widget.frameGeometry().center()
            screen = QGuiApplication.screenAt(center)
    if screen is None:
        screen = QGuiApplication.screenAt(QCursor.pos())
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:      # pragma: no cover - 无 GUI 时
        return None
    rect = screen.availableGeometry()
    if rect.width() <= 0 or rect.height() <= 0:      # pragma: no cover
        return None
    return rect


def max_client_size(geometry: Optional[QRect] = None) -> Tuple[int, int]:
    """当前屏幕能容纳的**客户区**上限 (已扣掉窗口装饰)。"""
    rect = geometry if geometry is not None else available_geometry()
    if rect is None:
        return FLOOR_WIDTH, FLOOR_HEIGHT
    return (
        max(FLOOR_WIDTH, int(rect.width() * SCREEN_MARGIN)),
        max(FLOOR_HEIGHT, int(rect.height() * SCREEN_MARGIN) - FRAME_ALLOWANCE),
    )


def clamp_size(
    width: int, height: int, *, geometry: Optional[QRect] = None
) -> Tuple[int, int]:
    """把一个期望尺寸夹到屏幕可用区域内 (永远不小于 ``FLOOR_*``)。"""
    max_width, max_height = max_client_size(geometry)
    return min(max(FLOOR_WIDTH, int(width)), max_width), min(
        max(FLOOR_HEIGHT, int(height)), max_height
    )


def fit_dialog_to_screen(
    dialog: QWidget,
    *,
    width: int = 0,
    height: int = 0,
    min_width: int = 0,
    min_height: int = 0,
    center: bool = True,
) -> bool:
    """确保 ``dialog`` 放得进屏幕; 需要时缩身并居中。返回是否做了调整。

    只在"放不下"时才动它 —— 屏幕够大时对话框保持自己声明的尺寸。
    """
    rect = available_geometry(dialog)
    if rect is None:      # pragma: no cover - 无 GUI 时
        return False

    max_width, max_height = max_client_size(rect)

    # 先看当前实际尺寸 (min 与 size 里取大的那个, 因为 Qt 会用 min 顶住窗口)
    current_min = dialog.minimumSize()
    current = dialog.size()
    target_w = width or max(current.width(), current_min.width())
    target_h = height or max(current.height(), current_min.height())

    need_w = max(target_w, current_min.width(), min_width)
    need_h = max(target_h, current_min.height(), min_height)

    if need_w <= max_width and need_h <= max_height:
        if center:
            _center_on(dialog, rect)
        return False

    small_w, small_h = clamp_size(need_w, need_h, geometry=rect)
    dialog.setMinimumSize(min(small_w, max_width), min(small_h, max_height))
    dialog.resize(small_w, small_h)
    if center:
        _center_on(dialog, rect)
    return True


def fit_window_to_screen(window: QWidget, *, margin: float = SCREEN_MARGIN) -> bool:
    """确保主窗口**整个 frame** 都在屏幕可用区域内。返回是否做了调整。

    为什么要管主窗口: `resize(1440, 900)` 这种首选尺寸在 1536×864 的屏幕上
    比可用高度还高, 于是窗口底部的面板与状态栏直接沉到屏幕外; 而对话框默认
    **以父窗口为中心**, 父窗口下沉会把对话框一起推到屏幕下方, 底部的「保存」
    就点不到了。

    已经最大化 / 全屏的窗口不做处理。
    """
    if window.isMaximized() or window.isFullScreen():
        return False
    rect = available_geometry(window)
    if rect is None:      # pragma: no cover - 无 GUI 时
        return False

    max_width = max(FLOOR_WIDTH, int(rect.width() * margin))
    max_height = max(FLOOR_HEIGHT, int(rect.height() * margin) - FRAME_ALLOWANCE)

    # 声明的**最小尺寸**本身就可能比屏幕还大 (主窗口默认最小 1080×700, 小屏或
    # 高缩放下根本放不下)。最小尺寸不压下来, Qt 会把窗口顶回去, 怎么 resize 都没用。
    minimum = window.minimumSize()
    if minimum.width() > max_width or minimum.height() > max_height:
        window.setMinimumSize(
            min(minimum.width(), max_width), min(minimum.height(), max_height)
        )

    size = window.size()
    width = min(max(size.width(), FLOOR_WIDTH), max_width)
    height = min(max(size.height(), FLOOR_HEIGHT), max_height)
    changed = False
    if (width, height) != (size.width(), size.height()):
        window.resize(width, height)
        changed = True

    # 位置也拉回可见区域 —— 之前在大显示器上保存的 geometry 可能整个在屏幕外
    frame = window.frameGeometry()
    x = frame.x()
    y = frame.y()
    if frame.right() > rect.right() or frame.width() > rect.width():
        x = max(rect.x(), rect.right() - frame.width() + 1)
    if frame.bottom() > rect.bottom() or frame.height() > rect.height():
        y = max(rect.y(), rect.bottom() - frame.height() + 1)
    x = min(max(x, rect.x()), max(rect.x(), rect.right() - frame.width() + 1))
    y = min(max(y, rect.y()), max(rect.y(), rect.bottom() - frame.height() + 1))
    if (x, y) != (frame.x(), frame.y()):
        window.move(x, y)
        changed = True
    return changed


def _center_on(dialog: QWidget, rect: QRect) -> None:
    """把窗口移到指定区域的中央 (不改变尺寸)。"""
    frame = dialog.frameGeometry()
    if frame.width() <= 0 or frame.height() <= 0:      # pragma: no cover
        return
    x = rect.x() + max(0, (rect.width() - frame.width()) // 2)
    y = rect.y() + max(0, (rect.height() - frame.height()) // 2)
    dialog.move(QPoint(x, y))


__all__ = [
    "SCREEN_MARGIN",
    "FRAME_ALLOWANCE",
    "available_geometry",
    "max_client_size",
    "clamp_size",
    "fit_dialog_to_screen",
    "fit_window_to_screen",
]
