"""条目列表: 数据模型 + VSCode 风格绘制委托.

每个条目绘制成两行卡片:

    ▍ 标题                                    修订 7
      构想 · #标签1 #标签2 · Python/Go        12 实现

左边缘的竖条颜色表示规划状态。
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from ...core.languages import get_language
from ...core.models import STATUS_COLORS, STATUS_LABELS, Entry
from ..theme import DEFAULT_THEME, Theme, mono_font, ui_font

ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1
ID_ROLE = Qt.ItemDataRole.UserRole + 2

ROW_HEIGHT = 54


class EntryListModel(QAbstractListModel):
    """条目列表的数据模型。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._entries: List[Entry] = []
        self._revision_counts: dict = {}
        self._tag_colors: dict = {}

    # ---- Qt 接口 ----
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._entries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        entry = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.display_title
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(entry)
        if role == ENTRY_ROLE:
            return entry
        if role == ID_ROLE:
            return entry.id
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # ---- 数据操作 ----
    def set_entries(
        self,
        entries: List[Entry],
        *,
        revision_counts: Optional[dict] = None,
        tag_colors: Optional[dict] = None,
    ) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        if revision_counts is not None:
            self._revision_counts = dict(revision_counts)
        if tag_colors is not None:
            self._tag_colors = dict(tag_colors)
        self.endResetModel()

    def entry_at(self, index: QModelIndex) -> Optional[Entry]:
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        return self._entries[index.row()]

    def row_of(self, entry_id: str) -> int:
        for row, entry in enumerate(self._entries):
            if entry.id == entry_id:
                return row
        return -1

    def index_of(self, entry_id: str) -> QModelIndex:
        row = self.row_of(entry_id)
        return self.index(row, 0) if row >= 0 else QModelIndex()

    def entries(self) -> List[Entry]:
        return list(self._entries)

    def set_revision_counts(self, counts: dict) -> None:
        self._revision_counts = dict(counts)
        if self._entries:
            top = self.index(0, 0)
            bottom = self.index(len(self._entries) - 1, 0)
            self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.DisplayRole])

    # ---- 内部 ----
    def _tooltip(self, entry: Entry) -> str:
        lines = [entry.display_title, ""]
        if entry.description:
            description = entry.description.strip().splitlines()[0][:120]
            lines.append(description)
        lines.append(f"状态: {entry.status_label}")
        if entry.tags:
            lines.append("标签: " + ", ".join("#" + t for t in entry.tags))
        if entry.languages:
            lines.append("语言: " + ", ".join(get_language(l).name for l in entry.languages))
        lines.append(f"实现: {len(entry.active_implementations)} 个 · 代码 {entry.total_lines} 行")
        lines.append(f"修订: {self._revision_counts.get(entry.id, 0)} 次")
        updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.updated_at))
        lines.append(f"更新: {updated}")
        return "\n".join(lines)


