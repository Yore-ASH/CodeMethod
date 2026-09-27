"""空间详情面板: 项目结构树 + README + 语言占比.

与模块详情并列显示在右侧, 由主窗口的 QStackedWidget 切换。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.languages import get_language
from ...core.models import STATUS_COLORS, STATUS_LABELS, format_ts
from ...core.spaces import ProjectFile, Space, TreeNode
from ..editor import CodePreview
from ..theme import DEFAULT_THEME, Theme
from .language_bar import LanguageBar, LanguageLegend
from .tag_chip import TagChipBar


class SpaceTree(QTreeWidget):
    """项目结构树。双击文件请求打开。"""

    file_activated = Signal(str)
    file_context_requested = Signal(str, object)
    directory_context_requested = Signal(str, object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.setIndentation(14)

    def load(self, root: TreeNode) -> None:
        self.clear()
        root_item = QTreeWidgetItem(self)
        root_item.setText(0, "（项目根目录）")
        root_item.setData(0, Qt.ItemDataRole.UserRole, ("dir", ""))
        self._fill(root_item, root)
        root_item.setExpanded(True)

    def _fill(self, parent: QTreeWidgetItem, node: TreeNode) -> None:
        for child in node.sorted_children():
            item = QTreeWidgetItem(parent)
            if child.is_dir:
                item.setText(0, f"{child.name}/")
                item.setData(0, Qt.ItemDataRole.UserRole, ("dir", child.path))
                self._fill(item, child)
                item.setExpanded(True)
            else:
                language = get_language(child.language)
                item.setText(0, f"{child.name}   {language.name} · {child.size} B")
                item.setData(0, Qt.ItemDataRole.UserRole, ("file", child.path))

    def _entry_at(self, item: Optional[QTreeWidgetItem]):
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = self._entry_at(item)
        if data and data[0] == "file":
            self.file_activated.emit(data[1])

    def _on_context_menu(self, point) -> None:
        item = self.itemAt(point)
        data = self._entry_at(item)
        if not data:
            return
        global_pos = self.viewport().mapToGlobal(point)
        if data[0] == "file":
            self.file_context_requested.emit(data[1], global_pos)
        else:
            self.directory_context_requested.emit(data[1], global_pos)


class SpaceDetailPanel(QWidget):
    """展示一个独立空间。"""

    edit_requested = Signal(str)
    history_requested = Signal(str)
    favorite_toggled = Signal(str)
    delete_requested = Signal(str)
    duplicate_requested = Signal(str)
    tag_clicked = Signal(str)
    copy_done = Signal(str)
    export_requested = Signal(str, str)
    open_file_requested = Signal(str, str)          # (space_id, path)
    add_file_requested = Signal(str)
    edit_file_requested = Signal(str, str)
    delete_file_requested = Signal(str, str)
    add_directory_requested = Signal(str)
    rename_file_requested = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._space: Optional[Space] = None
        self._tag_colors: Dict[str, str] = {}
        self.setObjectName("DetailPane")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._empty = QLabel(
            "选择左侧的一个空间以查看项目结构\n\n"
            "空间是库内的一个完整项目: 所有文件都保存在同一个 .cmdb 里,\n"
            "可以建目录、加文件, 并自动统计语言占比与索引 README。",
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

        splitter = QSplitter(Qt.Orientation.Horizontal, self._content)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_tree_panel())
        splitter.addWidget(self._build_content_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes([300, 520])
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
        self._fav_button.setToolTip("收藏 / 取消收藏")
        self._fav_button.clicked.connect(
            lambda: self._space and self.favorite_toggled.emit(self._space.id)
        )
        row.addWidget(self._fav_button)
        layout.addLayout(row)

        info = QHBoxLayout()
        info.setSpacing(8)
        self._kind_badge = QLabel("空间", header)
        self._kind_badge.setObjectName("StatusBadge")
        self._kind_badge.setStyleSheet(
            "color:#4EC9B0; border:1px solid #4EC9B0; border-radius:8px; padding:1px 8px;"
        )
        info.addWidget(self._kind_badge)
        self._status_badge = QLabel("", header)
        info.addWidget(self._status_badge)
        self._meta = QLabel("", header)
        self._meta.setObjectName("DimLabel")
        info.addWidget(self._meta)
        info.addStretch(1)

        self._edit_button = QPushButton("编辑", header)
        self._edit_button.clicked.connect(
            lambda: self._space and self.edit_requested.emit(self._space.id)
        )
        self._history_button = QPushButton("历史", header)
        self._history_button.setProperty("flat", True)
        self._history_button.clicked.connect(
            lambda: self._space and self.history_requested.emit(self._space.id)
        )
        self._add_file_button = QPushButton("＋ 文件", header)
        self._add_file_button.setProperty("flat", True)
        self._add_file_button.setToolTip("在空间里新建一个文件")
        self._add_file_button.clicked.connect(
            lambda: self._space and self.add_file_requested.emit(self._space.id)
        )
        self._more_button = QToolButton(header)
        self._more_button.setText("⋯")
        self._more_button.setFixedWidth(30)
        self._more_button.setAutoRaise(True)
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._build_more_menu()

        info.addWidget(self._edit_button)
        info.addWidget(self._history_button)
        info.addWidget(self._add_file_button)
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
            ("新建文件…", lambda: self._space and self.add_file_requested.emit(self._space.id)),
            ("新建目录…", lambda: self._space and self.add_directory_requested.emit(self._space.id)),
            (None, None),
            ("复制全部文件到剪贴板", self._copy_all_files),
            ("复制空间为 Markdown", lambda: self._space and self.export_requested.emit(self._space.id, "markdown")),
            ("复制空间为 JSON", lambda: self._space and self.export_requested.emit(self._space.id, "json")),
            (None, None),
            ("创建副本", lambda: self._space and self.duplicate_requested.emit(self._space.id)),
            ("删除空间", lambda: self._space and self.delete_requested.emit(self._space.id)),
        ]
        for text, slot in items:
            if text is None:
                menu.addSeparator()
                continue
            action = QAction(text, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
        self._more_button.setMenu(menu)

    def _build_tree_panel(self) -> QWidget:
        panel = QWidget(self._content)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget(panel)
        bar.setObjectName("CodeHeader")
        bar.setFixedHeight(30)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 0, 6, 0)
        row.setSpacing(6)
        label = QLabel("项目结构", bar)
        label.setObjectName("MutedLabel")
        row.addWidget(label)
        self._tree_count = QLabel("", bar)
        self._tree_count.setObjectName("DimLabel")
        row.addWidget(self._tree_count)
        row.addStretch(1)
        expand = QToolButton(bar)
        expand.setText("展开")
        expand.setAutoRaise(True)
        expand.clicked.connect(lambda: self.tree.expandAll())
        collapse = QToolButton(bar)
        collapse.setText("折叠")
        collapse.setAutoRaise(True)
        collapse.clicked.connect(lambda: self.tree.collapseAll())
        row.addWidget(expand)
        row.addWidget(collapse)
        layout.addWidget(bar)

        self.tree = SpaceTree(panel)
        self.tree.file_activated.connect(
            lambda path: self._space and self.open_file_requested.emit(self._space.id, path)
        )
        self.tree.file_context_requested.connect(self._on_file_menu)
        self.tree.directory_context_requested.connect(self._on_directory_menu)
        layout.addWidget(self.tree, 1)
        return panel

    def _build_content_panel(self) -> QWidget:
        self.tabs = QTabWidget(self._content)
        self.tabs.setDocumentMode(True)

        # 概览
        scroll = QScrollArea(self.tabs)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget(scroll)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)

        ratio_label = QLabel("语言占比", container)
        ratio_label.setObjectName("SectionLabel")
        layout.addWidget(ratio_label)
        self.language_bar = LanguageBar(container, theme=self._theme)
        layout.addWidget(self.language_bar)
        self.language_legend = LanguageLegend(container, theme=self._theme)
        layout.addWidget(self.language_legend)

        self.readme_label = QLabel("README", container)
        self.readme_label.setObjectName("SectionLabel")
        layout.addWidget(self.readme_label)
        self.readme_view = QTextBrowser(container)
        self.readme_view.setFrameShape(QFrame.Shape.NoFrame)
        self.readme_view.setOpenExternalLinks(False)
        self.readme_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.readme_view.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        layout.addWidget(self.readme_view)

        from .detail_panel import _MetaField

        self.prerequisites_field = _MetaField("前置要求", container)
        layout.addWidget(self.prerequisites_field)
        self.description_field = _MetaField("描述", container)
        layout.addWidget(self.description_field)
        layout.addStretch(1)
        scroll.setWidget(container)
        self.tabs.addTab(scroll, "概览")

        self._editor_tab = QWidget(self.tabs)
        editor_layout = QVBoxLayout(self._editor_tab)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        self.preview = CodePreview(self._editor_tab, theme=self._theme)
        self.preview.set_editable(False)
        self.preview.edit_requested.connect(self._edit_current_file)
        self.preview.copy_requested.connect(
            lambda _t: self.copy_done.emit("已复制文件内容到剪贴板")
        )
        editor_layout.addWidget(self.preview)
        self._preview_path = ""
        self.tabs.addTab(self._editor_tab, "文件内容")
        self.tabs.setTabEnabled(1, False)
        return self.tabs

    # ----------------------------------------------------------------------------
    def _on_file_menu(self, path: str, global_pos) -> None:
        if self._space is None:
            return
        menu = QMenu(self)
        menu.addAction("打开", lambda: self.open_file_requested.emit(self._space.id, path))
        menu.addAction("编辑…", lambda: self.edit_file_requested.emit(self._space.id, path))
        menu.addSeparator()
        menu.addAction("重命名…", lambda: self.rename_file_requested.emit(self._space.id, path))
        menu.addAction("复制内容", lambda: self._copy_file(path))
        menu.addSeparator()
        menu.addAction("删除文件", lambda: self.delete_file_requested.emit(self._space.id, path))
        menu.exec(global_pos)

    def _on_directory_menu(self, path: str, global_pos) -> None:
        if self._space is None:
            return
        menu = QMenu(self)
        menu.addAction("在此新建文件…", lambda: self._request_file_in(path))
        menu.addAction("新建目录…", lambda: self.add_directory_requested.emit(self._space.id))
        if path:
            menu.addSeparator()
            menu.addAction("删除目录", lambda: self.delete_file_requested.emit(self._space.id, path))
        menu.exec(global_pos)

    def _request_file_in(self, directory: str) -> None:
        if self._space is None:
            return
        # 用一个简单的前缀提示, 由主窗口弹输入框时预填
        self.add_file_requested.emit(self._space.id + "\x00" + directory)

    def _edit_current_file(self) -> None:
        if self._space is not None and self._preview_path:
            self.edit_file_requested.emit(self._space.id, self._preview_path)

    def _copy_file(self, path: str) -> None:
        if self._space is None:
            return
        file = self._space.get_file(path)
        if file is None:
            return
        QApplication.clipboard().setText(file.content)
        self.copy_done.emit(f"已复制 {path} ({len(file.content)} 字符) 到剪贴板")

    def _copy_all_files(self) -> None:
        if self._space is None:
            return
        parts: List[str] = []
        for file in sorted(self._space.files, key=lambda f: f.path):
            parts.append(f"===== {file.path} =====")
            parts.append(file.content if not file.binary else f"（二进制, {file.size} 字节）")
            parts.append("")
        text = "\n".join(parts)
        QApplication.clipboard().setText(text)
        self.copy_done.emit(
            f"已复制 {len(self._space.files)} 个文件 (共 {len(text)} 字符) 到剪贴板"
        )

    # ----------------------------------------------------------------------------
    def set_entry(self, space: Optional[Space], *, tag_colors: Optional[Dict[str, str]] = None) -> None:
        """与模块详情保持同名接口, 便于主窗口统一调用。"""
        self.set_space(space, tag_colors=tag_colors)

    def set_space(self, space: Optional[Space], *, tag_colors: Optional[Dict[str, str]] = None) -> None:
        if tag_colors is not None:
            self._tag_colors = {k.casefold(): v for k, v in tag_colors.items()}
        self._space = space
        if space is None:
            self._content.setVisible(False)
            self._empty.setVisible(True)
            self._status_label.setText("")
            self.tree.clear()
            return

        self._empty.setVisible(False)
        self._content.setVisible(True)

        self._title.setText(space.display_title)
        self._fav_button.setText("★" if space.favorite else "☆")
        self._status_badge.setText(STATUS_LABELS.get(space.status, space.status))
        self._status_badge.setStyleSheet(
            f"color:{STATUS_COLORS.get(space.status, self._theme.text_muted)};"
            f"border:1px solid {STATUS_COLORS.get(space.status, self._theme.text_muted)};"
            "border-radius:8px; padding:1px 8px;"
        )
        self._meta.setText(
            f"更新 {format_ts(space.updated_at)} · {len(space.files)} 个文件 · "
            f"{space.total_lines} 行 · {space.total_size} 字节 · ID {space.id}"
        )

        self.tree.load(space.tree())
        self._tree_count.setText(f"({len(space.files)} 个文件)")

        shares = space.language_shares()
        self.language_bar.set_shares(shares)
        self.language_legend.set_shares(shares)

        readmes = space.readme_files
        self.readme_label.setText(f"README ({len(readmes)})" if readmes else "README")
        if readmes:
            blocks = []
            for file in readmes:
                blocks.append(f"<h4 style='margin:6px 0 2px 0'>{file.path}</h4>")
                blocks.append(self._markdown_to_html(file.content))
            self.readme_view.setHtml("".join(blocks))
            self.readme_view.setFixedHeight(min(320, 90 + 60 * len(readmes)))
        else:
            self.readme_view.setHtml(
                "<span style='color:#808080;font-style:italic'>"
                "这个空间还没有 README.md —— 建一个就能被自动索引与检索。</span>"
            )
            self.readme_view.setFixedHeight(60)

        self.prerequisites_field.set_text(space.prerequisites)
        self.description_field.set_text(space.description)

        self._show_file("" if not space.files else sorted(space.files, key=lambda f: f.path)[0].path)

        self._status_label.setText(
            f"{space.kind_label} · {len(space.files)} 个文件 · {len(space.directories())} 个目录 · "
            f"{space.language_summary()} · {len(space.tags)} 个标签"
        )

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """极简 Markdown 渲染 (标题/列表/代码块/粗体), 避免引入额外依赖。"""
        import html as html_module

        lines_out: List[str] = []
        in_code = False
        for raw in (text or "").splitlines():
            stripped = raw.strip()
            if stripped.startswith("```"):
                if in_code:
                    lines_out.append("</pre>")
                else:
                    lines_out.append(
                        "<pre style='background:rgba(127,127,127,0.12);padding:6px;"
                        "border-radius:4px;white-space:pre-wrap'>"
                    )
                in_code = not in_code
                continue
            escaped = html_module.escape(raw)
            if in_code:
                lines_out.append(escaped)
            elif stripped.startswith("#"):
                level = min(4, len(stripped) - len(stripped.lstrip("#")))
                content = html_module.escape(stripped.lstrip("#").strip())
                lines_out.append(f"<h{level + 1} style='margin:8px 0 2px 0'>{content}</h{level + 1}>")
            elif stripped.startswith(("- ", "* ")):
                lines_out.append(f"&nbsp;&nbsp;• {html_module.escape(stripped[2:])}")
            elif not stripped:
                lines_out.append("<br/>")
            else:
                lines_out.append(escaped)
        if in_code:
            lines_out.append("</pre>")
        return "<div style='white-space:pre-wrap'>" + "<br/>".join(lines_out) + "</div>"

    def _show_file(self, path: str) -> None:
        if self._space is None or not path:
            self.tabs.setTabEnabled(1, False)
            self._preview_path = ""
            return
        file = self._space.get_file(path)
        if file is None:
            self.tabs.setTabEnabled(1, False)
            return
        self._preview_path = path
        if file.binary:
            self.preview.set_code(
                f"（二进制文件, {file.size} 字节 —— CodeMethod 只记录大小, 不保存内容）",
                language="plaintext",
                filename=file.name,
            )
        else:
            self.preview.set_code(file.content, language=file.language, filename=file.path)
        self.tabs.setTabEnabled(1, True)
        self.tabs.setTabText(1, file.name)

    def show_file(self, path: str) -> None:
        self._show_file(path)
        self.tabs.setCurrentIndex(1)

    def current_space(self) -> Optional[Space]:
        return self._space

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.preview.editor.set_theme(theme)
        self.language_bar.set_theme(theme)
        self.language_legend.set_theme(theme)


__all__ = ["SpaceDetailPanel", "SpaceTree"]
