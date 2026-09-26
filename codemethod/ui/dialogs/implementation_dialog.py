"""单个语言实现的编辑对话框.

用于在主界面里直接为某个条目**新增**或**修改**一种语言的实现, 不必进入完整的
条目编辑对话框。这正是"一个问题可以用多种语言实现"最常用的入口。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...core.languages import default_filename, get_language, language_choices
from ...core.models import Entry, Implementation
from ..theme import DEFAULT_THEME, Theme
from .entry_editor import ImplementationEditor


class ImplementationDialog(QDialog):
    """新增 / 编辑条目下的**一个**语言实现。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        entry: Entry,
        implementation: Optional[Implementation] = None,
        theme: Theme = DEFAULT_THEME,
    ) -> None:
        super().__init__(parent)
        self._entry = entry
        self._theme = theme
        self._is_new = implementation is None

        if self._is_new:
            self.setWindowTitle(f"为《{entry.display_title}》添加语言实现")
        else:
            self.setWindowTitle(
                f"编辑实现 · {get_language(implementation.language).name}"
            )
        self.setMinimumSize(820, 600)
        self.resize(980, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 顶部提示: 说明这是"同一问题的第 N 种语言实现"
        used = [get_language(impl.language).name for impl in entry.active_implementations]
        hint = QLabel(self._hint_text(used), self)
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.editor = ImplementationEditor(
            self,
            implementation=implementation
            or Implementation(language=self._suggest_language(entry), code=""),
            theme=theme,
        )
        # 单个实现时不需要"关闭此实现"按钮
        self.editor.close_button.setVisible(False)
        root.addWidget(self.editor, 1)

        buttons = QDialogButtonBox(self)
        self.save_button = buttons.addButton(
            "添加实现" if self._is_new else "保存实现",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(
            self.reject
        )
        buttons.accepted.connect(self._on_accept)
        root.addWidget(buttons)

    # ----------------------------------------------------------------------------
    @staticmethod
    def _suggest_language(entry: Entry) -> str:
        """优先给出该条目还没有用过的语言。"""
        used = {impl.language for impl in entry.active_implementations}
        for lang_id, _name in language_choices():
            if lang_id not in used:
                return lang_id
        return "python"

    def _hint_text(self, used: List[str]) -> str:
        if self._is_new:
            if used:
                return (
                    f"该条目已有 {len(used)} 种语言实现: {'、'.join(used)}。"
                    "下面再添加一种 —— 同一个问题, 不同语言的解法。"
                )
            return "为这个条目添加第一种语言实现。"
        return f"该条目共有 {len(used)} 种语言实现: {'、'.join(used)}。"

    # ----------------------------------------------------------------------------
    def _on_accept(self) -> None:
        if not self.editor.code().strip():
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.question(
                self,
                "代码为空",
                "还没有填写任何代码, 确定要保存这个空实现吗?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.editor.editor.setFocus()
                return
        self.accept()

    # ----------------------------------------------------------------------------
    def result_implementation(self) -> Implementation:
        """收集编辑结果。新增时返回一个全新的 :class:`Implementation`。"""
        impl = self.editor.collect()
        if self._is_new:
            impl.id = ""  # 交给仓储分配
        return impl


__all__ = ["ImplementationDialog"]
