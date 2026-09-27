"""空间编辑对话框: 在库内构建一个完整的项目结构.

左侧是文件树, 右侧是当前文件的代码编辑器。所有文件内容都保存在同一个 ``.cmdb`` 里,
因此这个对话框其实是一个"库内 IDE": 建目录、加文件、改内容、重命名、删除。
"""

from __future__ import annotations

import posixpath
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
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
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.languages import detect_language_from_filename, get_language
from ...core.models import STATUS_LABELS, STATUS_ORDER, normalize_tags
from ...core.spaces import (
    MAX_FILE_BYTES,
    ProjectFile,
    Space,
    build_tree,
    is_binary_path,
    normalize_project_path,
)
from ..editor import CodeEditor
from ..native import ThemedDialog
from ..theme import DEFAULT_THEME, Theme
from ..widgets.language_bar import LanguageBar, LanguageLegend


class SpaceEditorDialog(ThemedDialog):
    """新建 / 编辑一个独立空间 (含完整项目结构)。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        space: Optional[Space] = None,
        existing_tags: Optional[List[str]] = None,
        theme: Theme = DEFAULT_THEME,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._space = space
        self._is_new = space is None
        self._loading = True
        self._dirty_paths: set = set()      # 内容被改动过的文件路径 (提交时据此写回)

        self.setWindowTitle("新建空间" if self._is_new else "编辑空间")
        self.setMinimumSize(1060, 720)
        self.resize(1280, 860)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_form(existing_tags or []))
        splitter.addWidget(self._build_project_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes([400, 760])
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox(self)
        self.save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(self.reject)
        buttons.accepted.connect(self._on_save)
        root.addWidget(buttons)

        save_action = QAction(self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._on_save)
        self.addAction(save_action)

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
        self.name_edit.setPlaceholderText("项目名, 例如 tcp-server")
        form.addRow("空间名称 *", self.name_edit)

        self.status_combo = QComboBox(basics)
        for status in STATUS_ORDER:
            self.status_combo.addItem(STATUS_LABELS[status], status)
        form.addRow("规划状态", self.status_combo)

        self.entry_point_edit = QLineEdit(basics)
        self.entry_point_edit.setPlaceholderText("可选, 例如 src/main.py")
        form.addRow("入口文件", self.entry_point_edit)

        self.favorite_check = QCheckBox("加入收藏", basics)
        form.addRow("", self.favorite_check)
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

        ratio_group = QGroupBox("语言占比", panel)
        ratio_layout = QVBoxLayout(ratio_group)
        self.language_bar = LanguageBar(ratio_group, theme=self._theme)
        ratio_layout.addWidget(self.language_bar)
        self.language_legend = LanguageLegend(ratio_group, theme=self._theme, columns=2)
        ratio_layout.addWidget(self.language_legend)
        layout.addWidget(ratio_group)

        prereq_group = QGroupBox("前置要求", panel)
        prereq_layout = QVBoxLayout(prereq_group)
        self.prerequisites_edit = QPlainTextEdit(prereq_group)
        self.prerequisites_edit.setPlaceholderText(
            "整个项目需要什么, 例如: Python 3.10+ / 需要 docker / 需要 9000 端口"
        )
        prereq_layout.addWidget(self.prerequisites_edit)
        layout.addWidget(prereq_group, 1)

        desc_group = QGroupBox("描述", panel)
        desc_layout = QVBoxLayout(desc_group)
        self.description_edit = QPlainTextEdit(desc_group)
        self.description_edit.setPlaceholderText(
            "这个项目是什么、目录怎么组织、怎么运行…\n"
            "建议在项目根目录放一个 README.md —— 它会被自动索引并可全局检索。"
        )
        desc_layout.addWidget(self.description_edit)
        layout.addWidget(desc_group, 1)
        return panel

    def _build_project_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("项目结构 (文件内容全部存进代码库)", panel)
        title.setObjectName("SectionLabel")
        header.addWidget(title)
        header.addStretch(1)

        for text, tip, slot in (
            ("＋ 文件", "新建一个文件", self.add_file),
            ("＋ 目录", "新建一个目录", self.add_directory),
            ("重命名", "重命名选中的文件", self.rename_selected),
            ("删除", "删除选中的文件或目录", self.delete_selected),
        ):
            button = QPushButton(text, panel)
            button.setProperty("flat", True)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            header.addWidget(button)
        layout.addLayout(header)

        h_splitter = QSplitter(Qt.Orientation.Horizontal, panel)
        h_splitter.setChildrenCollapsible(False)

        self.tree = QTreeWidget(h_splitter)
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)
        self.tree.currentItemChanged.connect(lambda *_: self._on_tree_selection())
        h_splitter.addWidget(self.tree)

        editor_panel = QWidget(h_splitter)
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(4)
        self.file_label = QLabel("（未选择文件）", editor_panel)
        self.file_label.setObjectName("MutedLabel")
        editor_layout.addWidget(self.file_label)
        self.code_editor = CodeEditor(editor_panel, language="plaintext", theme=self._theme)
        self.code_editor.setPlaceholderText("在左侧选中一个文件开始编辑…")
        self.code_editor.textChanged.connect(self._on_code_changed)
        editor_layout.addWidget(self.code_editor, 1)
        self.size_label = QLabel("", editor_panel)
        self.size_label.setObjectName("DimLabel")
        editor_layout.addWidget(self.size_label)

        h_splitter.addWidget(editor_panel)
        h_splitter.setStretchFactor(0, 3)
        h_splitter.setStretchFactor(1, 5)
        h_splitter.setSizes([280, 460])
        layout.addWidget(h_splitter, 1)
        return panel

    # ----------------------------------------------------------------------------
    # 数据装载 / 收集
    # ----------------------------------------------------------------------------
    def _files(self) -> List[ProjectFile]:
        """当前累积的文件列表 (从对话框持有的工作副本)。"""
        return list(self._work_files.values())

    def _load(self) -> None:
        space = self._space or Space(name="")
        self._work_files: Dict[str, ProjectFile] = {
            file.path: file.clone() for file in space.files
        }
        self.name_edit.setText(space.name)
        index = self.status_combo.findData(space.status)
        self.status_combo.setCurrentIndex(max(0, index))
        self.entry_point_edit.setText(space.entry_point)
        self.favorite_check.setChecked(space.favorite)
        self.tags_edit.setText(" ".join(space.tags))
        self.prerequisites_edit.setPlainText(space.prerequisites)
        self.description_edit.setPlainText(space.description)
        self._refresh_tree()
        self._refresh_ratio()

    def _refresh_tree(self, select: str = "") -> None:
        self.tree.clear()
        root = build_tree(self._files())
        root_item = QTreeWidgetItem(self.tree)
        root_item.setText(0, f"（项目根目录）  {len(self._work_files)} 个文件")
        root_item.setData(0, Qt.ItemDataRole.UserRole, ("dir", ""))
        self._fill(root_item, root)
        root_item.setExpanded(True)
        if select:
            self._select_path(select)

    def _fill(self, parent: QTreeWidgetItem, node) -> None:
        for child in node.sorted_children():
            item = QTreeWidgetItem(parent)
            if child.is_dir:
                item.setText(0, f"{child.name}/")
                item.setData(0, Qt.ItemDataRole.UserRole, ("dir", child.path))
                self._fill(item, child)
                item.setExpanded(True)
            else:
                file = self._work_files.get(child.path)
                marker = " ●" if child.path in self._dirty_paths else ""
                language = get_language(file.language if file else "plaintext").name
                item.setText(0, f"{child.name}   {language} · {child.size} B{marker}")
                item.setData(0, Qt.ItemDataRole.UserRole, ("file", child.path))

    def _select_path(self, path: str) -> None:
        iterator = self.tree.findItems("", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive)
        stack: List[QTreeWidgetItem] = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == "file" and data[1] == path:
                self.tree.setCurrentItem(item)
                return
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _current(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _on_tree_selection(self) -> None:
        data = self._current()
        self._loading = True
        try:
            if not data or data[0] != "file":
                self.file_label.setText("（选择左侧的一个文件）")
                self.code_editor.setPlainText("")
                self.code_editor.set_language("plaintext")
                self.size_label.setText("")
                return
            file = self._work_files.get(data[1])
            if file is None:
                return
            self.file_label.setText(f"{file.path}  ·  {get_language(file.language).name}")
            if file.binary:
                self.code_editor.set_language("plaintext")
                self.code_editor.setPlainText(
                    f"（二进制文件, {file.size} 字节 —— CodeLibrary 只记录大小, 不保存内容）"
                )
                self.code_editor.setReadOnly(True)
            else:
                self.code_editor.setReadOnly(False)
                self.code_editor.set_language(file.language)
                self.code_editor.setPlainText(file.content)
            self._update_size_label(file)
        finally:
            self._loading = False

    def _update_size_label(self, file: ProjectFile) -> None:
        size = file.computed_size
        limit = f" / 上限 {MAX_FILE_BYTES // 1024} KB"
        self.size_label.setText(
            f"{file.line_count} 行 · {size} 字节{limit}"
            + ("   ⚠ 超过上限, 保存会被拒绝" if size > MAX_FILE_BYTES else "")
        )

    def _on_code_changed(self) -> None:
        if self._loading:
            return
        data = self._current()
        if not data or data[0] != "file":
            return
        file = self._work_files.get(data[1])
        if file is None or file.binary:
            return
        file.content = self.code_editor.toPlainText()
        file.size = file.computed_size
        self._dirty_paths.add(file.path)
        self._update_size_label(file)
        self._refresh_ratio()
        self._touch_tree_item()

    def _touch_tree_item(self) -> None:
        item = self.tree.currentItem()
        data = self._current()
        if item is None or not data or data[0] != "file":
            return
        file = self._work_files.get(data[1])
        if file is None:
            return
        marker = " ●" if file.path in self._dirty_paths else ""
        item.setText(
            0,
            f"{file.name}   {get_language(file.language).name} · {file.computed_size} B{marker}",
        )

    def _refresh_ratio(self) -> None:
        probe = Space(files=self._files())
        shares = probe.language_shares()
        self.language_bar.set_shares(shares)
        self.language_legend.set_shares(shares)

    # ----------------------------------------------------------------------------
    # 文件操作
    # ----------------------------------------------------------------------------
    def _selected_directory(self) -> str:
        data = self._current()
        if not data:
            return ""
        if data[0] == "dir":
            return data[1]
        return posixpath.dirname(data[1])

    def _ask_path(self, title: str, default: str) -> Optional[str]:
        text, ok = QInputDialog.getText(self, title, "相对路径 (用 / 分隔):", text=default)
        if not ok:
            return None
        normalized = normalize_project_path(text)
        if not normalized:
            QMessageBox.warning(self, "路径无效", "路径不能为空。")
            return None
        return normalized

    def add_file(self, directory: str = "") -> None:
        base = directory or self._selected_directory()
        default = f"{base}/new_file.py" if base else "new_file.py"
        path = self._ask_path("新建文件", default)
        if path is None:
            return
        if path in self._work_files:
            QMessageBox.warning(self, "已存在", f"文件已存在: {path}")
            return
        binary = is_binary_path(path)
        file = ProjectFile(
            path=path,
            content="" if not binary else "",
            binary=binary,
            size=0,
        )
        self._work_files[path] = file
        self._dirty_paths.add(path)
        self._refresh_tree(select=path if not binary else "")
        self._refresh_ratio()

    def add_directory(self) -> None:
        base = self._selected_directory()
        default = f"{base}/new_dir" if base else "new_dir"
        text, ok = QInputDialog.getText(self, "新建目录", "目录路径 (用 / 分隔):", text=default)
        if not ok:
            return
        directory = normalize_project_path(text)
        if not directory:
            QMessageBox.warning(self, "路径无效", "目录名不能为空。")
            return
        # 用一个占位文件把空目录"钉住" —— 容器里只存文件, 空目录本身无法表达
        placeholder = f"{directory}/.gitkeep"
        if placeholder not in self._work_files:
            self._work_files[placeholder] = ProjectFile(path=placeholder, content="")
            self._dirty_paths.add(placeholder)
        self._refresh_tree(select=placeholder)

    def rename_selected(self) -> None:
        data = self._current()
        if not data or data[0] != "file":
            QMessageBox.information(self, "重命名", "请先在左侧选中一个文件。")
            return
        old_path = data[1]
        new_path = self._ask_path("重命名文件", old_path)
        if new_path is None or new_path == old_path:
            return
        if new_path in self._work_files:
            QMessageBox.warning(self, "已存在", f"目标路径已存在: {new_path}")
            return
        file = self._work_files.pop(old_path)
        file.path = new_path
        if not file.binary:
            file.language = detect_language_from_filename(new_path)
        self._work_files[new_path] = file
        self._dirty_paths.discard(old_path)
        self._dirty_paths.add(new_path)
        self._refresh_tree(select=new_path)
        self._refresh_ratio()

    def delete_selected(self) -> None:
        data = self._current()
        if not data:
            QMessageBox.information(self, "删除", "请先在左侧选中一个文件或目录。")
            return
        kind, path = data
        if kind == "file":
            answer = QMessageBox.question(self, "删除文件", f"确定从空间里删除 {path} 吗?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._work_files.pop(path, None)
            self._dirty_paths.add(path)
        else:
            prefix = f"{path}/" if path else ""
            doomed = [p for p in self._work_files if p.startswith(prefix)] if prefix else list(self._work_files)
            if not doomed:
                return
            answer = QMessageBox.question(
                self, "删除目录", f"确定删除 {path or '项目根'} 下的 {len(doomed)} 个文件吗?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            for item_path in doomed:
                self._work_files.pop(item_path, None)
                self._dirty_paths.add(item_path)
        self._refresh_tree()
        self._refresh_ratio()

    def _on_tree_menu(self, point) -> None:
        item = self.tree.itemAt(point)
        if item is not None:
            self.tree.setCurrentItem(item)
        data = self._current()
        menu = QMenu(self)
        directory = data[1] if data and data[0] == "dir" else self._selected_directory()
        menu.addAction("新建文件…", lambda: self.add_file(directory))
        menu.addAction("新建目录…", self.add_directory)
        if data and data[0] == "file":
            menu.addSeparator()
            menu.addAction("重命名…", self.rename_selected)
            menu.addAction("删除文件", self.delete_selected)
        elif data:
            menu.addSeparator()
            menu.addAction("删除目录", self.delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    # ----------------------------------------------------------------------------
    def _on_save(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "缺少名称", "请填写空间名称。")
            self.name_edit.setFocus()
            return
        oversized = [
            f.path for f in self._files()
            if not f.binary and f.computed_size > MAX_FILE_BYTES
        ]
        if oversized:
            QMessageBox.warning(
                self,
                "文件过大",
                "以下文件超过单文件上限 "
                f"{MAX_FILE_BYTES // 1024} KB, 请拆分或删减:\n\n" + "\n".join(oversized[:10]),
            )
            return
        self.accept()

    def result_data(self) -> Dict[str, object]:
        return {
            "name": self.name_edit.text().strip(),
            "description": self.description_edit.toPlainText(),
            "prerequisites": self.prerequisites_edit.toPlainText(),
            "tags": normalize_tags(
                self.tags_edit.text().replace(",", " ").replace("，", " ").split()
            ),
            "status": self.status_combo.currentData() or "planned",
            "favorite": self.favorite_check.isChecked(),
            "entry_point": normalize_project_path(self.entry_point_edit.text()),
            "files": sorted(self._files(), key=lambda f: f.path),
        }


__all__ = ["SpaceEditorDialog"]