class EntryDelegate(QStyledItemDelegate):
    """两行卡片式绘制。"""

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        entry: Optional[Entry] = index.data(ENTRY_ROLE)
        if entry is None:
            super().paint(painter, option, index)
            return

        theme = self._theme
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect.adjusted(0, 0, 0, -1)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected:
            background = QColor(theme.list_selected)
        elif hovered:
            background = QColor(theme.list_hover)
        else:
            background = QColor(theme.sidebar)
        painter.fillRect(rect, background)

        # 左侧状态条
        status_color = QColor(STATUS_COLORS.get(entry.status, theme.text_muted))
        bar = QRect(rect.left(), rect.top(), 3, rect.height())
        painter.fillRect(bar, status_color if not entry.deleted else QColor(theme.text_dim))

        content_left = rect.left() + 10
        content_right = rect.right() - 8
        top = rect.top() + 6

        # ---- 第一行: 收藏星 + 标题 + 修订数 ----
        title_font = ui_font(theme.ui_font_size, bold=True)
        painter.setFont(title_font)
        title_metrics = QFontMetricsF(title_font)

        rev = self._revision_count(index)
        rev_text = f"修订 {rev}" if rev else ""
        rev_width = 0.0
        if rev_text:
            rev_font = ui_font(max(7, theme.ui_font_size - 1))
            rev_width = QFontMetricsF(rev_font).horizontalAdvance(rev_text) + 10

        star = "★ " if entry.favorite else ""
        title_area = content_right - content_left - int(rev_width)
        title = star + entry.display_title
        if entry.deleted:
            title = "[已删除] " + title
        elided = title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, int(title_area))
        painter.setPen(QColor(theme.text_bright if selected else theme.text))
        painter.drawText(
            QRect(int(content_left), top, int(title_area), 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided,
        )

        if rev_text:
            painter.setFont(ui_font(max(7, theme.ui_font_size - 1)))
            painter.setPen(QColor(theme.text_muted))
            painter.drawText(
                QRect(int(content_right - rev_width), top, int(rev_width), 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                rev_text,
            )

        # ---- 第二行: 状态 + 标签 + 语言 ----
        painter.setFont(ui_font(max(7, theme.ui_font_size - 1)))
        second_metrics = QFontMetricsF(painter.font())
        y = top + 21

        status_text = STATUS_LABELS.get(entry.status, entry.status)
        status_width = second_metrics.horizontalAdvance(status_text) + 4
        painter.setPen(status_color)
        painter.drawText(
            QRect(int(content_left), y, int(status_width), 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            status_text,
        )

        x = content_left + status_width + 8
        meta_parts: List[str] = []
        for tag in entry.tags[:4]:
            meta_parts.append("#" + tag)
        if len(entry.tags) > 4:
            meta_parts.append(f"+{len(entry.tags) - 4}")
        langs = [get_language(l).name for l in entry.languages]
        if langs:
            meta_parts.append("/".join(langs[:3]) + ("…" if len(langs) > 3 else ""))

        meta_text = " · ".join(meta_parts)
        if meta_text:
            painter.setPen(QColor(theme.text_muted))
            available = content_right - x
            elided_meta = second_metrics.elidedText(
                meta_text, Qt.TextElideMode.ElideRight, int(max(0, available))
            )
            painter.drawText(
                QRect(int(x), y, int(max(0, available)), 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided_meta,
            )

        # ---- 实现数量徽标 ----
        impl_count = len(entry.active_implementations)
        if impl_count:
            badge = f"{impl_count} 实现"
            badge_font = ui_font(max(7, theme.ui_font_size - 1))
            painter.setFont(badge_font)
            badge_width = QFontMetricsF(badge_font).horizontalAdvance(badge) + 12
            badge_rect = QRect(
                int(content_right - badge_width), int(y - 1), int(badge_width), 16
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.sidebar_section))
            painter.drawRoundedRect(badge_rect, 8, 8)
            painter.setPen(QColor(theme.text_muted))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge)

        # 分隔线
        painter.setPen(QPen(QColor(theme.border), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.restore()

    def _revision_count(self, index: QModelIndex) -> int:
        model = index.model()
        if isinstance(model, EntryListModel):
            entry = index.data(ENTRY_ROLE)
            if entry is not None:
                return model._revision_counts.get(entry.id, 0)
        return 0


class EntryListView(QListView):
    """配置好的条目列表视图。"""

    entry_activated = Signal(str)
    selection_changed = Signal(str)
    context_requested = Signal(str, object)

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._model = EntryListModel(self)
        self._delegate = EntryDelegate(self, theme=theme)
        self.setModel(self._model)
        self.setItemDelegate(self._delegate)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.doubleClicked.connect(self._on_double_clicked)
        self.selectionModel().currentChanged.connect(self._on_current_changed)
        self.customContextMenuRequested.connect(self._on_context_menu)

    # ---- 转发 ----
    def _on_double_clicked(self, index: QModelIndex) -> None:
        entry = self._model.entry_at(index)
        if entry is not None:
            self.entry_activated.emit(entry.id)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        entry = self._model.entry_at(current)
        self.selection_changed.emit(entry.id if entry else "")

    def _on_context_menu(self, point) -> None:
        index = self.indexAt(point)
        entry = self._model.entry_at(index)
        self.context_requested.emit(entry.id if entry else "", self.viewport().mapToGlobal(point))

    # ---- API ----
    @property
    def entry_model(self) -> EntryListModel:
        return self._model

    def set_entries(self, entries: List[Entry], *, revision_counts=None, tag_colors=None) -> None:
        current = self.current_entry_id()
        self._model.set_entries(
            entries, revision_counts=revision_counts, tag_colors=tag_colors
        )
        if current:
            index = self._model.index_of(current)
            if index.isValid():
                self.setCurrentIndex(index)
                return
        if entries:
            self.setCurrentIndex(self._model.index(0, 0))

    def current_entry_id(self) -> str:
        entry = self._model.entry_at(self.currentIndex())
        return entry.id if entry else ""

    def current_entry(self) -> Optional[Entry]:
        return self._model.entry_at(self.currentIndex())

    def select_entry(self, entry_id: str) -> bool:
        index = self._model.index_of(entry_id)
        if index.isValid():
            self.setCurrentIndex(index)
            self.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
            return True
        return False

    def selected_entry_ids(self) -> List[str]:
        return [
            entry.id
            for entry in (self._model.entry_at(i) for i in self.selectionModel().selectedIndexes())
            if entry is not None
        ]

    def refresh_visible(self) -> None:
        self.viewport().update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._delegate.set_theme(theme)
        self.viewport().update()


__all__ = [
    "EntryListModel",
    "EntryDelegate",
    "EntryListView",
    "ENTRY_ROLE",
    "ID_ROLE",
    "ROW_HEIGHT",
]
