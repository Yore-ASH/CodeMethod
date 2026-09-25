"""标签胶囊 (chip) 部件。"""

from __future__ import annotations

from typing import Iterable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from ..theme import DEFAULT_THEME, Theme
from .flow_layout import FlowLayout


def _readable_text_color(background: QColor) -> QColor:
    """根据背景亮度选黑/白文字, 保证对比度。"""
    luminance = (
        0.299 * background.red() + 0.587 * background.green() + 0.114 * background.blue()
    )
    return QColor("#101010") if luminance > 150 else QColor("#F0F0F0")


class TagChip(QLabel):
    """可点击的彩色标签胶囊。"""

    clicked = Signal(str)
    remove_requested = Signal(str)

    def __init__(
        self,
        tag: str,
        *,
        color: str = "#569CD6",
        count: Optional[int] = None,
        closable: bool = False,
        removable: bool = True,
        parent: Optional[QWidget] = None,
        theme: Theme = DEFAULT_THEME,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._color = QColor(color)
        self._closable = closable
        self._removable = removable
        self._theme = theme
        self.setObjectName("TagChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip(f"标签: {tag}" + (f"（{count} 个条目）" if count is not None else ""))
        self.set_text(count)

    def set_text(self, count: Optional[int] = None) -> None:
        suffix = f"  {count}" if count is not None else ""
        close = "  ×" if self._closable else ""
        self.setText(f"#{self._tag}{suffix}{close}")

    @property
    def tag(self) -> str:
        return self._tag

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_count(self, count: Optional[int]) -> None:
        self.set_text(count)

    def sizeHint(self):  # noqa: N802
        base = super().sizeHint()
        base.setWidth(base.width() + 16)
        base.setHeight(max(base.height() + 6, 20))
        return base

    def minimumSizeHint(self):  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        radius = rect.height() / 2
        path.addRoundedRect(rect, radius, radius)

        background = QColor(self._color)
        if self.underMouse():
            background = background.lighter(120)
        painter.fillPath(path, background)
        if self._closable or self.underMouse():
            painter.setPen(QColor(255, 255, 255, 90))
            painter.drawPath(path)

        painter.setPen(_readable_text_color(background))
        painter.setFont(self.font())
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            # 点击右侧 × 区域 = 移除
            if self._closable and event.position().x() >= self.width() - 20:
                self.remove_requested.emit(self._tag)
                event.accept()
                return
            self.clicked.emit(self._tag)
        super().mousePressEvent(event)


class TagChipBar(QWidget):
    """自动换行的标签集合。"""

    tag_clicked = Signal(str)
    tag_removed = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        theme: Theme = DEFAULT_THEME,
        closable: bool = False,
        placeholder: str = "（无标签）",
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._closable = closable
        self._placeholder = placeholder
        self._layout = FlowLayout(self, margin=0, h_spacing=5, v_spacing=4)
        self.setLayout(self._layout)
        self._empty_label: Optional[QLabel] = None

    def clear_chips(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._empty_label = None

    def set_tags(
        self,
        tags: Iterable[str],
        *,
        colors: Optional[dict] = None,
        counts: Optional[dict] = None,
        max_tags: Optional[int] = None,
    ) -> None:
        """设置标签。``colors``/``counts`` 以「小写标签名」为键。"""
        self.clear_chips()
        all_tags: List[str] = list(tags)
        if not all_tags:
            self._empty_label = QLabel(self._placeholder, self)
            self._empty_label.setObjectName("DimLabel")
            self._layout.addWidget(self._empty_label)
            return

        truncated = max_tags is not None and len(all_tags) > max_tags
        items: List[str] = all_tags[:max_tags] if truncated else all_tags

        colors = colors or {}
        counts = counts or {}
        for tag in items:
            key = tag.casefold()
            chip = TagChip(
                tag,
                color=colors.get(key, self._theme.accent),
                count=counts.get(key),
                closable=self._closable,
                parent=self,
                theme=self._theme,
            )
            chip.clicked.connect(self.tag_clicked.emit)
            chip.remove_requested.connect(self.tag_removed.emit)
            self._layout.addWidget(chip)

        if truncated:
            more = QLabel(f"+{len(all_tags) - len(items)}", self)
            more.setObjectName("DimLabel")
            self._layout.addWidget(more)

        self.updateGeometry()


__all__ = ["TagChip", "TagChipBar"]
