"""条目详情面板: 概览 + 每个语言实现一个标签页。"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
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

    def __init__(self, title: str, parent: Optional[QWidget] = None, *, mono: bool = False) -> None:
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
        self.body.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; }"
        )

        layout.addWidget(self.title_label)
        layout.addWidget(self.body)

    def set_text(self, text: str, *, placeholder: str = "（未填写）") -> None:
        content = (text or "").strip()
        if content:
            escaped = (
                content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
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
        self.body.setFixedHeight(max(28, min(height, 260)))

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
    export_requested = Signal(str, str)  # (entry_id, 格式: "markdown" / "json")

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._entry: Optional[Entry] = None
        self._tag_colors: Dict[str, str] = {}
        self._previews: List[CodePreview] = []
        self.setObjectName("DetailPane")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- 空状态 ----------
        self._empty = QLabel(
            "选择左侧的一个条目以查看详情\n\n"
            "提示: 双击条目可编辑; 每个条目可以挂载多种语言的实现; "
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
        content_layout.addWidget(self.tabs, 1)

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
        self._fav_button.setToolTip("收藏 / 取消收藏")
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
        self._edit_button.clicked.connect(
            lambda: self._entry and self.edit_requested.emit(self._entry.id)
        )
        self._history_button = QPushButton("历史", header)
        self._history_button.setProperty("flat", True)
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
        actions = [
            ("复制条目为 Markdown", lambda: self._entry and self.export_requested.emit(self._entry.id, "markdown")),
            ("复制条目为 JSON", lambda: self._entry and self.export_requested.emit(self._entry.id, "json")),
            (None, None),
            ("创建副本", lambda: self._entry and self.duplicate_requested.emit(self._entry.id)),
            ("删除条目", lambda: self._entry and self.delete_requested.emit(self._entry.id)),
        ]
        for text, slot in actions:
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
        chips.set_tags(
            entry.tags,
            colors=self._tag_colors,
            max_tags=None,
        )
        chips.tag_clicked.connect(self.tag_clicked.emit)
        layout.addWidget(chips)

        if entry.languages:
            langs = QLabel(
                "实现语言: " + "、".join(get_language(l).name for l in entry.languages),
                container,
            )
            langs.setObjectName("MutedLabel")
            langs.setWordWrap(True)
            layout.addWidget(langs)

        description = _MetaField("描述", container)
        description.set_text(entry.description)
        layout.addWidget(description)

        prerequisites = _MetaField("前置要求", container)
        prerequisites.set_text(entry.prerequisites)
        layout.addWidget(prerequisites)

        layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

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
            f"{len(entry.active_implementations)} 个实现 · {entry.total_lines} 行代码 · "
            f"条目 ID {entry.id}"
        )

        self._clear_tabs()
        self.tabs.addTab(self._build_overview(entry), "概览")

        for impl in entry.active_implementations:
            preview = CodePreview(self.tabs, language=impl.language, theme=self._theme)
            preview.set_code(impl.code, language=impl.language, filename=impl.filename)
            preview.set_editable(True)
            preview.copy_requested.connect(
                lambda _text, lang=impl.language: self.copy_done.emit(
                    f"已复制 {get_language(lang).name} 代码到剪贴板"
                )
            )
            preview.edit_requested.connect(
                lambda eid=entry.id: self.edit_requested.emit(eid)
            )
            self._previews.append(preview)
            title = impl.display_title
            if impl.deleted:
                title = "（已删除）" + title
            self.tabs.addTab(preview, title)

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
        self._status_label.setText(
            f"共 {len(entry.active_implementations)} 种语言实现 · {entry.total_lines} 行 · "
            f"{total_chars} 字符 · {len(entry.tags)} 个标签"
        )

    # ----------------------------------------------------------------------------
    # 操作
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
            f"已复制 {len(self._entry.active_implementations)} 个实现 (共 {len(text)} 字符) 到剪贴板"
        )

    def current_entry(self) -> Optional[Entry]:
        return self._entry

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
