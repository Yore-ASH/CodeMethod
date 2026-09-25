"""历史面板: 修订时间线 + 差异查看 + 全量回滚。

底部面板 (类似 VSCode 的输出/终端区域)。左侧是修订列表, 右侧是所选修订
相对其前一条的差异文本; 可以随时回滚到任意历史版本, 并且回滚本身也会被记录。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.models import Entry, Revision, format_ts
from ..editor import DiffView
from ..theme import DEFAULT_THEME, Theme

ACTION_COLORS: Dict[str, str] = {
    "create": "#4EC9B0",
    "update": "#569CD6",
    "delete": "#F14C4C",
    "purge": "#F14C4C",
    "restore_delete": "#4EC9B0",
    "impl_add": "#B5CEA8",
    "impl_update": "#9CDCFE",
    "impl_delete": "#D16969",
    "impl_restore": "#B5CEA8",
    "tag_change": "#C586C0",
    "status_change": "#DCDCAA",
    "favorite": "#DCDCAA",
    "restore": "#D7BA7D",
    "import": "#9CDCFE",
}


class HistoryPanel(QWidget):
    """修订历史查看与回滚。"""

    restore_requested = Signal(str, str)  # (entry_id, revision_id)
    undo_requested = Signal(str)
    redo_requested = Signal(str)
    preview_requested = Signal(str, str)  # (entry_id, revision_id) — 只查看不改动

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._entry: Optional[Entry] = None
        self._revisions: List[Revision] = []
        self._diff_provider = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self.list = QListWidget(left)
        self.list.setObjectName("HistoryList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.setToolTip("每一次修改都记录在案; 双击可回滚到该版本")
        self.list.itemDoubleClicked.connect(lambda _i: self._request_restore())
        left_layout.addWidget(self.list, 1)
        splitter.addWidget(left)

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.diff_header = QLabel("选择左侧修订查看差异", right)
        self.diff_header.setObjectName("SectionHeader")
        self.diff_header.setContentsMargins(10, 4, 10, 4)
        right_layout.addWidget(self.diff_header)
        self.diff_view = DiffView(right, theme=theme)
        right_layout.addWidget(self.diff_view, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([280, 620])
        layout.addWidget(splitter, 1)

    # ----------------------------------------------------------------------------
    def _build_toolbar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("CodeHeader")
        bar.setFixedHeight(32)
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(6)

        self.title_label = QLabel("历史记录", bar)
        self.title_label.setObjectName("MutedLabel")
        row.addWidget(self.title_label)
        self.count_label = QLabel("", bar)
        self.count_label.setObjectName("DimLabel")
        row.addWidget(self.count_label)
        row.addStretch(1)

        self.undo_button = QPushButton("撤销", bar)
        self.undo_button.setProperty("flat", True)
        self.undo_button.setToolTip("回退到上一条修订 (Ctrl+Z)")
        self.undo_button.clicked.connect(lambda: self._entry and self.undo_requested.emit(self._entry.id))

        self.redo_button = QPushButton("重做", bar)
        self.redo_button.setProperty("flat", True)
        self.redo_button.setToolTip("前进到下一条修订 (Ctrl+Y)")
        self.redo_button.clicked.connect(lambda: self._entry and self.redo_requested.emit(self._entry.id))

        self.restore_button = QPushButton("回滚到此版本", bar)
        self.restore_button.setToolTip("把条目全量恢复到选中的历史版本 (会记录为一条新的修订)")
        self.restore_button.clicked.connect(self._request_restore)

        self.copy_diff_button = QToolButton(bar)
        self.copy_diff_button.setText("复制差异")
        self.copy_diff_button.setAutoRaise(True)
        self.copy_diff_button.clicked.connect(self._copy_diff)

        row.addWidget(self.undo_button)
        row.addWidget(self.redo_button)
        row.addWidget(self.copy_diff_button)
        row.addWidget(self.restore_button)
        return bar

    # ----------------------------------------------------------------------------
    def set_entry(
        self,
        entry: Optional[Entry],
        revisions: Optional[List[Revision]] = None,
        *,
        can_undo: bool = False,
        can_redo: bool = False,
        cursor_revision_id: str = "",
        diff_provider=None,
    ) -> None:
        """刷新面板。

        ``diff_provider`` 是 ``callable(revision_id) -> str``, 由主窗口注入,
        这样历史面板不必知道仓储的实现细节。
        """
        self._entry = entry
        self._revisions = list(revisions or [])
        self._diff_provider = diff_provider

        self.list.blockSignals(True)
        self.list.clear()
        for rev in self._revisions:
            item = QListWidgetItem(self.list)
            item.setData(Qt.ItemDataRole.UserRole, rev.id)
            item.setText(self._format_item(rev))
            item.setToolTip(
                f"{rev.action_label}\n{format_ts(rev.timestamp)}\n{rev.summary}\n修订 ID: {rev.id}"
            )
            color = QColor(ACTION_COLORS.get(rev.action, self._theme.text_muted))
            item.setForeground(color)
            if rev.id == cursor_revision_id:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.list.addItem(item)
        self.list.blockSignals(False)

        if entry is None:
            self.title_label.setText("历史记录")
            self.count_label.setText("")
            self.restore_button.setEnabled(False)
            self.undo_button.setEnabled(False)
            self.redo_button.setEnabled(False)
            self.diff_view.set_diff("")
            self.diff_header.setText("选择左侧修订查看差异")
            return

        self.title_label.setText(f"历史记录 · {entry.display_title}")
        self.count_label.setText(f"({len(self._revisions)} 条)")
        self.restore_button.setEnabled(bool(self._revisions))
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)

        # 默认选中当前游标所在修订
        target_row = 0
        for row, rev in enumerate(self._revisions):
            if rev.id == cursor_revision_id:
                target_row = row
                break
        if self._revisions:
            self.list.setCurrentRow(target_row)
        else:
            self.diff_view.set_diff("")
            self.diff_header.setText("该条目还没有历史记录")

    def _format_item(self, rev: Revision) -> str:
        stamp = format_ts(rev.timestamp, "%m-%d %H:%M:%S")
        summary = rev.summary or rev.action_label
        if len(summary) > 60:
            summary = summary[:57] + "…"
        return f"{stamp}  ·  {rev.action_label}\n     {summary}"

    # ----------------------------------------------------------------------------
    def _on_current_changed(self, current: QListWidgetItem, _previous) -> None:
        if current is None:
            return
        revision_id = current.data(Qt.ItemDataRole.UserRole)
        rev = next((r for r in self._revisions if r.id == revision_id), None)
        if rev is None:
            return
        self.diff_header.setText(
            f"{rev.action_label} · {format_ts(rev.timestamp)} · {rev.summary}"
        )
        provider = getattr(self, "_diff_provider", None)
        if provider is None:
            self.diff_view.set_diff("（无法计算差异）")
            return
        try:
            text = provider(revision_id)
        except Exception as exc:  # pragma: no cover - 防御
            text = f"计算差异失败: {exc}"
        if not text.strip():
            text = f"该修订没有相对上一条的文本差异。\n\n动作: {rev.action_label}\n说明: {rev.summary}"
        self.diff_view.set_diff(text)

    def _request_restore(self) -> None:
        if self._entry is None:
            return
        item = self.list.currentItem()
        if item is None:
            return
        self.restore_requested.emit(self._entry.id, item.data(Qt.ItemDataRole.UserRole))

    def _copy_diff(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.diff_view.toPlainText())

    def selected_revision_id(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.diff_view.set_theme(theme)
        color_map = ACTION_COLORS
        for row in range(self.list.count()):
            item = self.list.item(row)
            revision_id = item.data(Qt.ItemDataRole.UserRole)
            rev = next((r for r in self._revisions if r.id == revision_id), None)
            if rev is not None:
                item.setForeground(QColor(color_map.get(rev.action, theme.text_muted)))


__all__ = ["HistoryPanel", "ACTION_COLORS"]
