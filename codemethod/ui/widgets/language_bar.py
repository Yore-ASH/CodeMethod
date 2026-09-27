"""语言占比条 (GitHub 风格) 与图例.

GitHub 仓库页顶部那条彩色横条就是它: 按语言把横条切成若干段, 每段宽度正比于该语言
的代码字节数, 下面跟一行图例。
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ...core.spaces import LanguageShare
from ..theme import DEFAULT_THEME, Theme


class LanguageBar(QWidget):
    """彩色占比条。鼠标悬停会显示该语言的名称与占比。"""

    language_clicked = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        theme: Theme = DEFAULT_THEME,
        height: int = 10,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._shares: List[LanguageShare] = []
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_shares(self, shares: Iterable[LanguageShare]) -> None:
        self._shares = [s for s in shares if s.percent > 0]
        self.setToolTip(self._tooltip_text())
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def _tooltip_text(self) -> str:
        if not self._shares:
            return "（暂无代码文件）"
        return "\n".join(f"{s.name}: {s.percent_label} ({s.bytes} 字节)" for s in self._shares)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = rect.height() / 2

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        if not self._shares:
            painter.fillRect(rect, QColor(self._theme.sidebar_section))
            painter.end()
            return

        x = 0.0
        total = sum(s.percent for s in self._shares) or 100.0
        for index, share in enumerate(self._shares):
            width = rect.width() * (share.percent / total)
            # 最后一段补齐, 避免浮点误差留下一条缝
            if index == len(self._shares) - 1:
                width = rect.width() - x
            painter.fillRect(
                QRectF(x, 0.0, width, rect.height()), QColor(share.color)
            )
            x += width
        painter.end()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._shares:
            return
        share = self._share_at(event.position().x())
        if share is not None:
            self.language_clicked.emit(share.language)

    def _share_at(self, x: float) -> Optional[LanguageShare]:
        total = sum(s.percent for s in self._shares) or 100.0
        cursor = 0.0
        for share in self._shares:
            width = self.width() * (share.percent / total)
            if cursor <= x <= cursor + width:
                return share
            cursor += width
        return None


class LanguageLegend(QWidget):
    """占比条下面的图例: 彩色圆点 + 语言名 + 百分比。"""

    language_clicked = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        theme: Theme = DEFAULT_THEME,
        columns: int = 3,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._columns = max(1, columns)
        self._grid = QVBoxLayout(self)
        self._grid.setContentsMargins(0, 4, 0, 0)
        self._grid.setSpacing(3)
        self._rows: List[QHBoxLayout] = []

    def clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            layout = item.layout() if item else None
            if layout is not None:
                while layout.count():
                    child = layout.takeAt(0)
                    widget = child.widget() if child else None
                    if widget is not None:
                        widget.setParent(None)
                        widget.deleteLater()
                layout.deleteLater()
        self._rows.clear()

    def set_shares(self, shares: Iterable[LanguageShare]) -> None:
        self.clear()
        items = [s for s in shares if s.percent > 0]
        if not items:
            empty = QLabel("（暂无代码文件）", self)
            empty.setObjectName("DimLabel")
            self._grid.addWidget(empty)
            return

        row: Optional[QHBoxLayout] = None
        for index, share in enumerate(items):
            if index % self._columns == 0:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(10)
                self._grid.addLayout(row)
                self._rows.append(row)
            assert row is not None
            row.addWidget(self._make_entry(share))
        if row is not None:
            row.addStretch(1)

    def _make_entry(self, share: LanguageShare) -> QWidget:
        holder = QWidget(self)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        dot = QLabel(holder)
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(
            f"background-color: {share.color}; border-radius: 5px;"
            "border: 1px solid rgba(0,0,0,60);"
        )
        layout.addWidget(dot)

        text = QLabel(f"{share.name} {share.percent_label}", holder)
        text.setObjectName("MutedLabel")
        text.setToolTip(
            f"{share.name}\n占比 {share.percent_label}\n"
            f"{share.bytes} 字节 · {share.files} 个文件 · {share.lines} 行"
        )
        layout.addWidget(text)
        holder.setCursor(Qt.CursorShape.PointingHandCursor)
        holder.mouseReleaseEvent = lambda _e, lang=share.language: self.language_clicked.emit(lang)
        return holder

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme


__all__ = ["LanguageBar", "LanguageLegend"]
