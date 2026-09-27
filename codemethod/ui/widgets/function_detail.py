"""函数体详情面板: 代码 + **变量含义表** + 独立的前置要求.

变量表是这一屏的主角 —— 自动检测出来的每个参数/字段/返回值都要求填含义,
未填写的会在表里高亮出来。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.functions import Function, Symbol, symbol_kind_label
from ...core.languages import get_language
from ...core.models import STATUS_COLORS, STATUS_LABELS, format_ts
from ..editor import CodePreview
from ..theme import DEFAULT_THEME, Theme

SYMBOL_COLUMNS = ("名称", "种类", "类型", "默认值", "含义")


class SymbolTable(QTableWidget):
    """变量含义表 (只读预览; 编辑在对话框里进行)。"""

    symbol_activated = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(0, len(SYMBOL_COLUMNS), parent)
        self._theme = theme
        self.setHorizontalHeaderLabels(SYMBOL_COLUMNS)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.cellDoubleClicked.connect(self._on_activated)

    def _on_activated(self, row: int, _column: int) -> None:
        item = self.item(row, 0)
        if item is not None:
            self.symbol_activated.emit(item.text())

    def load(self, symbols: List[Symbol]) -> None:
        self.setRowCount(0)
        missing_color = QColor(self._theme.warning)
        unset_color = QColor(self._theme.text_dim)
        for symbol in symbols:
            row = self.rowCount()
            self.insertRow(row)
            cells = [
                symbol.name,
                symbol.kind_label,
                symbol.type or "—",
                symbol.default or "—",
                symbol.meaning or ("待填写" if symbol.needs_meaning else "（可留空）"),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                    item.setData(Qt.ItemDataRole.UserRole, Symbol.key_of(symbol.name, symbol.kind))
                if column == 4:
                    if symbol.is_blank and symbol.needs_meaning:
                        item.setForeground(QBrush(missing_color))
                    elif symbol.is_blank:
                        item.setForeground(QBrush(unset_color))
                self.setItem(row, column, item)
        self.resizeRowsToContents()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme


class FunctionDetailPanel(QWidget):
    """展示一个函数体。"""

    edit_requested = Signal(str)
    history_requested = Signal(str)
    favorite_toggled = Signal(str)
    delete_requested = Signal(str)
    duplicate_requested = Signal(str)
    tag_clicked = Signal(str)
    copy_done = Signal(str)
    export_requested = Signal(str, str)
    redetect_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._function: Optional[Function] = None
        self.setObjectName("DetailPane")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._empty = QLabel(
            "选择左侧的一个函数体以查看\n\n"
            "函数体会自动检测变量声明, 并请你为每个变量填写含义。\n"
            "它还有自己独立的前置要求 (例如「Python 3.10+」)。",
            self,
        )
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setObjectName("DimLabel")
        self._empty.setWordWrap(True)
        root.addWidget(self._empty, 1)

        self._content = QWidget(self)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Vertical, self._content)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_code_panel())
        splitter.addWidget(self._build_symbol_panel())
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([380, 280])
        layout.addWidget(splitter, 1)

        root.addWidget(self._content, 1)
        self._content.setVisible(False)

        self._status_label = QLabel("", self)
        self._status_label.setObjectName("DimLabel")
        self._status_label.setContentsMargins(10, 3, 10, 5)
        root.addWidget(self._status_label)

    # ----------------------------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QWidget(self)
        layout = QVBoxLayout(header)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._title = QLabel("", header)
        self._title.setObjectName("TitleLabel")
        self._title.setWordWrap(True)
        self._title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self._title, 1)
        self._fav_button = QToolButton(header)
        self._fav_button.setText("☆")
        self._fav_button.setAutoRaise(True)
        self._fav_button.clicked.connect(
            lambda: self._function and self.favorite_toggled.emit(self._function.id)
        )
        row.addWidget(self._fav_button)
        layout.addLayout(row)

        info = QHBoxLayout()
        info.setSpacing(8)
        kind_badge = QLabel("函数体", header)
        kind_badge.setStyleSheet(
            "color:#C586C0; border:1px solid #C586C0; border-radius:8px; padding:1px 8px;"
        )
        info.addWidget(kind_badge)
        self._language_badge = QLabel("", header)
        info.addWidget(self._language_badge)
        self._status_badge = QLabel("", header)
        info.addWidget(self._status_badge)
        self._unresolved = QLabel("", header)
        info.addWidget(self._unresolved)
        self._meta = QLabel("", header)
        self._meta.setObjectName("DimLabel")
        info.addWidget(self._meta)
        info.addStretch(1)

        self._edit_button = QPushButton("编辑", header)
        self._edit_button.clicked.connect(
            lambda: self._function and self.edit_requested.emit(self._function.id)
        )
        self._detect_button = QPushButton("重新检测变量", header)
        self._detect_button.setProperty("flat", True)
        self._detect_button.setToolTip("按当前代码重新扫描变量声明 (已填写的含义不会被覆盖)")
        self._detect_button.clicked.connect(
            lambda: self._function and self.redetect_requested.emit(self._function.id)
        )
        self._history_button = QPushButton("历史", header)
        self._history_button.setProperty("flat", True)
        self._history_button.clicked.connect(
            lambda: self._function and self.history_requested.emit(self._function.id)
        )
        self._more_button = QToolButton(header)
        self._more_button.setText("⋯")
        self._more_button.setFixedWidth(30)
        self._more_button.setAutoRaise(True)
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._build_more_menu()

        info.addWidget(self._edit_button)
        info.addWidget(self._detect_button)
        info.addWidget(self._history_button)
        info.addWidget(self._more_button)
        layout.addLayout(info)

        divider = QFrame(header)
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {self._theme.border};")
        layout.addWidget(divider)
        return header

    def _build_more_menu(self) -> None:
        menu = QMenu(self._more_button)
        items = [
            ("复制代码", self._copy_code),
            ("复制变量含义表", self._copy_symbols),
            ("复制函数体为 Markdown",
             lambda: self._function and self.export_requested.emit(self._function.id, "markdown")),
            ("复制函数体为 JSON",
             lambda: self._function and self.export_requested.emit(self._function.id, "json")),
            (None, None),
            ("创建副本", lambda: self._function and self.duplicate_requested.emit(self._function.id)),
            ("删除函数体", lambda: self._function and self.delete_requested.emit(self._function.id)),
        ]
        for text, slot in items:
            if text is None:
                menu.addSeparator()
                continue
            action = QAction(text, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
        self._more_button.setMenu(menu)

    def _build_code_panel(self) -> QWidget:
        panel = QWidget(self._content)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget(panel)
        self.tabs.setDocumentMode(True)

        self.preview = CodePreview(self.tabs, theme=self._theme)
        self.preview.set_editable(False)
        self.preview.edit_requested.connect(
            lambda: self._function and self.edit_requested.emit(self._function.id)
        )
        self.preview.copy_requested.connect(lambda _t: self.copy_done.emit("已复制函数代码到剪贴板"))
        self.tabs.addTab(self.preview, "代码")

        prereq_page = QWidget(self.tabs)
        prereq_layout = QVBoxLayout(prereq_page)
        prereq_layout.setContentsMargins(12, 10, 12, 10)
        self.prereq_view = QTextBrowser(prereq_page)
        self.prereq_view.setFrameShape(QFrame.Shape.NoFrame)
        self.prereq_view.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        prereq_layout.addWidget(self.prereq_view)
        self.tabs.addTab(prereq_page, "前置要求")

        notes_page = QWidget(self.tabs)
        notes_layout = QVBoxLayout(notes_page)
        notes_layout.setContentsMargins(12, 10, 12, 10)
        self.notes_view = QTextBrowser(notes_page)
        self.notes_view.setFrameShape(QFrame.Shape.NoFrame)
        self.notes_view.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        notes_layout.addWidget(self.notes_view)
        self.tabs.addTab(notes_page, "说明")

        layout.addWidget(self.tabs, 1)
        return panel

    def _build_symbol_panel(self) -> QWidget:
        panel = QWidget(self._content)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget(panel)
        bar.setObjectName("CodeHeader")
        bar.setFixedHeight(30)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 0, 8, 0)
        row.setSpacing(6)
        label = QLabel("变量含义", bar)
        label.setObjectName("MutedLabel")
        row.addWidget(label)
        self._symbol_summary = QLabel("", bar)
        self._symbol_summary.setObjectName("DimLabel")
        row.addWidget(self._symbol_summary)
        row.addStretch(1)
        copy_button = QToolButton(bar)
        copy_button.setText("复制表")
        copy_button.setAutoRaise(True)
        copy_button.clicked.connect(self._copy_symbols)
        row.addWidget(copy_button)
        layout.addWidget(bar)

        self.symbol_table = SymbolTable(panel, theme=self._theme)
        self.symbol_table.symbol_activated.connect(
            lambda _name: self._function and self.edit_requested.emit(self._function.id)
        )
        layout.addWidget(self.symbol_table, 1)
        return panel

    # ----------------------------------------------------------------------------
    def _copy_code(self) -> None:
        if self._function is None:
            return
        QApplication.clipboard().setText(self._function.code)
        self.copy_done.emit(f"已复制代码 ({self._function.total_lines} 行) 到剪贴板")

    def _copy_symbols(self) -> None:
        if self._function is None:
            return
        lines = ["| 名称 | 种类 | 类型 | 默认值 | 含义 |", "| --- | --- | --- | --- | --- |"]
        for symbol in self._function.symbols:
            lines.append(
                f"| `{symbol.name}` | {symbol.kind_label} | {symbol.type or '—'} "
                f"| {symbol.default or '—'} | {symbol.meaning or '（未填写）'} |"
            )
        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.copy_done.emit(f"已复制 {len(self._function.symbols)} 个变量的含义表到剪贴板")

    def set_entry(self, function: Optional[Function], *, tag_colors: Optional[Dict[str, str]] = None) -> None:
        """与模块详情保持同名接口。"""
        self.set_function(function)

    def set_function(self, function: Optional[Function]) -> None:
        self._function = function
        if function is None:
            self._content.setVisible(False)
            self._empty.setVisible(True)
            self._status_label.setText("")
            self.symbol_table.setRowCount(0)
            return

        self._empty.setVisible(False)
        self._content.setVisible(True)

        self._title.setText(function.display_title)
        self._fav_button.setText("★" if function.favorite else "☆")
        self._language_badge.setText(get_language(function.language).name)
        self._language_badge.setStyleSheet(
            f"color:{get_language(function.language).color};"
            f"border:1px solid {get_language(function.language).color};"
            "border-radius:8px; padding:1px 8px;"
        )
        self._status_badge.setText(STATUS_LABELS.get(function.status, function.status))
        self._status_badge.setStyleSheet(
            f"color:{STATUS_COLORS.get(function.status, self._theme.text_muted)};"
            f"border:1px solid {STATUS_COLORS.get(function.status, self._theme.text_muted)};"
            "border-radius:8px; padding:1px 8px;"
        )
        missing = function.required_symbols_missing_meaning
        if missing:
            self._unresolved.setText(f"⚠ {len(missing)} 个变量待填含义")
            self._unresolved.setStyleSheet(f"color:{self._theme.warning}; font-weight:bold;")
        else:
            self._unresolved.setText("")
        self._meta.setText(
            f"更新 {format_ts(function.updated_at)} · {function.total_lines} 行 · "
            f"{len(function.symbols)} 个变量 · ID {function.id}"
        )

        self.preview.set_code(
            function.code, language=function.language, filename=f"{function.name or 'function'}"
        )
        self.tabs.setTabText(0, f"代码 · {get_language(function.language).name}")

        self.prereq_view.setHtml(self._html_or_placeholder(function.prerequisites, "（未填写前置要求）"))
        self.notes_view.setHtml(self._html_or_placeholder(function.description, "（未填写说明）"))

        self.symbol_table.load(function.symbols)
        blank = len(function.symbols_missing_meaning)
        self._symbol_summary.setText(
            f"({len(function.symbols)} 个, {blank} 个待填写)" if blank
            else f"({len(function.symbols)} 个, 全部已填写)"
        )

        self._status_label.setText(
            f"{function.language_name} · {function.total_lines} 行 · "
            f"{len(function.symbols)} 个变量 · "
            + (f"{len(missing)} 个必填项未完成" if missing else "含义已完整")
        )

    @staticmethod
    def _html_or_placeholder(text: str, placeholder: str) -> str:
        import html as html_module

        body = (text or "").strip()
        if not body:
            return f"<span style='color:#808080;font-style:italic'>{placeholder}</span>"
        escaped = html_module.escape(body).replace("\n", "<br/>")
        return f"<div style='white-space:pre-wrap;line-height:150%'>{escaped}</div>"

    def current_function(self) -> Optional[Function]:
        return self._function

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.preview.editor.set_theme(theme)
        self.symbol_table.set_theme(theme)


__all__ = ["FunctionDetailPanel", "SymbolTable", "SYMBOL_COLUMNS"]
