"""条目详情面板: 概览 + 每个语言实现一个标签页.

设计要点
--------
* 代码区默认**只读** (避免在主界面里改了代码却悄悄丢失), 每个页签自带「编辑」按钮
  打开 :class:`~codemethod.ui.dialogs.implementation_dialog.ImplementationDialog`;
* 页签栏右上角与概览页都有「＋ 语言实现」入口, 让"同一个问题用多种语言实现"随手可用;
* 头部与概览页都会列出已用的语言清单, 一眼看出这条记录覆盖了哪几种语言。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.languages import get_language
from ...core.models import STATUS_COLORS, STATUS_LABELS, Entry, format_ts
from ..editor import CodePreview
from ..theme import DEFAULT_THEME, Theme
from .tag_chip import TagChipBar


class StatusBadge(QLabel):
    """规划状态徽标 (彩色圆角)。"""

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._color = QColor(theme.text_muted)
        self.setObjectName("StatusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(20)
        self.set_status("idea")

    def set_status(self, status: str) -> None:
        self._color = QColor(STATUS_COLORS.get(status, self._theme.text_muted))
        self.setText(STATUS_LABELS.get(status, status))
        self.setToolTip(f"规划状态: {self.text()}")
        width = self.fontMetrics().horizontalAdvance(self.text()) + 24
        self.setMinimumWidth(width)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(0, 1, -1, -1)
        path = QPainterPath()
        radius = rect.height() / 2
        path.addRoundedRect(rect, radius, radius)
        background = QColor(self._color)
        background.setAlpha(60)
        painter.fillPath(path, background)
        painter.setPen(self._color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()


class _MetaField(QWidget):
    """「标题 + 内容」的小块。"""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.title_label = QLabel(title.upper(), self)
        self.title_label.setObjectName("SectionLabel")
        self.body = QTextBrowser(self)
        self.body.setOpenExternalLinks(False)
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.body.setStyleSheet("QTextBrowser { background: transparent; border: none; }")

        layout.addWidget(self.title_label)
        layout.addWidget(self.body)

    def set_text(self, text: str, *, placeholder: str = "（未填写）") -> None:
        content = (text or "").strip()
        if content:
            escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            self.body.setHtml(
                "<div style='white-space:pre-wrap;line-height:150%'>"
                + escaped.replace("\n", "<br/>")
                + "</div>"
            )
        else:
            self.body.setHtml(
                "<span style='color:#808080;font-style:italic'>" + placeholder + "</span>"
            )
        self._adjust_height()

    def _adjust_height(self) -> None:
        document = self.body.document()
        document.setTextWidth(max(200, self.body.viewport().width()))
        height = int(document.size().height()) + 8
        self.body.setFixedHeight(max(28, min(height, 240)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._adjust_height()


class DetailPanel(QWidget):
    """展示单个条目的全部信息与所有语言实现。"""

    edit_requested = Signal(str)
    history_requested = Signal(str)
    favorite_toggled = Signal(str)
    delete_requested = Signal(str)
    duplicate_requested = Signal(str)
    tag_clicked = Signal(str)
    copy_done = Signal(str)
    export_requested = Signal(str, str)                   # (entry_id, 格式)
    add_implementation_requested = Signal(str)            # (entry_id)
    edit_implementation_requested = Signal(str, str)      # (entry_id, implementation_id)
    delete_implementation_requested = Signal(str, str)    # (entry_id, implementation_id)

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._entry: Optional[Entry] = None
        self._tag_colors: Dict[str, str] = {}
        self._previews: List[CodePreview] = []
        self._impl_ids: List[str] = []
        self.setObjectName("DetailPane")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- 空状态 ----------
        self._empty = QLabel(
            "选择左侧的一个条目以查看详情\n\n"
            "一个条目可以挂载多种语言的实现 —— 这是本工具的核心用法。\n"
            "用详情页签栏右上角的「＋ 语言实现」, 为同一个问题添加另一种语言的解法。\n"
            "所有修改都会进入历史, 可随时全量回滚。",
            self,
        )
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setObjectName("DimLabel")
        self._empty.setWordWrap(True)
        root.addWidget(self._empty, 1)

        # ---------- 内容 ----------
        self._content = QWidget(self)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        content_layout.addWidget(self._build_header())

        self.tabs = QTabWidget(self._content)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(False)
        self.tabs.setMovable(False)
        content_layout.addWidget(self.tabs, 1)

        # 页签栏右上角的「＋ 语言实现」: 最容易被发现的入口
        self._add_impl_button = QToolButton(self.tabs)
        self._add_impl_button.setText("＋ 语言实现")
        self._add_impl_button.setToolTip(
            "为这个条目添加另一种语言的实现\n(同一个问题, 不同语言的解法)"
        )
        self._add_impl_button.setAutoRaise(True)
        self._add_impl_button.clicked.connect(self._emit_add_implementation)
        self.tabs.setCornerWidget(self._add_impl_button, Qt.Corner.TopRightCorner)

        root.addWidget(self._content, 1)
        self._content.setVisible(False)

        self._status_label = QLabel("", self)
        self._status_label.setObjectName("DimLabel")
        self._status_label.setContentsMargins(10, 3, 10, 5)
        root.addWidget(self._status_label)

    # ----------------------------------------------------------------------------
    # 头部
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
        self._fav_button.setToolTip("收藏 / 取消收藏 (Ctrl+D)")
        self._fav_button.setAutoRaise(True)
        self._fav_button.clicked.connect(
            lambda: self._entry and self.favorite_toggled.emit(self._entry.id)
        )
        row.addWidget(self._fav_button)
        layout.addLayout(row)

        info = QHBoxLayout()
        info.setSpacing(8)
        self._status_badge = StatusBadge(header, theme=self._theme)
        info.addWidget(self._status_badge)
        self._meta = QLabel("", header)
        self._meta.setObjectName("DimLabel")
        self._meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(self._meta)
        info.addStretch(1)

        self._edit_button = QPushButton("编辑", header)
        self._edit_button.setToolTip("编辑描述、前置要求、标签与全部语言实现 (Ctrl+E)")
        self._edit_button.clicked.connect(
            lambda: self._entry and self.edit_requested.emit(self._entry.id)
        )
        self._history_button = QPushButton("历史", header)
        self._history_button.setProperty("flat", True)
        self._history_button.setToolTip("查看修订历史并回滚 (Ctrl+H)")
        self._history_button.clicked.connect(
            lambda: self._entry and self.history_requested.emit(self._entry.id)
        )
        self._copy_button = QPushButton("复制全部代码", header)
        self._copy_button.setProperty("flat", True)
        self._copy_button.clicked.connect(self._copy_all_code)
        self._more_button = QToolButton(header)
        self._more_button.setText("⋯")
        self._more_button.setToolTip("更多操作")
        self._more_button.setAutoRaise(True)
        self._more_button.setFixedWidth(30)
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._build_more_menu()

        info.addWidget(self._edit_button)
        info.addWidget(self._history_button)
        info.addWidget(self._copy_button)
        info.addWidget(self._more_button)
        layout.addLayout(info)

        divider = QFrame(header)
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {self._theme.border};")
        layout.addWidget(divider)
        return header

    def _build_more_menu(self) -> None:
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self._more_button)
        items = [
            ("添加语言实现…", self._emit_add_implementation),
            (None, None),
            ("复制条目为 Markdown", lambda: self._entry and self.export_requested.emit(self._entry.id, "markdown")),
            ("复制条目为 JSON", lambda: self._entry and self.export_requested.emit(self._entry.id, "json")),
            (None, None),
            ("创建副本", lambda: self._entry and self.duplicate_requested.emit(self._entry.id)),
            ("删除条目", lambda: self._entry and self.delete_requested.emit(self._entry.id)),
        ]
        for text, slot in items:
            if text is None:
                menu.addSeparator()
                continue
            action = QAction(text, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
        self._more_button.setMenu(menu)

    # ----------------------------------------------------------------------------
    # 内容构建
    # ----------------------------------------------------------------------------
    def _clear_tabs(self) -> None:
        self._previews.clear()
        self._impl_ids.clear()
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _build_overview(self, entry: Entry) -> QWidget:
        scroll = QScrollArea(self.tabs)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget(scroll)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)

        tags_label = QLabel("标签", container)
        tags_label.setObjectName("SectionLabel")
        layout.addWidget(tags_label)
        chips = TagChipBar(container, theme=self._theme, placeholder="（无标签 — 点击编辑添加标签）")
        chips.set_tags(entry.tags, colors=self._tag_colors, max_tags=None)
        chips.tag_clicked.connect(self.tag_clicked.emit)
        layout.addWidget(chips)

        # ---- 语言实现总览: 突出"同一问题的多种语言实现" ----
        impls_label = QLabel(
            f"语言实现 ({len(entry.active_implementations)})", container
        )
        impls_label.setObjectName("SectionLabel")
        layout.addWidget(impls_label)

        for impl in entry.active_implementations:
            line = QWidget(container)
            # 竖向: 第一行是"语言 + 说明 + 操作", 第二行是该语言自己的前置要求
            line_layout = QVBoxLayout(line)
            line_layout.setContentsMargins(0, 0, 0, 2)
            line_layout.setSpacing(2)

            top = QHBoxLayout()
            top.setSpacing(8)
            badge = QLabel(get_language(impl.language).name, line)
            badge.setObjectName("CountBadge")
            top.addWidget(badge)

            describe = QLabel(
                f"{impl.display_title} · {impl.filename} · {impl.line_count} 行"
                + (f" · {impl.notes}" if impl.notes else ""),
                line,
            )
            describe.setObjectName("MutedLabel")
            describe.setWordWrap(True)
            top.addWidget(describe, 1)

            view = QPushButton("查看", line)
            view.setProperty("flat", True)
            view.setToolTip(f"跳到 {get_language(impl.language).name} 实现")
            view.clicked.connect(lambda _c=False, iid=impl.id: self.show_implementation(iid))
            top.addWidget(view)

            edit = QPushButton("编辑", line)
            edit.setProperty("flat", True)
            edit.setToolTip("编辑这一种语言的实现")
            edit.clicked.connect(
                lambda _c=False, iid=impl.id: self._emit_edit_implementation(iid)
            )
            top.addWidget(edit)

            remove = QPushButton("删除", line)
            remove.setProperty("flat", True)
            remove.setToolTip("删除这一种语言的实现 (会记录到历史, 可回滚)")
            remove.clicked.connect(
                lambda _c=False, iid=impl.id: self._request_delete_implementation(iid)
            )
            top.addWidget(remove)
            line_layout.addLayout(top)

            # 每种语言自己的前置要求 (前置要求是分语言的)
            prerequisite = impl.prerequisites.strip()
            prereq_label = QLabel(
                f"　　前置要求: {prerequisite}" if prerequisite else "　　前置要求: （未填写）",
                line,
            )
            prereq_label.setObjectName("MutedLabel" if prerequisite else "DimLabel")
            prereq_label.setWordWrap(True)
            prereq_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            line_layout.addWidget(prereq_label)
            layout.addWidget(line)

        if not entry.active_implementations:
            hint = QLabel("还没有任何语言实现。", container)
            hint.setObjectName("DimLabel")
            layout.addWidget(hint)

        add_button = QPushButton("＋ 为这个问题添加一种语言实现", container)
        add_button.setToolTip("同一个功能可以用多种语言实现, 例如 Python 版 + Go 版 + Rust 版")
        add_button.clicked.connect(self._emit_add_implementation)
        layout.addWidget(add_button)

        description = _MetaField("描述", container)
        description.set_text(entry.description)
        layout.addWidget(description)

        # 条目级: 与语言无关的通用前置要求
        prerequisites = _MetaField("通用前置要求 (所有语言共用)", container)
        prerequisites.set_text(entry.prerequisites)
        layout.addWidget(prerequisites)

        layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

    # ----------------------------------------------------------------------------
    def _emit_add_implementation(self) -> None:
        if self._entry is not None:
            self.add_implementation_requested.emit(self._entry.id)

    def _emit_edit_implementation(self, implementation_id: str) -> None:
        if self._entry is not None:
            self.edit_implementation_requested.emit(self._entry.id, implementation_id)

    def _request_delete_implementation(self, implementation_id: str) -> None:
        if self._entry is None:
            return
        impl = self._entry.get_implementation(implementation_id)
        if impl is None:
            return
        answer = QMessageBox.question(
            self,
            "删除实现",
            f"确定删除《{self._entry.display_title}》的 "
            f"{get_language(impl.language).name} 实现吗?\n\n"
            "该实现会从条目中移除, 内容仍保留在修订历史里, 可以随时回滚。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.delete_implementation_requested.emit(self._entry.id, implementation_id)

    # ----------------------------------------------------------------------------
    def set_entry(self, entry: Optional[Entry], *, tag_colors: Optional[Dict[str, str]] = None) -> None:
        if tag_colors is not None:
            self._tag_colors = {k.casefold(): v for k, v in tag_colors.items()}
        self._entry = entry
        if entry is None:
            self._content.setVisible(False)
            self._empty.setVisible(True)
            self._status_label.setText("")
            self._clear_tabs()
            return

        self._empty.setVisible(False)
        self._content.setVisible(True)

        self._title.setText(entry.display_title)
        self._status_badge.set_status(entry.status)
        self._fav_button.setText("★" if entry.favorite else "☆")
        self._fav_button.setStyleSheet(
            f"QToolButton {{ color: {'#DCDCAA' if entry.favorite else self._theme.text_muted};"
            f" font-size: 14pt; }}"
        )
        self._meta.setText(
            f"更新 {format_ts(entry.updated_at)} · 创建 {format_ts(entry.created_at)} · "
            f"{len(entry.active_implementations)} 种语言 · {entry.total_lines} 行代码 · "
            f"ID {entry.id}"
        )

        self._clear_tabs()
        self.tabs.addTab(self._build_overview(entry), "概览")

        for impl in entry.active_implementations:
            preview = CodePreview(self.tabs, language=impl.language, theme=self._theme)
            preview.set_code(impl.code, language=impl.language, filename=impl.filename)
            # 只读预览 + 「编辑」按钮: 避免在主界面改了代码却悄悄丢失
            preview.set_editable(False)
            preview.copy_requested.connect(
                lambda _text, lang=impl.language: self.copy_done.emit(
                    f"已复制 {get_language(lang).name} 代码到剪贴板"
                )
            )
            preview.edit_requested.connect(
                lambda eid=entry.id, iid=impl.id: self.edit_implementation_requested.emit(eid, iid)
            )
            self._previews.append(preview)
            self._impl_ids.append(impl.id)
            page = self._wrap_implementation_tab(preview, impl)
            self.tabs.addTab(page, impl.display_title)
            self.tabs.setTabToolTip(
                self.tabs.count() - 1,
                f"{get_language(impl.language).name} · {impl.filename} · {impl.line_count} 行"
                + (f"\n前置要求: {impl.prerequisites.strip()}" if impl.prerequisites.strip() else ""),
            )

        removed = [impl for impl in entry.implementations if impl.deleted]
        if removed:
            note = QLabel(
                "已删除的实现: " + "、".join(impl.display_title for impl in removed),
                self,
            )
            note.setObjectName("DimLabel")
            note.setContentsMargins(12, 2, 12, 2)
            self.tabs.addTab(note, f"回收 ({len(removed)})")

        total_chars = sum(len(impl.code) for impl in entry.active_implementations)
        languages = "、".join(get_language(l).name for l in entry.languages) or "无"
        self._status_label.setText(
            f"{len(entry.active_implementations)} 种语言实现 ({languages}) · "
            f"{entry.total_lines} 行 · {total_chars} 字符 · {len(entry.tags)} 个标签"
        )

    # ----------------------------------------------------------------------------
    # 操作
    def _wrap_implementation_tab(self, preview: CodePreview, implementation) -> QWidget:
        """给代码预览加一条"本语言前置要求"信息栏。

        前置要求是分语言的, 所以放在每种语言自己的页签里, 打开就能看到。
        """
        prerequisite = implementation.prerequisites.strip()
        if not prerequisite:
            return preview

        page = QWidget(self.tabs)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget(page)
        bar.setObjectName("CodeHeader")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 4, 10, 4)
        bar_layout.setSpacing(6)

        title = QLabel(f"{get_language(implementation.language).name} 前置要求", bar)
        title.setObjectName("SectionLabel")
        bar_layout.addWidget(title)

        text = QLabel(prerequisite, bar)
        text.setObjectName("MutedLabel")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bar_layout.addWidget(text, 1)

        layout.addWidget(bar)
        layout.addWidget(preview, 1)
        return page

    # ----------------------------------------------------------------------------
    def _copy_all_code(self) -> None:
        if self._entry is None:
            return
        parts: List[str] = []
        for impl in self._entry.active_implementations:
            parts.append(f"// ===== {get_language(impl.language).name} · {impl.filename} =====")
            parts.append(impl.code)
            parts.append("")
        text = "\n".join(parts).strip()
        QApplication.clipboard().setText(text)
        self.copy_done.emit(
            f"已复制 {len(self._entry.active_implementations)} 种语言实现 "
            f"(共 {len(text)} 字符) 到剪贴板"
        )

    def current_entry(self) -> Optional[Entry]:
        return self._entry

    def show_implementation(self, implementation_id: str) -> bool:
        """切到某种语言实现的页签。"""
        if implementation_id in self._impl_ids:
            self.tabs.setCurrentIndex(self._impl_ids.index(implementation_id) + 1)
            return True
        return False

    def current_implementation_id(self) -> str:
        index = self.tabs.currentIndex() - 1
        if 0 <= index < len(self._impl_ids):
            return self._impl_ids[index]
        return ""

    def show_tab(self, index: int) -> None:
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._status_badge._theme = theme
        for preview in self._previews:
            preview.editor.set_theme(theme)
        if self._entry is not None:
            self.set_entry(self._entry, tag_colors=self._tag_colors)


__all__ = ["DetailPanel", "StatusBadge"]
