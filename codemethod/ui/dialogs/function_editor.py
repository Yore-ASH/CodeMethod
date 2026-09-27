"""函数体编辑对话框: 代码 + **变量含义表** + 独立的前置要求.

核心交互
--------
1. 用户粘贴/编写某个语言的函数代码;
2. 编辑器**自动检测**变量声明 (参数 / 局部变量 / 字段 / 全局 / 常量 / 返回值),
   填入下方表格的"名称 / 种类 / 类型 / 默认值"几列;
3. 用户必须为每个变量填写**含义** (参数、字段、返回值属于必填项);
4. 保存时若仍有必填项为空, 会列出来并跳到第一条, 不会静默放过。

重新检测时**永远不会覆盖已经填好的含义** —— 见 :meth:`Function.apply_detected`。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.functions import (
    REQUIRED_SYMBOL_KINDS,
    SYMBOL_KIND_LABELS,
    SYMBOL_KIND_ORDER,
    Function,
    Symbol,
)
from ...core.languages import get_language, language_choices
from ...core.models import STATUS_LABELS, STATUS_ORDER, normalize_tags
from ...core.repository import detect_function_symbols
from ..editor import CodeEditor
from ..native import ThemedDialog
from ..theme import DEFAULT_THEME, Theme

MEANING_COLUMN = 4


class FunctionSymbolTable(QTableWidget):
    """可编辑的变量含义表 (只有"含义"一列可改, 其余由检测器填充)。"""

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(0, 5, parent)
        self._theme = theme
        self.setHorizontalHeaderLabels(("名称", "种类", "类型", "默认值", "含义 *"))
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        header = self.horizontalHeader()
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(MEANING_COLUMN, QHeaderView.ResizeMode.Stretch)

    # ---- 数据 ----
    def load(self, symbols: List[Symbol]) -> None:
        self.setRowCount(0)
        for symbol in symbols:
            self.append_symbol(symbol)
        self.resizeRowsToContents()

    def append_symbol(self, symbol: Symbol) -> int:
        row = self.rowCount()
        self.insertRow(row)
        for column, text in enumerate(
            (symbol.name, symbol.kind_label, symbol.type or "—", symbol.default or "—")
        ):
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if column == 0:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
                item.setData(Qt.ItemDataRole.UserRole, symbol.kind)
            self.setItem(row, column, item)
        meaning = QTableWidgetItem(symbol.meaning)
        self.setItem(row, MEANING_COLUMN, meaning)
        self._decorate(row, symbol)
        return row

    def _decorate(self, row: int, symbol: Symbol) -> None:
        """未填写的必填项用警告色标出来, 一眼能看出还差什么。"""
        item = self.item(row, MEANING_COLUMN)
        if item is None:
            return
        if symbol.is_blank and symbol.kind in REQUIRED_SYMBOL_KINDS:
            item.setForeground(QBrush(QColor(self._theme.warning)))
            item.setToolTip("这一项是必填的 —— 参数 / 字段 / 返回值的含义必须写清楚")
        elif symbol.is_blank:
            item.setForeground(QBrush(QColor(self._theme.text_dim)))
            item.setToolTip("局部变量可以留空")
        else:
            item.setForeground(QBrush(QColor(self._theme.text)))
            item.setToolTip("")

    def refresh_decoration(self) -> None:
        for row in range(self.rowCount()):
            self._decorate(row, self.symbol_at(row))

    def symbol_at(self, row: int) -> Symbol:
        def text(column: int) -> str:
            item = self.item(row, column)
            return item.text().strip() if item is not None else ""

        kind_item = self.item(row, 0)
        kind = ""
        if kind_item is not None:
            kind = str(kind_item.data(Qt.ItemDataRole.UserRole) or "")
        if not kind:
            reverse = {label: name for name, label in SYMBOL_KIND_LABELS.items()}
            kind = reverse.get(text(1), "local")
        return Symbol(
            name=text(0),
            kind=kind,
            type="" if text(2) in ("—", "") else text(2),
            default="" if text(3) in ("—", "") else text(3),
            meaning=text(MEANING_COLUMN),
        )

    def symbols(self) -> List[Symbol]:
        return [self.symbol_at(row) for row in range(self.rowCount())]

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.refresh_decoration()


class FunctionEditorDialog(ThemedDialog):
    """新建 / 编辑一个函数体。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        function: Optional[Function] = None,
        existing_tags: Optional[List[str]] = None,
        theme: Theme = DEFAULT_THEME,
        default_language: str = "python",
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._function = function
        self._is_new = function is None
        self._loading = True

        self.setWindowTitle("新建函数体" if self._is_new else "编辑函数体")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 820)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_form(existing_tags or []))
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes([420, 720])
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox(self)
        self.save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(self.reject)
        buttons.accepted.connect(self._on_save)
        root.addWidget(buttons)

        self._detect_timer = QTimer(self)
        self._detect_timer.setSingleShot(True)
        self._detect_timer.setInterval(600)
        self._detect_timer.timeout.connect(self.redetect)

        self._load()
        self._loading = False

    # ----------------------------------------------------------------------------
    def _build_form(self, existing_tags: List[str]) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(8)

        basics = QGroupBox("基本信息", panel)
        form = QFormLayout(basics)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(6)

        self.name_edit = QLineEdit(basics)
        self.name_edit.setPlaceholderText("函数名, 例如 reverse")
        form.addRow("函数名 *", self.name_edit)

        self.language_combo = QComboBox(basics)
        for lang_id, lang_name in language_choices():
            self.language_combo.addItem(lang_name, lang_id)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        form.addRow("语言", self.language_combo)

        self.status_combo = QComboBox(basics)
        for status in STATUS_ORDER:
            self.status_combo.addItem(STATUS_LABELS[status], status)
        form.addRow("规划状态", self.status_combo)

        self.favorite_check = QCheckBox("加入收藏", basics)
        form.addRow("", self.favorite_check)

        self.strict_check = QCheckBox("保存前必须填完所有必填变量含义", basics)
        self.strict_check.setChecked(True)
        self.strict_check.setToolTip(
            "打开时: 参数 / 字段 / 返回值只要有一条没写含义, 保存就会被拦下来。\n"
            "关掉它仅在你确实想先存草稿时使用。"
        )
        form.addRow("", self.strict_check)
        layout.addWidget(basics)

        tags_group = QGroupBox("标签", panel)
        tags_layout = QVBoxLayout(tags_group)
        self.tags_edit = QLineEdit(tags_group)
        self.tags_edit.setPlaceholderText("用逗号或空格分隔")
        completer = QCompleter(existing_tags, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.tags_edit.setCompleter(completer)
        tags_layout.addWidget(self.tags_edit)
        layout.addWidget(tags_group)

        signature_group = QGroupBox("函数签名", panel)
        signature_layout = QVBoxLayout(signature_group)
        self.signature_edit = QPlainTextEdit(signature_group)
        self.signature_edit.setPlaceholderText("可选。留空时会从代码里取第一行函数声明。")
        self.signature_edit.setFixedHeight(70)
        signature_layout.addWidget(self.signature_edit)
        layout.addWidget(signature_group)

        prereq_group = QGroupBox("前置要求 (这个函数体自己的)", panel)
        prereq_layout = QVBoxLayout(prereq_group)
        self.prerequisites_edit = QPlainTextEdit(prereq_group)
        self.prerequisites_edit.setPlaceholderText(
            "该函数运行需要什么, 例如: Python 3.10+ / 需要 pip install aiohttp / 需要 GCC 11"
        )
        prereq_layout.addWidget(self.prerequisites_edit)
        layout.addWidget(prereq_group, 1)

        desc_group = QGroupBox("说明", panel)
        desc_layout = QVBoxLayout(desc_group)
        self.description_edit = QPlainTextEdit(desc_group)
        self.description_edit.setPlaceholderText("这个函数做什么、输入输出、边界条件、复杂度…")
        desc_layout.addWidget(self.description_edit)
        layout.addWidget(desc_group, 1)

        return panel

    def _build_workspace(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("函数代码 (在此粘贴或编写)", panel)
        title.setObjectName("SectionLabel")
        header.addWidget(title)
        header.addStretch(1)
        self.detect_button = QPushButton("重新检测变量", panel)
        self.detect_button.setToolTip("按当前代码重新扫描; 已经填写的含义不会被覆盖")
        self.detect_button.clicked.connect(self.redetect)
        header.addWidget(self.detect_button)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Vertical, panel)
        splitter.setChildrenCollapsible(False)

        self.code_editor = CodeEditor(self, language="python", theme=self._theme)
        self.code_editor.setPlaceholderText("粘贴一个函数…")
        self.code_editor.textChanged.connect(self._on_code_changed)
        splitter.addWidget(self.code_editor)

        symbol_panel = QWidget(splitter)
        symbol_layout = QVBoxLayout(symbol_panel)
        symbol_layout.setContentsMargins(0, 0, 0, 0)
        symbol_layout.setSpacing(4)

        bar = QHBoxLayout()
        self.symbol_label = QLabel("变量含义", symbol_panel)
        self.symbol_label.setObjectName("SectionLabel")
        bar.addWidget(self.symbol_label)
        self.symbol_summary = QLabel("", symbol_panel)
        self.symbol_summary.setObjectName("DimLabel")
        bar.addWidget(self.symbol_summary)
        bar.addStretch(1)

        add_button = QToolButton(symbol_panel)
        add_button.setText("＋ 添加")
        add_button.setAutoRaise(True)
        add_button.setToolTip("手动补一个检测器没识别出来的变量")
        add_button.clicked.connect(self.add_manual_symbol)
        remove_button = QToolButton(symbol_panel)
        remove_button.setText("－ 删除选中")
        remove_button.setAutoRaise(True)
        remove_button.setToolTip("把噪声变量从表里去掉 (例如 for 循环计数器)")
        remove_button.clicked.connect(self.remove_selected_symbol)
        bar.addWidget(add_button)
        bar.addWidget(remove_button)
        symbol_layout.addLayout(bar)

        self.symbol_table = FunctionSymbolTable(symbol_panel, theme=self._theme)
        self.symbol_table.itemChanged.connect(lambda _i: self._refresh_symbol_summary())
        symbol_layout.addWidget(self.symbol_table, 1)

        hint = QLabel(
            "带 * 的列必填: 参数、成员字段、返回值的含义必须写清楚; 局部变量可以留空。",
            symbol_panel,
        )
        hint.setObjectName("DimLabel")
        hint.setWordWrap(True)
        symbol_layout.addWidget(hint)

        splitter.addWidget(symbol_panel)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([360, 300])
        layout.addWidget(splitter, 1)
        return panel

    # ----------------------------------------------------------------------------
    def _load(self) -> None:
        function = self._function or Function(language="python")
        self.name_edit.setText(function.name)
        index = self.language_combo.findData(function.language)
        self.language_combo.setCurrentIndex(max(0, index))
        status_index = self.status_combo.findData(function.status)
        self.status_combo.setCurrentIndex(max(0, status_index))
        self.favorite_check.setChecked(function.favorite)
        self.tags_edit.setText(" ".join(function.tags))
        self.signature_edit.setPlainText(function.signature)
        self.prerequisites_edit.setPlainText(function.prerequisites)
        self.description_edit.setPlainText(function.description)
        self.code_editor.set_language(function.language)
        self.code_editor.setPlainText(function.code)
        self.symbol_table.load(function.symbols)
        self._refresh_symbol_summary()

    def _on_language_changed(self) -> None:
        language = self.language_combo.currentData() or "plaintext"
        self.code_editor.set_language(language)
        if not self._loading:
            self._detect_timer.start()

    def _on_code_changed(self) -> None:
        if not self._loading:
            self._detect_timer.start()

    def _refresh_symbol_summary(self) -> None:
        symbols = self.symbol_table.symbols()
        missing = [s for s in symbols if s.is_blank and s.kind in REQUIRED_SYMBOL_KINDS]
        self.symbol_table.refresh_decoration()
        if missing:
            self.symbol_summary.setText(f"({len(symbols)} 个, {len(missing)} 个必填项待填写)")
            self.symbol_summary.setStyleSheet(f"color:{self._theme.warning};")
        else:
            self.symbol_summary.setText(f"({len(symbols)} 个, 含义已完整)")
            self.symbol_summary.setStyleSheet("")

    # ----------------------------------------------------------------------------
    def _current_function(self) -> Function:
        """把对话框内容组装成一个 :class:`Function` (不含含义表以外的副作用)。"""
        function = (self._function or Function()).clone() if self._function else Function()
        function.name = self.name_edit.text().strip() or "未命名函数"
        function.language = self.language_combo.currentData() or "plaintext"
        function.signature = self.signature_edit.toPlainText().strip()
        function.code = self.code_editor.toPlainText()
        function.description = self.description_edit.toPlainText()
        function.prerequisites = self.prerequisites_edit.toPlainText()
        function.tags = normalize_tags(
            self.tags_edit.text().replace(",", " ").replace("，", " ").split()
        )
        function.status = self.status_combo.currentData() or "planned"
        function.favorite = self.favorite_check.isChecked()
        function.set_symbols(self.symbol_table.symbols())
        return function

    def redetect(self) -> None:
        """重新扫描代码里的变量声明, 保留已填写的含义。"""
        function = self._current_function()
        # 先把表里当前填好的含义带上, 再让检测器合并
        before = len(function.symbols)
        added = detect_function_symbols(function)
        self.symbol_table.load(function.symbols)
        self._refresh_symbol_summary()
        if added:
            self.symbol_label.setText(f"变量含义 — 新识别出 {added} 个")
        elif before:
            self.symbol_label.setText("变量含义 — 没有新变量")
        else:
            self.symbol_label.setText("变量含义")

    def add_manual_symbol(self) -> None:
        row = self.symbol_table.append_symbol(Symbol(name="新变量", kind="local"))
        item = self.symbol_table.item(row, 0)
        if item is not None:
            self.symbol_table.setCurrentItem(item)
            self.symbol_table.editItem(item)
        self._refresh_symbol_summary()

    def remove_selected_symbol(self) -> None:
        rows = sorted({index.row() for index in self.symbol_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.symbol_table.removeRow(row)
        self._refresh_symbol_summary()

    # ----------------------------------------------------------------------------
    def _on_save(self) -> None:
        function = self._current_function()
        if not function.code.strip():
            answer = QMessageBox.question(self, "代码为空", "还没有写任何代码, 确定要保存吗?")
            if answer != QMessageBox.StandardButton.Yes:
                self.code_editor.setFocus()
                return

        missing = function.required_symbols_missing_meaning
        if missing and self.strict_check.isChecked():
            names = "、".join(f"{s.name}({s.kind_label})" for s in missing[:12])
            more = f" 等 {len(missing)} 项" if len(missing) > 12 else ""
            answer = QMessageBox.warning(
                self,
                "还有变量没填含义",
                f"这些变量还没有填写含义:\n\n{names}{more}\n\n"
                "参数 / 字段 / 返回值的含义是必填的。\n"
                "如果某一条只是噪声 (例如循环里的临时变量), 可以选中它点「－ 删除选中」。\n\n"
                "要现在就去填吗?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Ignore,
                QMessageBox.StandardButton.Ok,
            )
            # 定位到第一条空项并聚焦到"含义"列
            target = None
            for row in range(self.symbol_table.rowCount()):
                symbol = self.symbol_table.symbol_at(row)
                if symbol.is_blank and symbol.kind in REQUIRED_SYMBOL_KINDS:
                    target = row
                    break
            if target is not None:
                self.symbol_table.setCurrentCell(target, MEANING_COLUMN)
                self.symbol_table.scrollToItem(self.symbol_table.item(target, 0))
                self.symbol_table.setFocus()
            if answer != QMessageBox.StandardButton.Ignore:
                return
        self.accept()

    # ----------------------------------------------------------------------------
    def result_data(self) -> Dict[str, object]:
        function = self._current_function()
        return {
            "name": function.name,
            "language": function.language,
            "signature": function.signature,
            "code": function.code,
            "description": function.description,
            "prerequisites": function.prerequisites,
            "tags": function.tags,
            "status": function.status,
            "favorite": function.favorite,
            "symbols": function.symbols,
        }


__all__ = ["FunctionEditorDialog", "FunctionSymbolTable", "MEANING_COLUMN"]
