"""函数体编辑对话框: **多语言实现** + 每种语言自己的变量含义表.

核心交互
--------
1. 一个函数体可以有**多种语言的实现** (右上角的语言下拉框切换);
2. 每种实现粘一段该语言的函数代码, 编辑器会**自动检测**变量声明
   (参数 / 局部变量 / 字段 / 全局 / 常量 / 返回值), 填入表格的
   "名称 / 种类 / 类型 / 默认值" 几列;
3. 用户必须为每个变量填写**含义** (参数、字段、返回值属于必填项);
4. 每种实现有**自己的前置要求** —— Python 版要 3.10+、Go 版要 1.21+,
   函数体本身还有一个所有语言共用的前置要求;
5. 保存时若仍有必填项为空, 会列出来并跳到那一条, 不会静默放过。

重新检测时**永远不会覆盖已经填好的含义** —— 见
:meth:`codemethod.core.functions.FunctionImplementation.apply_detected`。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
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
    Function,
    FunctionImplementation,
    Symbol,
)
from ...core.languages import get_language, language_choices, normalize_language
from ...core.models import STATUS_LABELS, STATUS_ORDER, normalize_tags
from ...core.repository import detect_implementation_symbols
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

    def missing_rows(self) -> List[int]:
        return [
            row
            for row in range(self.rowCount())
            if (lambda s: s.is_blank and s.kind in REQUIRED_SYMBOL_KINDS)(self.symbol_at(row))
        ]

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.refresh_decoration()


class ImplementationPane(QWidget):
    """**一种语言**的实现编辑区: 语言 / 签名 / 前置要求 / 代码 / 变量表。"""

    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._impl: Optional[FunctionImplementation] = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)

        self.language_combo = QComboBox(self)
        for lang_id, lang_name in language_choices():
            self.language_combo.addItem(lang_name, lang_id)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        form.addRow("语言 *", self.language_combo)

        self.signature_edit = QLineEdit(self)
        self.signature_edit.setPlaceholderText("可选, 例如 def reverse(s: str) -> str")
        self.signature_edit.textChanged.connect(self._on_edited)
        form.addRow("函数签名", self.signature_edit)

        self.prerequisites_edit = QLineEdit(self)
        self.prerequisites_edit.setPlaceholderText(
            "这一种语言自己的前置要求, 例如 Python 3.10+ / go 1.21+ / 需要 GCC 11"
        )
        self.prerequisites_edit.textChanged.connect(self._on_edited)
        form.addRow("前置要求 (本语言)", self.prerequisites_edit)

        self.notes_edit = QLineEdit(self)
        self.notes_edit.setPlaceholderText("可选: 这一版实现的说明 / 语言特有的取舍")
        self.notes_edit.textChanged.connect(self._on_edited)
        form.addRow("实现说明", self.notes_edit)
        layout.addLayout(form)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.setChildrenCollapsible(False)

        code_panel = QWidget(splitter)
        code_layout = QVBoxLayout(code_panel)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(4)
        header = QHBoxLayout()
        self.code_label = QLabel("函数代码 (在此粘贴或编写)", code_panel)
        self.code_label.setObjectName("SectionLabel")
        header.addWidget(self.code_label)
        header.addStretch(1)
        self.detect_button = QPushButton("重新检测变量", code_panel)
        self.detect_button.setToolTip("按当前代码重新扫描; 已经填写的含义不会被覆盖")
        self.detect_button.clicked.connect(self.redetect)
        header.addWidget(self.detect_button)
        code_layout.addLayout(header)

        self.code_editor = CodeEditor(code_panel, language="python", theme=self._theme)
        self.code_editor.setPlaceholderText("粘贴一个函数…")
        self.code_editor.textChanged.connect(self._on_code_changed)
        code_layout.addWidget(self.code_editor, 1)
        splitter.addWidget(code_panel)

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
            "带 * 的列必填: 参数、成员字段、返回值的含义必须写清楚; 局部变量可以留空。"
            "每个语言实现有自己独立的一张表。",
            symbol_panel,
        )
        hint.setObjectName("DimLabel")
        hint.setWordWrap(True)
        symbol_layout.addWidget(hint)

        splitter.addWidget(symbol_panel)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([340, 300])
        layout.addWidget(splitter, 1)

        self._detect_timer = QTimer(self)
        self._detect_timer.setSingleShot(True)
        self._detect_timer.setInterval(600)
        self._detect_timer.timeout.connect(self.redetect)

    # ----------------------------------------------------------------------------
    @property
    def implementation(self) -> Optional[FunctionImplementation]:
        return self._impl

    def set_implementation(self, impl: Optional[FunctionImplementation]) -> None:
        self.store()
        self._impl = impl
        self._loading = True
        try:
            if impl is None:
                self.language_combo.setCurrentIndex(0)
                self.signature_edit.setText("")
                self.prerequisites_edit.setText("")
                self.notes_edit.setText("")
                self.code_editor.set_language("plaintext")
                self.code_editor.setPlainText("")
                self.symbol_table.load([])
            else:
                index = self.language_combo.findData(impl.language)
                self.language_combo.setCurrentIndex(max(0, index))
                self.signature_edit.setText(impl.signature)
                self.prerequisites_edit.setText(impl.prerequisites)
                self.notes_edit.setText(impl.notes)
                self.code_editor.set_language(impl.language)
                self.code_editor.setPlainText(impl.code)
                self.symbol_table.load(impl.symbols)
            self.symbol_label.setText("变量含义")
        finally:
            self._loading = False
        self._refresh_symbol_summary()
        self.setEnabled(impl is not None)

    def store(self) -> None:
        """把控件里的内容写回当前的实现对象。"""
        if self._impl is None:
            return
        self._impl.language = normalize_language(self.language_combo.currentData() or "plaintext")
        self._impl.signature = self.signature_edit.text().strip()
        self._impl.prerequisites = self.prerequisites_edit.text().strip()
        self._impl.notes = self.notes_edit.text().strip()
        self._impl.code = self.code_editor.toPlainText()
        self._impl.set_symbols(self.symbol_table.symbols())

    # ----------------------------------------------------------------------------
    def _on_language_changed(self) -> None:
        if self._impl is None:
            return
        language = self.language_combo.currentData() or "plaintext"
        self.code_editor.set_language(language)
        self._impl.language = normalize_language(language)
        if not self._loading:
            self._detect_timer.start()
            self.changed.emit()

    def _on_code_changed(self) -> None:
        if not self._loading:
            self._detect_timer.start()
            self.changed.emit()

    def _on_edited(self) -> None:
        if not self._loading:
            self.changed.emit()

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

    def redetect(self) -> int:
        """重新扫描代码里的变量声明, 保留已填写的含义。"""
        if self._impl is None:
            return 0
        before = len(self._impl.symbols)
        self.store()
        added = detect_implementation_symbols(self._impl)
        self._loading = True
        try:
            self.symbol_table.load(self._impl.symbols)
        finally:
            self._loading = False
        self._refresh_symbol_summary()
        if added:
            self.symbol_label.setText(f"变量含义 — 新识别出 {added} 个")
        elif before:
            self.symbol_label.setText("变量含义 — 没有新变量")
        else:
            self.symbol_label.setText("变量含义")
        self.changed.emit()
        return added

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

    def missing_symbols(self) -> List[Symbol]:
        return [
            self.symbol_table.symbol_at(row) for row in self.symbol_table.missing_rows()
        ]

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.code_editor.set_theme(theme)
        self.symbol_table.set_theme(theme)


class FunctionEditorDialog(ThemedDialog):
    """新建 / 编辑一个函数体 (可以带多个语言实现)。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        function: Optional[Function] = None,
        existing_tags: Optional[List[str]] = None,
        theme: Theme = DEFAULT_THEME,
        default_language: str = "python",
        initial_implementation: Optional[FunctionImplementation] = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._function = function
        self._is_new = function is None
        self._loading = True
        self._current_index = -1

        self.setWindowTitle("新建函数体" if self._is_new else "编辑函数体")
        self.setMinimumSize(1040, 720)
        self.resize(1240, 840)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_form(existing_tags or []))
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes([400, 760])
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox(self)
        self.save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(self.reject)
        buttons.accepted.connect(self._on_save)
        root.addWidget(buttons)

        self._load(default_language=default_language, extra=initial_implementation)
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

        self.tags_edit = QLineEdit(basics)
        self.tags_edit.setPlaceholderText("用逗号或空格分隔")
        completer = QCompleter(existing_tags, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.tags_edit.setCompleter(completer)
        form.addRow("标签", self.tags_edit)
        layout.addWidget(basics)

        prereq_group = QGroupBox("通用前置要求 (所有语言共用)", panel)
        prereq_layout = QVBoxLayout(prereq_group)
        self.prerequisites_edit = QPlainTextEdit(prereq_group)
        self.prerequisites_edit.setPlaceholderText(
            "与语言无关的共同要求, 例如: 需要联网 / 需要 8GB 内存。\n"
            "某种语言专有的要求请写在右栏「前置要求 (本语言)」里。"
        )
        prereq_layout.addWidget(self.prerequisites_edit)
        layout.addWidget(prereq_group, 1)

        desc_group = QGroupBox("函数说明", panel)
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

        bar = QHBoxLayout()
        title = QLabel("语言实现", panel)
        title.setObjectName("SectionLabel")
        bar.addWidget(title)
        self.impl_combo = QComboBox(panel)
        self.impl_combo.setMinimumWidth(220)
        self.impl_combo.currentIndexChanged.connect(self._on_impl_changed)
        bar.addWidget(self.impl_combo)
        self.impl_summary = QLabel("", panel)
        self.impl_summary.setObjectName("DimLabel")
        bar.addWidget(self.impl_summary)
        bar.addStretch(1)
        self.add_impl_button = QPushButton("＋ 添加语言", panel)
        self.add_impl_button.setToolTip("同一个函数再支持一种语言的实现")
        self.add_impl_button.clicked.connect(self.add_implementation)
        self.remove_impl_button = QPushButton("－ 删除该语言", panel)
        self.remove_impl_button.setToolTip("只删除这一种语言的实现, 其他语言不受影响")
        self.remove_impl_button.clicked.connect(self.remove_implementation)
        bar.addWidget(self.add_impl_button)
        bar.addWidget(self.remove_impl_button)
        layout.addLayout(bar)

        self.pane = ImplementationPane(panel, theme=self._theme)
        self.pane.changed.connect(self._refresh_impl_bar)
        layout.addWidget(self.pane, 1)
        return panel

    # ----------------------------------------------------------------------------
    @property
    def _implementations(self) -> List[FunctionImplementation]:
        return self._impls

    def _load(
        self, *, default_language: str, extra: Optional[FunctionImplementation] = None
    ) -> None:
        source = self._function.clone() if self._function else Function()
        self.name_edit.setText(source.name)
        status_index = self.status_combo.findData(source.status)
        self.status_combo.setCurrentIndex(max(0, status_index))
        self.favorite_check.setChecked(source.favorite)
        self.tags_edit.setText(" ".join(source.tags))
        self.prerequisites_edit.setPlainText(source.prerequisites)
        self.description_edit.setPlainText(source.description)

        self._impls: List[FunctionImplementation] = list(source.implementations)
        if not self._impls:
            self._impls.append(
                FunctionImplementation(language=normalize_language(default_language))
            )
        if extra is not None:
            self._impls.append(extra)

        self._rebuild_combo(select=len(self._impls) - 1 if extra is not None else 0)

    def _rebuild_combo(self, *, select: int = 0) -> None:
        self.impl_combo.blockSignals(True)
        self.impl_combo.clear()
        for impl in self._impls:
            self.impl_combo.addItem(self._impl_label(impl), impl.id)
        self.impl_combo.blockSignals(False)
        index = max(0, min(select, len(self._impls) - 1))
        self.impl_combo.setCurrentIndex(index)
        self._current_index = index
        self.pane.set_implementation(self._impls[index])
        self._refresh_impl_bar()

    @staticmethod
    def _impl_label(impl: FunctionImplementation) -> str:
        return f"{get_language(impl.language).name} · {impl.line_count} 行"

    def _refresh_impl_bar(self) -> None:
        for index, impl in enumerate(self._impls):
            if index < self.impl_combo.count():
                self.impl_combo.setItemText(index, self._impl_label(impl))
        count = len(self._impls)
        self.remove_impl_button.setEnabled(count > 1)
        languages = []
        for impl in self._impls:
            name = get_language(impl.language).name
            if name not in languages:
                languages.append(name)
        self.impl_summary.setText(f"({count} 种实现: {'、'.join(languages)})")

    def _on_impl_changed(self, index: int) -> None:
        if self._loading or index < 0 or index == self._current_index:
            return
        self.pane.store()
        self._current_index = index
        self.pane.set_implementation(self._impls[index])
        self._refresh_impl_bar()

    def add_implementation(self) -> None:
        self.pane.store()
        used = {impl.language for impl in self._impls}
        language = next(
            (lang for lang, _name in language_choices() if lang not in used), "plaintext"
        )
        impl = FunctionImplementation(language=language)
        self._impls.append(impl)
        self._rebuild_combo(select=len(self._impls) - 1)
        self.pane.code_editor.setFocus()

    def remove_implementation(self) -> None:
        if len(self._impls) <= 1:
            QMessageBox.information(self, "无法删除", "至少要保留一种语言的实现。")
            return
        index = self.impl_combo.currentIndex()
        if index < 0:
            return
        impl = self._impls[index]
        answer = QMessageBox.question(
            self,
            "删除语言实现",
            f"确定删除 {get_language(impl.language).name} 这一版实现吗?\n\n"
            "保存后仍然可以从历史里把它恢复回来。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._impls.pop(index)
        self._rebuild_combo(select=min(index, len(self._impls) - 1))

    # ----------------------------------------------------------------------------
    def _collect(self) -> Function:
        """把对话框内容组装成一个 :class:`Function`。"""
        self.pane.store()
        function = Function()
        function.name = self.name_edit.text().strip() or "未命名函数"
        function.description = self.description_edit.toPlainText()
        function.prerequisites = self.prerequisites_edit.toPlainText()
        function.status = self.status_combo.currentData() or "planned"
        function.favorite = self.favorite_check.isChecked()
        function.tags = normalize_tags(
            self.tags_edit.text().replace(",", " ").replace("，", " ").split()
        )
        function.implementations = list(self._impls)
        if self._function is not None:
            function.id = self._function.id
        return function

    def redetect(self) -> None:
        self.pane.redetect()

    # ----------------------------------------------------------------------------
    def _on_save(self) -> None:
        function = self._collect()
        empty = [impl for impl in function.active_implementations if not impl.code.strip()]
        if empty:
            answer = QMessageBox.question(
                self,
                "有语言实现是空的",
                "这些语言实现还没有写任何代码:\n\n"
                + "、".join(get_language(impl.language).name for impl in empty)
                + "\n\n确定要保存吗?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._focus_implementation(empty[0])
                self.pane.code_editor.setFocus()
                return

        missing = self._missing_required()
        if missing and self.strict_check.isChecked():
            lines = "、".join(
                f"{symbol.name}({get_language(impl.language).name})" for impl, symbol in missing[:12]
            )
            more = f" 等 {len(missing)} 项" if len(missing) > 12 else ""
            answer = QMessageBox.warning(
                self,
                "还有变量没填含义",
                f"这些变量还没有填写含义:\n\n{lines}{more}\n\n"
                "参数 / 字段 / 返回值的含义是必填的。\n"
                "如果某一条只是噪声 (例如循环里的临时变量), 可以选中它点「－ 删除选中」。\n\n"
                "要现在就去填吗?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Ignore,
                QMessageBox.StandardButton.Ok,
            )
            target_impl, target_symbol = missing[0]
            self._focus_implementation(target_impl)
            for row in range(self.pane.symbol_table.rowCount()):
                if self.pane.symbol_table.symbol_at(row).name == target_symbol.name:
                    self.pane.symbol_table.setCurrentCell(row, MEANING_COLUMN)
                    self.pane.symbol_table.scrollToItem(self.pane.symbol_table.item(row, 0))
                    break
            self.pane.symbol_table.setFocus()
            if answer != QMessageBox.StandardButton.Ignore:
                return
        self.accept()

    def _missing_required(self) -> List[tuple]:
        """返回 ``[(实现, 变量), …]``, 按当前顺序排列。"""
        out: List[tuple] = []
        for index, impl in enumerate(self._impls):
            if index == self._current_index:
                symbols = self.pane.missing_symbols()
            else:
                symbols = impl.required_symbols_missing_meaning
            out.extend((impl, symbol) for symbol in symbols)
        return out

    def _focus_implementation(self, impl: FunctionImplementation) -> None:
        index = self.impl_combo.findData(impl.id)
        if index >= 0:
            self._on_impl_changed(index)

    # ----------------------------------------------------------------------------
    def result_data(self) -> Dict[str, object]:
        function = self._collect()
        return {
            "name": function.name,
            "description": function.description,
            "prerequisites": function.prerequisites,
            "tags": function.tags,
            "status": function.status,
            "favorite": function.favorite,
            "implementations": function.implementations,
        }


__all__ = [
    "FunctionEditorDialog",
    "FunctionSymbolTable",
    "ImplementationPane",
    "MEANING_COLUMN",
]
