"""检索栏: 关键词输入 + 排序 + 检索范围。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.query import SORT_OPTIONS
from ..theme import DEFAULT_THEME, Theme


class SearchBar(QWidget):
    """顶部检索栏。

    输入有 250ms 防抖, 避免每敲一个字都全库扫描。
    """

    query_changed = Signal(str)
    sort_changed = Signal(str)
    scope_changed = Signal(bool, bool)  # (搜索代码, 搜索描述)

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self._emit_query)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText('检索条目…  支持 tag:标签  lang:python  status:done  is:favorite  -排除  "短语"')
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(lambda _t: self._debounce.start())
        self.input.returnPressed.connect(self._emit_query)
        row.addWidget(self.input, 1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        sort_label = QLabel("排序", self)
        sort_label.setObjectName("MutedLabel")
        self.sort_combo = QComboBox(self)
        for key, label in SORT_OPTIONS:
            self.sort_combo.addItem(label, key)
        self.sort_combo.currentIndexChanged.connect(
            lambda _i: self.sort_changed.emit(self.sort_combo.currentData())
        )

        self.scope_code = QToolButton(self)
        self.scope_code.setText("代码")
        self.scope_code.setCheckable(True)
        self.scope_code.setChecked(True)
        self.scope_code.setToolTip("是否在源代码中检索")
        self.scope_code.toggled.connect(self._on_scope)

        self.scope_desc = QToolButton(self)
        self.scope_desc.setText("描述")
        self.scope_desc.setCheckable(True)
        self.scope_desc.setChecked(True)
        self.scope_desc.setToolTip("是否在描述/前置要求中检索")
        self.scope_desc.toggled.connect(self._on_scope)

        row2.addWidget(sort_label)
        row2.addWidget(self.sort_combo)
        row2.addStretch(1)
        row2.addWidget(QLabel("范围", self))
        row2.addWidget(self.scope_code)
        row2.addWidget(self.scope_desc)
        layout.addLayout(row2)

    # ---- 事件 ----
    def _emit_query(self) -> None:
        self.query_changed.emit(self.input.text())

    def _on_scope(self) -> None:
        self.scope_changed.emit(self.scope_code.isChecked(), self.scope_desc.isChecked())

    # ---- API ----
    def text(self) -> str:
        return self.input.text()

    def set_text(self, text: str, *, emit: bool = True) -> None:
        self.input.setText(text)
        if emit:
            self._emit_query()

    def clear(self) -> None:
        self.input.clear()

    def sort_key(self) -> str:
        return self.sort_combo.currentData() or "updated_desc"

    def set_sort_key(self, key: str) -> None:
        index = self.sort_combo.findData(key)
        if index >= 0:
            self.sort_combo.setCurrentIndex(index)

    def scope(self) -> tuple:
        return self.scope_code.isChecked(), self.scope_desc.isChecked()

    def append_token(self, token: str) -> None:
        """向检索框追加一个词项 (例如点击标签时)。"""
        text = self.input.text().strip()
        if token in text:
            return
        combined = f"{text} {token}".strip()
        self.set_text(combined)


__all__ = ["SearchBar"]
