"""README 全局检索对话框.

扫描**所有空间**里的 README 文件 (``README.md`` / ``README.rst`` / ``docs/README.md`` …),
按行给出命中结果, 双击可以跳回对应的空间并打开该文件。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...core.repository import Repository
from ...core.spaces import ReadmeHit, Space, search_readmes
from ..native import ThemedDialog
from ..theme import DEFAULT_THEME, Theme


class ReadmeSearchDialog(ThemedDialog):
    """在所有空间的 README 里搜索。"""

    open_requested = Signal(str, str)      # (space_id, path)

    def __init__(
        self,
        repository: Repository,
        parent: Optional[QWidget] = None,
        *,
        theme: Theme = DEFAULT_THEME,
        initial_query: str = "",
    ) -> None:
        super().__init__(parent)
        self._repo = repository
        self._theme = theme
        self._hits: List[ReadmeHit] = []

        self.setWindowTitle("README 检索")
        self.setMinimumSize(720, 460)
        self.resize(1040, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.summary = QLabel("", self)
        self.summary.setObjectName("MutedLabel")
        root.addWidget(self.summary)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText("在全部空间的 README 里搜索…")
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self._schedule_search)
        self.input.returnPressed.connect(self.run_search)
        row.addWidget(self.input, 1)
        self.search_button = QPushButton("搜索", self)
        self.search_button.clicked.connect(self.run_search)
        row.addWidget(self.search_button)
        root.addLayout(row)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        self.results = QListWidget(splitter)
        self.results.setUniformItemSizes(False)
        self.results.setWordWrap(True)
        self.results.currentRowChanged.connect(self._on_row_changed)
        self.results.itemDoubleClicked.connect(lambda _i: self._open_current())
        splitter.addWidget(self.results)

        preview = QWidget(splitter)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)
        self.preview_title = QLabel("选择左侧的一条结果查看上下文", preview)
        self.preview_title.setObjectName("SectionHeader")
        preview_layout.addWidget(self.preview_title)
        self.preview = QTextBrowser(preview)
        self.preview.setFrameShape(QTextBrowser.Shape.NoFrame)
        preview_layout.addWidget(self.preview, 1)
        splitter.addWidget(preview)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes([420, 560])
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox(self)
        self.open_button = buttons.addButton("打开所在文件", QDialogButtonBox.ButtonRole.ActionRole)
        self.open_button.clicked.connect(self._open_current)
        close_button = buttons.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        close_button.clicked.connect(self.reject)
        root.addWidget(buttons)

        self._debounce = None
        from PySide6.QtCore import QTimer

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self.run_search)

        self._refresh_summary()
        if initial_query:
            self.input.setText(initial_query)
            self.run_search()

    # ----------------------------------------------------------------------------
    def _schedule_search(self) -> None:
        self._debounce.start()

    def _readmes(self) -> List[Space]:
        return [s for s in self._repo.spaces.values() if not s.deleted]

    def _refresh_summary(self) -> None:
        spaces = self._readmes()
        readme_count = sum(len(s.readme_files) for s in spaces)
        self.summary.setText(
            f"已索引 {len(spaces)} 个空间中的 {readme_count} 个 README 文件"
            + ("（还没有任何 README —— 在空间里建一个 README.md 就会自动被索引）"
               if readme_count == 0 else "")
        )

    def run_search(self) -> None:
        query = self.input.text().strip()
        self.results.clear()
        self._hits = []
        if not query:
            self.preview.setHtml("")
            self.preview_title.setText("输入关键词开始搜索")
            self._refresh_summary()
            return

        self._hits = search_readmes(self._readmes(), query)
        for hit in self._hits:
            item = QListWidgetItem(f"{hit.space_name} / {hit.path}   L{hit.line_number}\n{hit.line}")
            item.setData(Qt.ItemDataRole.UserRole, hit)
            font = QFont(item.font())
            font.setBold(False)
            item.setFont(font)
            self.results.addItem(item)

        self.summary.setText(
            f"在 {len(self._readmes())} 个空间中找到 {len(self._hits)} 处匹配"
            + ("（已截断，请缩小关键词）" if len(self._hits) >= 300 else "")
        )
        if self._hits:
            self.results.setCurrentRow(0)
        else:
            self.preview_title.setText("没有匹配")
            self.preview.setHtml(
                f"<span style='color:#808080'>没有 README 包含 “{query}”。</span>"
            )

    def _on_row_changed(self, row: int) -> None:
        if not (0 <= row < len(self._hits)):
            return
        hit = self._hits[row]
        self.preview_title.setText(f"{hit.space_name} / {hit.path}  第 {hit.line_number} 行")
        lines = []
        for number, text in hit.context:
            escaped = (
                text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            if number == hit.line_number:
                lines.append(
                    f"<div style='background:{self._theme.accent_dim};'>"
                    f"<b>{number:>4}</b>  {escaped}</div>"
                )
            else:
                lines.append(f"<div><span style='color:{self._theme.text_dim}'>{number:>4}</span>  {escaped}</div>")
        self.preview.setHtml(
            "<div style='font-family:Consolas,monospace;white-space:pre-wrap;line-height:150%'>"
            + "".join(lines)
            + "</div>"
        )

    def _open_current(self) -> None:
        row = self.results.currentRow()
        if 0 <= row < len(self._hits):
            hit = self._hits[row]
            self.open_requested.emit(hit.space_id, hit.path)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme


__all__ = ["ReadmeSearchDialog"]
