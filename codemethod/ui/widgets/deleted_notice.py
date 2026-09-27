"""回收站提示条: 当前对象被软删除时在详情面板顶部给出明确提示.

删除只是"移入回收站", 内容和全部历史都还在。没有提示的话, 用户看到详情面板
还在展示内容, 会以为删除没生效 —— 所以这里显式说明, 并指向恢复入口。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

TEXT = "🗑 这个对象在回收站里 —— 内容与全部实现都还在, 可以随时恢复"


class DeletedNotice(QWidget):
    """一条横贯面板顶部的提示, 带「恢复」按钮。"""

    restore_requested = Signal()
    purge_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("DeletedNotice")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        label = QLabel(TEXT, self)
        label.setWordWrap(True)
        layout.addWidget(label, 1)

        self.restore_button = QPushButton("恢复", self)
        self.restore_button.setToolTip("把对象从回收站里拿出来, 内容与历史都不变")
        self.restore_button.clicked.connect(self.restore_requested)
        layout.addWidget(self.restore_button)

        self.purge_button = QPushButton("彻底删除", self)
        self.purge_button.setProperty("flat", True)
        self.purge_button.setToolTip("永久移除对象本体 (历史记录仍会保留)")
        self.purge_button.clicked.connect(self.purge_requested)
        layout.addWidget(self.purge_button)

        self.setVisible(False)


__all__ = ["DeletedNotice", "TEXT"]
