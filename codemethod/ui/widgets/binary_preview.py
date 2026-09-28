"""二进制文件预览: 图片直接显示, 其它类型给十六进制 + 摘要.

空间里的二进制文件现在是**真的嵌在 ``.cmdb`` 里**的 (base64), 所以这里可以:

* 图片 (png/jpg/gif/bmp/webp/ico) → 直接渲染出来;
* 其它类型 → 十六进制 dump + SHA-256 + 大小;
* 提供「导出到磁盘…」「复制十六进制」「重新导入…」三个操作。

旧版本保存的二进制文件只有大小没有内容, 这里会明确提示"未嵌入内容"并引导重新导入。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.spaces import HEX_PREVIEW_BYTES, ProjectFile, human_bytes
from ..editor import CodeEditor
from ..theme import DEFAULT_THEME, Theme
from .action_button import fit_action_button

NOT_EMBEDDED_HINT = (
    "这个二进制文件只有大小记录 (旧版本保存的库), 内容没有嵌进代码库。\n"
    "点「重新导入…」从磁盘再选一次, 内容就会被完整嵌入。"
)


class BinaryPreview(QWidget):
    """只读的二进制文件预览器。"""

    export_requested = Signal()
    import_requested = Signal()
    copy_done = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self._theme = theme
        self._file: Optional[ProjectFile] = None
        self.setObjectName("BinaryPreview")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_bar())

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_image_page())
        self.stack.addWidget(self._build_hex_page())
        layout.addWidget(self.stack, 1)

    # ----------------------------------------------------------------------------
    def _build_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("CodeHeader")
        bar.setFixedHeight(34)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 0, 8, 0)
        row.setSpacing(8)

        self.kind_label = QLabel("二进制", bar)
        self.kind_label.setObjectName("MutedLabel")
        row.addWidget(self.kind_label)
        self.meta_label = QLabel("", bar)
        self.meta_label.setObjectName("DimLabel")
        row.addWidget(self.meta_label)
        row.addStretch(1)

        self.copy_button = QPushButton("复制十六进制", bar)
        self.copy_button.setProperty("flat", True)
        self.copy_button.clicked.connect(self._copy_hex)
        self.export_button = QPushButton("导出到磁盘…", bar)
        self.export_button.setProperty("flat", True)
        self.export_button.clicked.connect(self.export_requested)
        self.import_button = QPushButton("重新导入…", bar)
        self.import_button.setProperty("flat", True)
        self.import_button.setToolTip("从磁盘重新选一个文件, 内容会被嵌进代码库 (不是引用)")
        self.import_button.clicked.connect(self.import_requested)
        for button in (self.copy_button, self.export_button, self.import_button):
            fit_action_button(button)
            row.addWidget(button)
        return bar

    def _build_image_page(self) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label = QLabel(scroll)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        scroll.setWidget(self.image_label)
        self._image_scroll = scroll
        return scroll

    def _build_hex_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.hex_editor = CodeEditor(page, language="plaintext", theme=self._theme, read_only=True)
        self.hex_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.hex_editor)
        self.hint_label = QLabel("", page)
        self.hint_label.setObjectName("DimLabel")
        self.hint_label.setWordWrap(True)
        self.hint_label.setContentsMargins(10, 4, 10, 6)
        layout.addWidget(self.hint_label)
        return page

    # ----------------------------------------------------------------------------
    def set_file(self, file: Optional[ProjectFile]) -> None:
        self._file = file
        if file is None or not file.binary:
            self.kind_label.setText("（未选择二进制文件）")
            self.meta_label.setText("")
            self.hex_editor.setPlainText("")
            self.image_label.clear()
            self.hint_label.setText("")
            self._set_buttons_enabled(False, False)
            self.stack.setCurrentIndex(1)
            return

        raw = file.raw_bytes()
        self.kind_label.setText(f"二进制 · {file.extension.upper() or '未知类型'}")
        pieces = [file.size_label]
        if raw:
            pieces.append(f"已嵌入 · sha256 {file.sha256()[:12]}")
        else:
            pieces.append("未嵌入内容")
        pieces.append(file.path)
        self.meta_label.setText(" · ".join(pieces))

        self._set_buttons_enabled(bool(raw), True)

        if raw and file.is_image:
            pixmap = QPixmap()
            if pixmap.loadFromData(raw):
                self.image_label.setPixmap(
                    pixmap.scaled(
                        900,
                        900,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.image_label.setToolTip(f"{pixmap.width()} × {pixmap.height()} 像素")
                self.stack.setCurrentIndex(0)
                return

        # 十六进制
        if raw:
            self.hex_editor.setPlainText(file.hex_preview())
            more = (
                f"（只显示前 {human_bytes(HEX_PREVIEW_BYTES)}, 共 {file.size_label}）"
                if len(raw) > HEX_PREVIEW_BYTES
                else f"（完整内容, {file.size_label}）"
            )
            self.hint_label.setText(more + "  内容已嵌进代码库, 导出 ZIP 时会还原成原始字节。")
        else:
            self.hex_editor.setPlainText(NOT_EMBEDDED_HINT)
            self.hint_label.setText("旧格式: 只有大小, 没有内容。")
        self.stack.setCurrentIndex(1)

    def _set_buttons_enabled(self, has_data: bool, is_binary: bool) -> None:
        self.copy_button.setEnabled(has_data)
        self.export_button.setEnabled(has_data)
        self.import_button.setEnabled(is_binary)

    def _copy_hex(self) -> None:
        if self._file is None:
            return
        raw = self._file.raw_bytes()
        if not raw:
            self.copy_done.emit("这个文件没有嵌入内容, 无法复制")
            return
        text = self._file.hex_preview()
        QApplication.clipboard().setText(text)
        self.copy_done.emit(f"已复制 {self._file.path} 的十六进制预览到剪贴板")

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.hex_editor.set_theme(theme)
        for button in (self.copy_button, self.export_button, self.import_button):
            fit_action_button(button)
        if self._file is not None:
            self.set_file(self._file)


__all__ = ["BinaryPreview", "NOT_EMBEDDED_HINT"]
