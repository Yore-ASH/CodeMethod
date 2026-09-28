"""代码编辑 / 预览控件 (VSCode 风格).

* :class:`CodeEditor`  — 带行号、当前行高亮、括号匹配、自动缩进的编辑器;
* :class:`CodePreview` — 只读预览, 顶部有文件名/语言徽标与复制、换行、缩放按钮;
* :class:`DiffView`    — 差异查看器, 按行着色。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QPainter,
    QSyntaxHighlighter,
    QTextCursor,
    QTextFormat,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTextEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.languages import get_language, normalize_language
from .highlighter import create_highlighter
from .theme import DEFAULT_THEME, Theme, mono_font

# --------------------------------------------------------------------------------------
# 行号区
# --------------------------------------------------------------------------------------


class LineNumberArea(QWidget):
    """编辑器左侧的装订线 (行号)。"""

    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self._editor = editor
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        self._editor.paint_line_numbers(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 点击行号 -> 选中该行
        block = self._editor.firstVisibleBlock()
        y = event.position().y()
        while block.isValid():
            top = self._editor.blockBoundingGeometry(block).translated(
                self._editor.contentOffset()
            ).top()
            height = self._editor.blockBoundingRect(block).height()
            if top <= y <= top + height:
                cursor = QTextCursor(block)
                cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                self._editor.setTextCursor(cursor)
                break
            block = block.next()
        super().mousePressEvent(event)


# --------------------------------------------------------------------------------------
# 编辑器
# --------------------------------------------------------------------------------------


class CodeEditor(QPlainTextEdit):
    """带行号与语法高亮的代码编辑器。

    文件大小**不设限制**, 但语法高亮有性能护栏: 高亮器是纯 Python 正则, 对超大文档
    会卡住界面。所以文档超过 :data:`LARGE_DOCUMENT_CHARS` 或
    :data:`LARGE_DOCUMENT_BLOCKS` 时**自动降级成纯文本显示** —— 内容一个字节都不少,
    只是不再上色 (:attr:`highlight_suppressed` 为 True, 界面可以据此给出提示)。
    """

    TAB_WIDTH = 4
    _BRACKETS = {"(": ")", "[": "]", "{": "}", ")": "(", "]": "[", "}": "{"}

    # 超过任一条就关掉语法高亮 (只影响上色, 不影响内容)
    LARGE_DOCUMENT_CHARS = 1_200_000
    LARGE_DOCUMENT_BLOCKS = 30_000

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        language: str = "python",
        theme: Theme = DEFAULT_THEME,
        read_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._language = normalize_language(language)
        self._line_number_area = LineNumberArea(self)
        # 是否因为文档太大而主动关掉了语法高亮
        self.highlight_suppressed = False
        self._highlighter: QSyntaxHighlighter = self._make_highlighter()

        self.setReadOnly(read_only)
        self.setFont(mono_font(theme.mono_font_size))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(
            QFontMetricsF(self.font()).horizontalAdvance(" ") * self.TAB_WIDTH
        )
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCursorWidth(2)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {theme.editor}; color: {theme.text};"
            f" border: none; selection-background-color: {theme.editor_selection}; }}"
        )

        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._on_cursor_moved)

        self._update_line_number_area_width(0)
        self._highlight_current_line()

    # ---- 语言 / 主题 ----
    @property
    def language(self) -> str:
        return self._language

    def _is_large(self, chars: int, blocks: int) -> bool:
        return chars > self.LARGE_DOCUMENT_CHARS or blocks > self.LARGE_DOCUMENT_BLOCKS

    def _apply_highlight_guard(self, *, chars: Optional[int] = None, blocks: Optional[int] = None) -> None:
        """按(即将成为的)文档大小决定要不要挂语法高亮器。

        关键是**必须在内容进去之前**决定 —— 否则高亮器会先把几十万个 block 全跑一遍,
        界面直接卡死十几秒。
        """
        if chars is None or blocks is None:
            document = self.document()
            chars = document.characterCount()
            blocks = document.blockCount()
        large = self._is_large(int(chars), int(blocks))
        if large == self.highlight_suppressed:
            return
        self.highlight_suppressed = large
        # 先摘掉旧高亮器, 再决定挂不挂新的: 超大文档干脆不挂, 省掉每个 block 的一次回调
        self._highlighter.setDocument(None)
        if not large:
            self._highlighter = create_highlighter(self.document(), self._language, self._theme)

    def _make_highlighter(self) -> QSyntaxHighlighter:
        """构造时的初始高亮器 (此时文档还是空的, 一定不需要降级)。"""
        self.highlight_suppressed = False
        return create_highlighter(self.document(), self._language, self._theme)

    def refresh_highlighting(self) -> None:
        """文档换掉之后重算一次护栏 (超限就降级, 变小了再恢复)。"""
        self._apply_highlight_guard()

    def set_language(self, language: str) -> None:
        normalized = normalize_language(language)
        if normalized == self._language:
            return
        self._language = normalized
        # 语言变化时高亮器类型可能变化 (纯文本 <-> 语法高亮), 因此重建
        self._highlighter.setDocument(None)
        if not self.highlight_suppressed:
            self._highlighter = create_highlighter(self.document(), self._language, self._theme)

    def setPlainText(self, text: str) -> None:  # noqa: N802 - Qt 命名
        body = text or ""
        # 先按"即将变成的规模"决定高亮, 再真正塞内容
        self._apply_highlight_guard(chars=len(body), blocks=body.count("\n") + 1)
        super().setPlainText(body)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {theme.editor}; color: {theme.text};"
            f" border: none; selection-background-color: {theme.editor_selection}; }}"
        )
        if hasattr(self._highlighter, "set_theme"):
            self._highlighter.set_theme(theme)  # type: ignore[attr-defined]
        self._highlight_current_line()

    def set_font_size(self, size: int) -> None:
        size = max(6, min(40, size))
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)
        self.setTabStopDistance(QFontMetricsF(font).horizontalAdvance(" ") * self.TAB_WIDTH)
        self._update_line_number_area_width(0)

    def font_size(self) -> int:
        return self.font().pointSize()

    # ---- 行号 ----
    def line_number_area_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 12 + QFontMetricsF(self.font()).horizontalAdvance("9") * digits

    def _update_line_number_area_width(self, _count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(
                0, rect.y(), self._line_number_area.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor(self._theme.editor_gutter))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        offset = self.contentOffset()
        top = self.blockBoundingGeometry(block).translated(offset).top()
        bottom = top + self.blockBoundingRect(block).height()
        height = QFontMetricsF(self.font()).height()
        current_line = self.textCursor().blockNumber()

        painter.setFont(self.font())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                is_current = block_number == current_line
                painter.setPen(
                    QColor(self._theme.text if is_current else self._theme.text_dim)
                )
                painter.drawText(
                    0,
                    int(top),
                    self._line_number_area.width() - 8,
                    int(height),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

    # ---- 当前行 / 括号匹配 ----
    def _on_cursor_moved(self) -> None:
        self._refresh_extra_selections()

    def _refresh_extra_selections(self) -> None:
        """重建附加选区: 当前行高亮 + 括号匹配高亮。"""
        selections: List[QTextEdit.ExtraSelection] = []

        if not self.isReadOnly():
            current_line = QTextEdit.ExtraSelection()
            current_line.format.setBackground(QColor(self._theme.editor_current_line))
            current_line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            current_line.cursor = self.textCursor()
            current_line.cursor.clearSelection()
            selections.append(current_line)

        pair = self._bracket_pair()
        if pair:
            for index in pair:
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(QColor(self._theme.accent_dim))
                sel.format.setForeground(QColor(self._theme.text_bright))
                sel.cursor = QTextCursor(self.document())
                sel.cursor.setPosition(index)
                sel.cursor.movePosition(
                    QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor
                )
                selections.append(sel)

        self.setExtraSelections(selections)

    def _highlight_current_line(self) -> None:
        """兼容旧调用点。"""
        self._refresh_extra_selections()

    def _bracket_pair(self) -> Optional[Tuple[int, int]]:
        """光标处(或其左侧)括号的配对位置。"""
        text = self.toPlainText()
        if not text:
            return None
        position = self.textCursor().position()
        for probe in (position, position - 1):
            if 0 <= probe < len(text) and text[probe] in self._BRACKETS:
                mate = self._find_mate(text, probe)
                if mate is not None:
                    return probe, mate
        return None

    def _find_mate(self, text: str, index: int, *, limit: int = 20000) -> Optional[int]:
        opener = text[index]
        closer = self._BRACKETS[opener]
        forward = opener in "([{"
        depth = 0
        step = 1 if forward else -1
        cursor = index
        scanned = 0
        while 0 <= cursor < len(text) and scanned < limit:
            char = text[cursor]
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return cursor
            cursor += step
            scanned += 1
        return None

    # ---- 输入辅助 ----
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Tab and not event.modifiers():
            self._indent_selection()
            return
        if event.key() == Qt.Key.Key_Backtab:
            self._unindent_selection()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._auto_indent()
            return
        if event.key() == Qt.Key.Key_Home and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            # 智能 Home: 先跳到首个非空白字符
            block_text = self.textCursor().block().text()
            first = len(block_text) - len(block_text.lstrip())
            column = self.textCursor().positionInBlock()
            if column != first:
                cursor = self.textCursor()
                cursor.setPosition(cursor.block().position() + first)
                self.setTextCursor(cursor)
                return
        super().keyPressEvent(event)

    def _indent_selection(self) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor.insertText(" " * self.TAB_WIDTH)
            return
        self._shift_selection(indent=True)

    def _unindent_selection(self) -> None:
        self._shift_selection(indent=False)

    def _shift_selection(self, *, indent: bool) -> None:
        cursor = self.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        cursor.setPosition(start)
        first_block = cursor.blockNumber()
        cursor.setPosition(end)
        last_block = cursor.blockNumber()

        cursor.beginEditBlock()
        for number in range(first_block, last_block + 1):
            block = self.document().findBlockByNumber(number)
            if not block.isValid():
                continue
            editor_cursor = QTextCursor(block)
            if indent:
                editor_cursor.insertText(" " * self.TAB_WIDTH)
            else:
                text = block.text()
                strip = 0
                while strip < self.TAB_WIDTH and strip < len(text) and text[strip] == " ":
                    strip += 1
                if strip:
                    for _ in range(strip):
                        editor_cursor.deleteChar()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _auto_indent(self) -> None:
        cursor = self.textCursor()
        block_text = cursor.block().text()
        indent = len(block_text) - len(block_text.lstrip(" "))
        stripped = block_text.rstrip()
        extra = 1 if stripped.endswith((":", "{", "(", "[")) else 0
        cursor.insertText("\n" + " " * (indent + extra * self.TAB_WIDTH))
        # 若紧跟着闭合符号, 自动再补一行
        position = cursor.position()
        tail = self.toPlainText()[position : position + 1]
        if extra and tail and tail in ")]}":
            cursor.insertText(" " * indent)
            cursor.setPosition(position)
            self.setTextCursor(cursor)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            self.set_font_size(self.font_size() + (1 if delta > 0 else -1))
            event.accept()
            return
        super().wheelEvent(event)

    # ---- 便捷操作 ----
    def current_line_number(self) -> int:
        return self.textCursor().blockNumber() + 1

    def total_lines(self) -> int:
        return self.blockCount()

    def copy_all(self) -> str:
        text = self.toPlainText()
        QApplication.clipboard().setText(text)
        return text

    def goto_line(self, line: int) -> None:
        block = self.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            cursor = QTextCursor(block)
            self.setTextCursor(cursor)
            self.centerCursor()


# --------------------------------------------------------------------------------------
# 预览控件
# --------------------------------------------------------------------------------------


class _IconButton(QToolButton):
    """紧凑的图标/文字按钮。"""

    def __init__(self, text: str, tooltip: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setToolTip(tooltip)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class CodePreview(QWidget):
    """只读代码预览: 顶部工具条 (语言徽标 + 操作按钮) + 带高亮的代码区。"""

    copy_requested = Signal(str)
    edit_requested = Signal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        language: str = "python",
        theme: Theme = DEFAULT_THEME,
        show_toolbar: bool = True,
        editable: bool = False,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._language = normalize_language(language)
        self._filename = ""
        self.setObjectName("CodePreview")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.editor = CodeEditor(self, language=self._language, theme=theme, read_only=not editable)
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.editor.setPlaceholderText("（无代码）")

        self._header: Optional[QWidget] = None
        if show_toolbar:
            self._header = self._build_header()
            layout.addWidget(self._header)
        layout.addWidget(self.editor, 1)

        self.editor.cursorPositionChanged.connect(self._update_position_label)

    # ---- 头部 ----
    def _build_header(self) -> QWidget:
        header = QWidget(self)
        header.setObjectName("CodeHeader")
        header.setFixedHeight(30)
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 0, 6, 0)
        row.setSpacing(6)

        self._lang_label = QLabel(get_language(self._language).name, header)
        self._lang_label.setObjectName("MutedLabel")
        self._file_label = QLabel("", header)
        self._file_label.setObjectName("MutedLabel")
        self._position_label = QLabel("", header)
        self._position_label.setObjectName("DimLabel")
        # 文档太大时高亮会降级 —— 明确告诉用户"内容没少, 只是没上色"
        self._highlight_notice = QLabel("", header)
        self._highlight_notice.setObjectName("DimLabel")
        self._highlight_notice.setToolTip(
            "这个文件太大, 语法高亮会影响流畅度, 已自动改为纯文本显示。\n"
            "内容一个字节都不会少, 只是不再上色。"
        )

        self._copy_button = _IconButton("复制", "复制全部代码到剪贴板 (Ctrl+Shift+C)", header)
        self._copy_button.clicked.connect(self._on_copy)
        self._wrap_button = _IconButton("换行", "切换自动换行", header)
        self._wrap_button.setCheckable(True)
        self._wrap_button.toggled.connect(self._on_wrap_toggled)
        self._zoom_out_button = _IconButton("A-", "减小字号", header)
        self._zoom_out_button.clicked.connect(lambda: self.editor.set_font_size(self.editor.font_size() - 1))
        self._zoom_in_button = _IconButton("A+", "增大字号", header)
        self._zoom_in_button.clicked.connect(lambda: self.editor.set_font_size(self.editor.font_size() + 1))
        self._edit_button = _IconButton("编辑", "编辑此实现", header)
        self._edit_button.clicked.connect(self.edit_requested.emit)
        self._edit_button.setVisible(False)

        row.addWidget(self._lang_label)
        row.addWidget(self._file_label)
        row.addWidget(self._highlight_notice)
        row.addStretch(1)
        row.addWidget(self._position_label)
        row.addWidget(self._copy_button)
        row.addWidget(self._wrap_button)
        row.addWidget(self._zoom_out_button)
        row.addWidget(self._zoom_in_button)
        row.addWidget(self._edit_button)
        return header

    def set_editable(self, editable: bool) -> None:
        self.editor.setReadOnly(not editable)
        if self._header is not None:
            self._edit_button.setVisible(not editable)

    def set_filename(self, filename: str) -> None:
        self._filename = filename or ""
        if self._header is not None:
            self._file_label.setText(f"— {self._filename}" if self._filename else "")

    def _on_copy(self) -> None:
        text = self.editor.copy_all()
        self.copy_requested.emit(text)

    def _on_wrap_toggled(self, checked: bool) -> None:
        self.editor.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if checked
            else QPlainTextEdit.LineWrapMode.NoWrap
        )

    def _update_position_label(self) -> None:
        if self._header is None:
            return
        self._position_label.setText(
            f"行 {self.editor.current_line_number()} / {self.editor.total_lines()}"
        )

    # ---- 内容 ----
    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> None:
        self._language = normalize_language(language)
        self.editor.set_language(self._language)
        if self._header is not None:
            self._lang_label.setText(get_language(self._language).name)

    def set_code(self, code: str, *, language: Optional[str] = None, filename: str = "") -> None:
        if language is not None:
            self.set_language(language)
        self.set_filename(filename)
        self.editor.setPlainText(code or "")
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)
        self._update_position_label()
        self._update_highlight_notice()

    def _update_highlight_notice(self) -> None:
        if self._header is None:
            return
        self._highlight_notice.setText(
            "⚠ 文件较大, 已关闭语法高亮" if self.editor.highlight_suppressed else ""
        )

    @property
    def highlight_suppressed(self) -> bool:
        return self.editor.highlight_suppressed

    def code(self) -> str:
        return self.editor.toPlainText()


# --------------------------------------------------------------------------------------
# 差异查看
# --------------------------------------------------------------------------------------


class DiffView(QPlainTextEdit):
    """只读差异视图, 按行着色 (新增/删除/区块头)。"""

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setReadOnly(True)
        self.setFont(mono_font(theme.mono_font_size))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("DiffView")
        self.setPlaceholderText("选择一条历史记录查看差异")
        self._apply_style()

    def _apply_style(self) -> None:
        """QPlainTextEdit 的视口背景需要控件级样式表才会可靠重绘 (切换主题时尤其明显)。"""
        theme = self._theme
        self.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {theme.editor}; color: {theme.text};"
            f" border: none; selection-background-color: {theme.editor_selection};"
            f" selection-color: {theme.text_bright}; }}"
        )

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setFont(mono_font(theme.mono_font_size))
        self._apply_style()
        self._apply_line_colors()

    def set_diff(self, diff_text: str, *, language: str = "plaintext") -> None:
        self.setPlainText(diff_text or "")
        self._apply_line_colors()
        self.moveCursor(QTextCursor.MoveOperation.Start)

    def _apply_line_colors(self) -> None:
        selections: List[QTextEdit.ExtraSelection] = []
        document = self.document()
        block = document.firstBlock()
        add_bg = QColor(self._theme.diff_add_bg)
        del_bg = QColor(self._theme.diff_del_bg)
        hunk_bg = QColor(self._theme.diff_hunk_bg)
        while block.isValid():
            text = block.text()
            color: Optional[QColor] = None
            if text.startswith("###") or text.startswith("+++") or text.startswith("---"):
                color = hunk_bg
            elif text.startswith("@@"):
                color = hunk_bg
            elif text.startswith("+"):
                color = add_bg
            elif text.startswith("-"):
                color = del_bg
            if color is not None:
                selection = QTextEdit.ExtraSelection()
                selection.format.setBackground(color)
                selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                selection.cursor = QTextCursor(block)
                selection.cursor.clearSelection()
                selections.append(selection)
            block = block.next()
        self.setExtraSelections(selections)


__all__ = ["CodeEditor", "CodePreview", "DiffView", "LineNumberArea"]
