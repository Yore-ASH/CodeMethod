"""详情面板头部动作按钮的小工具.

Qt 在水平空间不足时会把 QPushButton 压缩到比 sizeHint 更窄, 中文按钮
(例如「重新检测变量」) 就会被截掉半截。这里按字体度量给一个最小宽度,
保证按钮文字永远完整显示。
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QSizePolicy


def fit_action_button(button: QPushButton, *, extra: int = 34) -> None:
    """按字体度量给按钮留够宽度, 避免中文按钮文字被挤掉半截。"""
    metrics = button.fontMetrics()
    width = metrics.horizontalAdvance(button.text()) + extra
    button.setMinimumWidth(width)
    button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)


__all__ = ["fit_action_button"]
