"""条目编辑对话框: 描述 / 前置要求 / 标签 / 多语言实现.

这是数据录入的主界面。左侧是条目元数据, 右侧是"实现"标签页 —— 每个标签页
对应一种语言的实现, 各自带独立的语法高亮编辑器。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.languages import default_filename, detect_language_from_filename, get_language, language_choices
from ...core.models import (
    STATUS_LABELS,
    STATUS_ORDER,
    Entry,
    Implementation,
    new_id,
    normalize_tags,
)
from ..editor import CodeEditor
from ..theme import DEFAULT_THEME, Theme


class ImplementationEditor(QWidget):
    """单个语言实现的编辑页。"""

    changed = Signal()
    request_close = Signal(object)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        implementation: Optional[Implementation] = None,
        theme: Theme = DEFAULT_THEME,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._impl = implementation or Implementation(language="python")
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- 元信息行 ----
        meta = QHBoxLayout()
        meta.setSpacing(6)

        meta.addWidget(QLabel("语言", self))
        self.language_combo = QComboBox(self)
        for lang_id, lang_name in language_choices():
            self.language_combo.addItem(lang_name, lang_id)
        index = self.language_combo.findData(self._impl.language)
        self.language_combo.setCurrentIndex(max(0, index))
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        meta.addWidget(self.language_combo)

        meta.addWidget(QLabel("标题", self))
        self.title_edit = QLineEdit(self)
        self.title_edit.setPlaceholderText("可选, 例如「基于 asyncio 的实现」")
        self.title_edit.setText(self._impl.title)
        self.title_edit.textChanged.connect(self._on_edited)
        meta.addWidget(self.title_edit, 1)

        meta.addWidget(QLabel("文件名", self))
        self.filename_edit = QLineEdit(self)
        self.filename_edit.setText(self._impl.filename)
        self.filename_edit.textChanged.connect(self._on_edited)
        meta.addWidget(self.filename_edit, 1)

        meta.addWidget(QLabel("备注", self))
        self.notes_edit = QLineEdit(self)
        self.notes_edit.setPlaceholderText("可选")
        self.notes_edit.setText(self._impl.notes)
        self.notes_edit.textChanged.connect(self._on_edited)
        meta.addWidget(self.notes_edit, 1)

        layout.addLayout(meta)

        # ---- 代码 ----
        self.editor = CodeEditor(self, language=self._impl.language, theme=theme)
        self.editor.setPlaceholderText("在此粘贴 / 编写源代码…")
        self.editor.setPlainText(self._impl.code)
        self.editor.textChanged.connect(self._on_edited)
        layout.addWidget(self.editor, 1)

        footer = QHBoxLayout()
        self.stats_label = QLabel("", self)
        self.stats_label.setObjectName("DimLabel")
        footer.addWidget(self.stats_label)
        footer.addStretch(1)
        self.close_button = QPushButton("关闭此实现", self)
        self.close_button.setProperty("flat", True)
        self.close_button.clicked.connect(lambda: self.request_close.emit(self))
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

        self.editor.textChanged.connect(self._update_stats)
        self._update_stats()
        self._loading = False

    # ---- 事件 ----
    def _on_edited(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _on_language_changed(self) -> None:
        language = self.language_combo.currentData() or "plaintext"
        self.editor.set_language(language)
        # 文件名与语言不匹配时自动纠正
        current = self.filename_edit.text().strip()
        if not current:
            self.filename_edit.setText(default_filename(language))
        else:
            detected = detect_language_from_filename(current)
            if detected == "plaintext" and current.count(".") <= 1:
                self.filename_edit.setText(default_filename(language))
        self._on_edited()

    def _update_stats(self) -> None:
        text = self.editor.toPlainText()
        lines = text.count("\n") + 1 if text else 0
        self.stats_label.setText(f"{lines} 行 · {len(text)} 字符")

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.editor.set_theme(theme)

    # ---- 数据 ----
    def load(self, implementation: Implementation) -> None:
        self._loading = True
        self._impl = implementation
        index = self.language_combo.findData(implementation.language)
        self.language_combo.setCurrentIndex(max(0, index))
        self.title_edit.setText(implementation.title)
        self.filename_edit.setText(implementation.filename)
        self.notes_edit.setText(implementation.notes)
        self.editor.set_language(implementation.language)
        self.editor.setPlainText(implementation.code)
        self._loading = False
        self._update_stats()

    def collect(self) -> Implementation:
        impl = self._impl
        impl.language = self.language_combo.currentData() or "plaintext"
        impl.title = self.title_edit.text().strip()
        impl.filename = self.filename_edit.text().strip() or default_filename(impl.language)
        impl.notes = self.notes_edit.text().strip()
        code = self.editor.toPlainText()
        if code != impl.code:
            impl.code = code
            impl.touch()
        return impl

    def language(self) -> str:
        return self.language_combo.currentData() or "plaintext"

    def code(self) -> str:
        return self.editor.toPlainText()

    def is_empty(self) -> bool:
        return not self.editor.toPlainText().strip()


class EntryEditorDialog(QDialog):
    """新建 / 编辑一个条目 (含全部语言实现)。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        entry: Optional[Entry] = None,
        existing_tags: Optional[List[str]] = None,
        tag_colors: Optional[Dict[str, str]] = None,
        theme: Theme = DEFAULT_THEME,
        default_language: str = "python",
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._entry = entry
        self._existing_tags = list(existing_tags or [])
        self._tag_colors = {k.casefold(): v for k, v in (tag_colors or {}).items()}
        self._editors: List[ImplementationEditor] = []

        self.setWindowTitle("编辑条目" if entry else "新建条目")
        self.setMinimumSize(980, 680)
        self.resize(1120, 760)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_meta_panel())
        splitter.addWidget(self._build_impl_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes([400, 700])
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox(self)
        self.save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setToolTip("保存并写入一条新的修订记录 (Ctrl+S)")
        cancel = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setToolTip("放弃全部改动 (Esc)")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        save_action = QAction(self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._on_save)
        self.addAction(save_action)

        self._load_entry()
        self._ensure_at_least_one()

    # ----------------------------------------------------------------------------
    # 左侧: 条目元数据
    # ----------------------------------------------------------------------------
    def _build_meta_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(8)

        basics = QGroupBox("基本信息", panel)
        form = QFormLayout(basics)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(6)

        self.title_edit = QLineEdit(basics)
        self.title_edit.setPlaceholderText("这个实现要完成什么功能?")
        form.addRow("标题 *", self.title_edit)

        self.status_combo = QComboBox(basics)
        for status in STATUS_ORDER:
            self.status_combo.addItem(STATUS_LABELS[status], status)
        self.status_combo.setToolTip("多标签规划中的阶段")
        form.addRow("规划状态", self.status_combo)

        self.favorite_check = QCheckBox("加入收藏", basics)
        form.addRow("", self.favorite_check)

        layout.addWidget(basics)

        tags_group = QGroupBox("标签 (多标签规划)", panel)
        tags_layout = QVBoxLayout(tags_group)
        tags_layout.setSpacing(5)
        self.tags_edit = QLineEdit(tags_group)
        self.tags_edit.setPlaceholderText("用逗号或空格分隔, 例如: network tcp 高性能")
        completer = QCompleter(self._existing_tags, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.tags_edit.setCompleter(completer)
        self.tags_edit.textChanged.connect(self._update_tag_preview)
        tags_layout.addWidget(self.tags_edit)

        tag_actions = QHBoxLayout()
        tag_actions.setSpacing(5)
        self.pick_tag_button = QToolButton(tags_group)
        self.pick_tag_button.setText("选择已有标签")
        self.pick_tag_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.pick_tag_button.setMenu(self._build_tag_menu())
        tag_actions.addWidget(self.pick_tag_button)
        tag_actions.addStretch(1)
        tags_layout.addLayout(tag_actions)

        self.tag_preview = QLabel("", tags_group)
        self.tag_preview.setObjectName("DimLabel")
        self.tag_preview.setWordWrap(True)
        tags_layout.addWidget(self.tag_preview)
        layout.addWidget(tags_group)

        self.description_edit = QPlainTextEdit(panel)
        self.description_edit.setPlaceholderText(
            "描述这个功能: 用途、输入输出、关键思路、复杂度、注意事项…"
        )
        layout.addWidget(self._labeled("描述", self.description_edit), 1)

        self.prerequisites_edit = QPlainTextEdit(panel)
        self.prerequisites_edit.setPlaceholderText(
            "前置要求: 依赖库 / 运行环境 / 版本 / 硬件 / 前置知识…"
        )
        layout.addWidget(self._labeled("前置要求", self.prerequisites_edit), 1)

        return panel

    def _labeled(self, title: str, widget: QWidget) -> QWidget:
        box = QGroupBox(title, self)
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(6, 6, 6, 6)
        box_layout.addWidget(widget)
        return box

    def _build_tag_menu(self) -> QMenu:
        menu = QMenu(self)
        if not self._existing_tags:
            action = QAction("（暂无已有标签）", menu)
            action.setEnabled(False)
            menu.addAction(action)
            return menu
        for tag in self._existing_tags[:60]:
            action = QAction(tag, menu)
            action.triggered.connect(lambda _c=False, t=tag: self._append_tag(t))
            menu.addAction(action)
        return menu

    def _append_tag(self, tag: str) -> None:
        current = self.parsed_tags()
        if tag.casefold() in {t.casefold() for t in current}:
            return
        current.append(tag)
        self.tags_edit.setText(" ".join(current))

    def _update_tag_preview(self) -> None:
        tags = self.parsed_tags()
        if not tags:
            self.tag_preview.setText("未添加标签")
            return
        self.tag_preview.setText(
            f"共 {len(tags)} 个标签: " + "  ".join("#" + t for t in tags)
        )

    # ----------------------------------------------------------------------------
    # 右侧: 实现
    # ----------------------------------------------------------------------------
    def _build_impl_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("语言实现 (同一描述的多种实现)", panel)
        title.setObjectName("SectionLabel")
        header.addWidget(title)
        header.addStretch(1)

        self.add_impl_button = QPushButton("+ 新增实现", panel)
        self.add_impl_button.setToolTip("为这个条目再添加一种语言的实现")
        self.add_impl_button.clicked.connect(lambda: self.add_implementation())
        header.addWidget(self.add_impl_button)

        self.duplicate_impl_button = QPushButton("复制当前", panel)
        self.duplicate_impl_button.setProperty("flat", True)
        self.duplicate_impl_button.clicked.connect(self.duplicate_current)
        header.addWidget(self.duplicate_impl_button)

        self.remove_impl_button = QPushButton("删除当前", panel)
        self.remove_impl_button.setProperty("flat", True)
        self.remove_impl_button.clicked.connect(self.remove_current)
        header.addWidget(self.remove_impl_button)
        layout.addLayout(header)

        self.tabs = QTabWidget(panel)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        self.hint_label = QLabel(
            "提示: Ctrl+S 保存; 编辑器支持语法高亮、自动缩进、括号匹配、Ctrl+滚轮缩放。",
            panel,
        )
        self.hint_label.setObjectName("DimLabel")
        layout.addWidget(self.hint_label)
        return panel

    # ----------------------------------------------------------------------------
    # 载入 / 收集
    # ----------------------------------------------------------------------------
    def _load_entry(self) -> None:
        if self._entry is None:
            self._update_tag_preview()
            return
        self.title_edit.setText(self._entry.title)
        index = self.status_combo.findData(self._entry.status)
        self.status_combo.setCurrentIndex(max(0, index))
        self.favorite_check.setChecked(self._entry.favorite)
        self.tags_edit.setText(" ".join(self._entry.tags))
        self.description_edit.setPlainText(self._entry.description)
        self.prerequisites_edit.setPlainText(self._entry.prerequisites)
        for impl in self._entry.implementations:
            if impl.deleted:
                continue
            self._append_editor(impl)
        self._update_tag_preview()

    def _append_editor(self, implementation: Implementation) -> ImplementationEditor:
        editor = ImplementationEditor(self, implementation=implementation, theme=self._theme)
        editor.changed.connect(self._on_impl_changed)
        editor.request_close.connect(self._close_editor)
        self._editors.append(editor)
        self.tabs.addTab(editor, self._tab_title(editor))
        return editor

    def _ensure_at_least_one(self) -> None:
        if not self._editors:
            self.add_implementation()

    def _tab_title(self, editor: ImplementationEditor) -> str:
        language = get_language(editor.language()).name
        title = editor.title_edit.text().strip()
        return f"{title} ({language})" if title else language

    def _on_impl_changed(self) -> None:
        current = self.tabs.currentWidget()
        if isinstance(current, ImplementationEditor):
            index = self.tabs.indexOf(current)
            if index >= 0:
                self.tabs.setTabText(index, self._tab_title(current))

    def _on_tab_changed(self, _index: int) -> None:
        current = self.tabs.currentWidget()
        self.duplicate_impl_button.setEnabled(isinstance(current, ImplementationEditor))
        self.remove_impl_button.setEnabled(
            isinstance(current, ImplementationEditor) and len(self._editors) > 1
        )

    def parsed_tags(self) -> List[str]:
        raw = self.tags_edit.text().replace(",", " ").replace("，", " ").replace(";", " ")
        return normalize_tags(raw.split())

    # ----------------------------------------------------------------------------
    # 实现增删
    # ----------------------------------------------------------------------------
    def add_implementation(self, implementation: Optional[Implementation] = None) -> ImplementationEditor:
        if implementation is None:
            used = {editor.language() for editor in self._editors}
            for lang_id, _name in language_choices():
                if lang_id not in used:
                    implementation = Implementation(language=lang_id)
                    break
            else:
                implementation = Implementation(language="python")
        editor = self._append_editor(implementation)
        self.tabs.setCurrentWidget(editor)
        self._on_tab_changed(self.tabs.currentIndex())
        return editor

    def duplicate_current(self) -> None:
        current = self.tabs.currentWidget()
        if not isinstance(current, ImplementationEditor):
            return
        clone = current.collect().clone(new_identity=True)
        clone.title = (clone.title + " 副本").strip()
        editor = self._append_editor(clone)
        self.tabs.setCurrentWidget(editor)

    def remove_current(self) -> None:
        current = self.tabs.currentWidget()
        if not isinstance(current, ImplementationEditor):
            return
        if len(self._editors) <= 1:
            QMessageBox.information(self, "无法删除", "至少需要保留一个实现。")
            return
        self._close_editor(current)

    def _close_editor(self, editor: ImplementationEditor) -> None:
        index = self.tabs.indexOf(editor)
        if index < 0:
            return
        self.tabs.removeTab(index)
        self._editors.remove(editor)
        editor.setParent(None)
        editor.deleteLater()
        self._on_tab_changed(self.tabs.currentIndex())

    # ----------------------------------------------------------------------------
    # 保存
    # ----------------------------------------------------------------------------
    def _on_save(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "缺少标题", "请填写条目标题。")
            self.title_edit.setFocus()
            return
        if not self._editors:
            QMessageBox.warning(self, "缺少实现", "请至少添加一个语言实现。")
            return
        self.accept()

    def result_data(self) -> Dict[str, object]:
        """收集对话框内容, 供主窗口写入仓储。"""
        implementations = [editor.collect() for editor in self._editors]
        return {
            "title": self.title_edit.text().strip(),
            "description": self.description_edit.toPlainText(),
            "prerequisites": self.prerequisites_edit.toPlainText(),
            "tags": self.parsed_tags(),
            "status": self.status_combo.currentData() or "idea",
            "favorite": self.favorite_check.isChecked(),
            "implementations": implementations,
        }


__all__ = ["EntryEditorDialog", "ImplementationEditor"]
