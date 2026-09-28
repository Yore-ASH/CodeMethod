"""标签面板: 多标签筛选 (与/或/排除) + 使用次数统计。"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.query import TagMatch
from ..theme import DEFAULT_THEME, Theme


def _dot_icon(color: str, size: int = 10) -> QIcon:
    """生成一个圆点图标 (表示标签颜色)。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


MODE_ITEMS = (
    (TagMatch.ALL.value, "同时包含 (AND)"),
    (TagMatch.ANY.value, "包含任一 (OR)"),
    (TagMatch.NONE.value, "排除所选 (NOT)"),
    (TagMatch.EXACT.value, "恰好等于 (EXACT)"),
)


class TagPanel(QWidget):
    """左侧标签导航。

    多标签规划的入口: 勾选若干标签 + 选择组合方式, 即可得到候选条目集合。
    """

    selection_changed = Signal(list, str)  # (标签列表, 模式)
    tag_context_requested = Signal(str, object)

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._colors: Dict[str, str] = {}
        self._detail: Dict[str, Dict[str, int]] = {}
        self.setObjectName("SideBar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("标签筛选", self)
        title.setObjectName("SideBarTitle")
        layout.addWidget(title)

        controls = QWidget(self)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(5)

        self.mode_combo = QComboBox(controls)
        for value, label in MODE_ITEMS:
            self.mode_combo.addItem(label, value)
        self.mode_combo.setToolTip("多个标签之间的组合关系")
        self.mode_combo.currentIndexChanged.connect(lambda _i: self._emit())
        controls_layout.addWidget(self.mode_combo)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        self.filter_input = QLineEdit(controls)
        self.filter_input.setPlaceholderText("过滤标签…")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_input, 1)
        controls_layout.addLayout(filter_row)

        actions = QHBoxLayout()
        actions.setSpacing(4)
        self.clear_button = QToolButton(controls)
        self.clear_button.setText("清空")
        self.clear_button.setToolTip("清空标签选择")
        self.clear_button.clicked.connect(self.clear_selection)
        self.invert_button = QToolButton(controls)
        self.invert_button.setText("反选")
        self.invert_button.clicked.connect(self.invert_selection)
        actions.addWidget(self.clear_button)
        actions.addWidget(self.invert_button)
        actions.addStretch(1)
        controls_layout.addLayout(actions)
        layout.addWidget(controls)

        self.list = QListWidget(self)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        self.list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list, 1)

        self.summary = QLabel("未选择标签", self)
        self.summary.setObjectName("DimLabel")
        self.summary.setContentsMargins(8, 4, 8, 6)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

    # ---- 数据 ----
    def set_tags(
        self,
        tags: List[str],
        usage: Optional[Dict[str, int]] = None,
        colors: Optional[Dict[str, str]] = None,
        detail: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> None:
        """``usage`` 是标签 → 使用次数; ``detail`` 是标签 → {类别: 次数} (用于提示)。"""
        usage = usage or {}
        self._detail = {k.casefold(): v for k, v in (detail or {}).items()}
        self._colors = {k.casefold(): v for k, v in (colors or {}).items()}
        checked = {t.casefold() for t in self.selected_tags()}

        self.list.blockSignals(True)
        self.list.clear()
        for tag in tags:
            key = tag.casefold()
            count = usage.get(key, 0)
            item = QListWidgetItem(self.list)
            item.setText(f"{tag}    {count}")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setData(Qt.ItemDataRole.UserRole + 1, count)
            item.setIcon(_dot_icon(self._colors.get(key, self._theme.text_muted)))
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
            )
            item.setCheckState(
                Qt.CheckState.Checked if key in checked else Qt.CheckState.Unchecked
            )
            if count == 0:
                # 真正"从未使用"的标签才用斜体 (统计覆盖三类实体后, 这种情况很少见)
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            item.setToolTip(self._tooltip(tag, count))
        self.list.blockSignals(False)
        self._apply_filter(self.filter_input.text())
        self._update_summary()

    def _tooltip(self, tag: str, count: int) -> str:
        lines = [f"#{tag} — {'从未使用' if not count else f'{count} 个对象'}" ]
        breakdown = self._detail.get(tag.casefold()) or {}
        labels = {"module": "模块", "space": "空间", "function": "函数体"}
        parts = [f"{labels.get(k, k)} {v}" for k, v in sorted(breakdown.items()) if v]
        if parts:
            lines.append(" · ".join(parts))
        lines.append("右键可重命名/合并/删除")
        return "\n".join(lines)

    def selected_tags(self) -> List[str]:
        tags: List[str] = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                tags.append(item.data(Qt.ItemDataRole.UserRole))
        return tags

    def mode(self) -> str:
        return self.mode_combo.currentData() or TagMatch.ALL.value

    def set_mode(self, mode: str) -> None:
        index = self.mode_combo.findData(mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)

    def set_selected_tags(self, tags: List[str], *, emit: bool = True) -> None:
        wanted = {t.casefold() for t in tags}
        self.list.blockSignals(True)
        for row in range(self.list.count()):
            item = self.list.item(row)
            key = str(item.data(Qt.ItemDataRole.UserRole)).casefold()
            item.setCheckState(
                Qt.CheckState.Checked if key in wanted else Qt.CheckState.Unchecked
            )
        self.list.blockSignals(False)
        self._update_summary()
        if emit:
            self._emit()

    def toggle_tag(self, tag: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)).casefold() == tag.casefold():
                new_state = (
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                item.setCheckState(new_state)
                return

    def clear_selection(self) -> None:
        self.set_selected_tags([])

    def invert_selection(self) -> None:
        self.list.blockSignals(True)
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
        self.list.blockSignals(False)
        self._update_summary()
        self._emit()

    # ---- 内部 ----
    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        self._update_summary()
        self._emit()

    def _emit(self) -> None:
        self.selection_changed.emit(self.selected_tags(), self.mode())

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().casefold()
        for row in range(self.list.count()):
            item = self.list.item(row)
            tag = str(item.data(Qt.ItemDataRole.UserRole))
            item.setHidden(bool(needle) and needle not in tag.casefold())

    def _update_summary(self) -> None:
        chosen = self.selected_tags()
        if not chosen:
            self.summary.setText("未选择标签 — 显示全部条目")
        else:
            connector = {
                TagMatch.ALL.value: " 且 ",
                TagMatch.ANY.value: " 或 ",
                TagMatch.NONE.value: " 排除 ",
                TagMatch.EXACT.value: " 恰为 ",
            }.get(self.mode(), " 且 ")
            self.summary.setText(f"已选 {len(chosen)} 个: " + connector.join("#" + t for t in chosen))

    def _on_context_menu(self, point) -> None:
        item = self.list.itemAt(point)
        if item is None:
            return
        tag = str(item.data(Qt.ItemDataRole.UserRole))
        self.tag_context_requested.emit(tag, self.list.viewport().mapToGlobal(point))

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        # 重新绘制颜色点
        for row in range(self.list.count()):
            item = self.list.item(row)
            tag = str(item.data(Qt.ItemDataRole.UserRole))
            item.setIcon(_dot_icon(self._colors.get(tag.casefold(), theme.text_muted)))


__all__ = ["TagPanel", "MODE_ITEMS"]
