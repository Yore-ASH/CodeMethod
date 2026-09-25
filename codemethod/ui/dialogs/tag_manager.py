"""标签管理对话框: 重命名 / 合并 / 删除 / 改色。"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.repository import Repository
from ..theme import DEFAULT_THEME, Theme


class TagManagerDialog(QDialog):
    """标签管理中心。

    所有操作都通过 :class:`~codemethod.core.repository.Repository` 执行,
    因此"标签重命名/合并/删除"同样会进入每个受影响条目的修订历史。
    """

    changed = Signal()

    def __init__(
        self,
        repository: Repository,
        parent: Optional[QWidget] = None,
        *,
        theme: Theme = DEFAULT_THEME,
    ) -> None:
        super().__init__(parent)
        self._repo = repository
        self._theme = theme
        self.setWindowTitle("标签管理")
        self.setMinimumSize(640, 480)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        self.summary = QLabel("", self)
        self.summary.setObjectName("MutedLabel")
        root.addWidget(self.summary)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.list = QListWidget(self)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.list.itemDoubleClicked.connect(lambda _i: self.rename_selected())
        row.addWidget(self.list, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(5)
        self.rename_button = QPushButton("重命名…", self)
        self.rename_button.clicked.connect(self.rename_selected)
        self.merge_button = QPushButton("合并到…", self)
        self.merge_button.clicked.connect(self.merge_selected)
        self.color_button = QPushButton("修改颜色…", self)
        self.color_button.clicked.connect(self.recolor_selected)
        self.delete_button = QPushButton("删除标签", self)
        self.delete_button.setProperty("danger", True)
        self.delete_button.clicked.connect(self.delete_selected)
        buttons.addWidget(self.rename_button)
        buttons.addWidget(self.merge_button)
        buttons.addWidget(self.color_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        root.addLayout(row, 1)

        self.hint = QLabel(
            "说明: 重命名 / 合并 / 删除都会写入受影响条目的历史记录, 可随时回滚。",
            self,
        )
        self.hint.setObjectName("DimLabel")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        box = QDialogButtonBox(self)
        close_button = box.addButton("关闭", QDialogButtonBox.ButtonRole.AcceptRole)
        close_button.clicked.connect(self.accept)
        root.addWidget(box)

        self.refresh()

    # ----------------------------------------------------------------------------
    def refresh(self) -> None:
        usage = self._repo.tag_usage()
        self.list.clear()
        tags = self._repo.all_tags()
        for tag in tags:
            key = tag.casefold()
            item = QListWidgetItem(f"{tag}    —  {usage.get(key, 0)} 个条目")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setForeground(QColor(self._repo.tag_color(tag)))
            self.list.addItem(item)
        total = sum(usage.values())
        self.summary.setText(
            f"共 {len(tags)} 个标签, 累计被引用 {total} 次 (仅统计未删除条目)"
        )
        self._update_buttons()

    def _selected(self) -> List[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()]

    def _update_buttons(self) -> None:
        count = len(self._selected())
        self.rename_button.setEnabled(count == 1)
        self.merge_button.setEnabled(count >= 1)
        self.color_button.setEnabled(count == 1)
        self.delete_button.setEnabled(count >= 1)

    # ----------------------------------------------------------------------------
    def rename_selected(self) -> None:
        selected = self._selected()
        if len(selected) != 1:
            return
        old = selected[0]
        new, ok = QInputDialog.getText(self, "重命名标签", f"把「{old}」重命名为:", text=old)
        if not ok or not new.strip() or new.strip() == old:
            return
        try:
            affected = self._repo.rename_tag(old, new.strip())
        except Exception as exc:
            QMessageBox.warning(self, "重命名失败", str(exc))
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "完成", f"已重命名, 影响 {affected} 个条目。")

    def merge_selected(self) -> None:
        selected = self._selected()
        if not selected:
            return
        all_tags = [t for t in self._repo.all_tags() if t not in selected]
        choices = [t for t in all_tags] + ["<输入新标签名>"]
        target, ok = QInputDialog.getItem(
            self, "合并标签", f"把 {len(selected)} 个标签合并到:", choices, 0, False
        )
        if not ok:
            return
        if target == "<输入新标签名>":
            target, ok = QInputDialog.getText(self, "新标签名", "请输入合并后的标签名:")
            if not ok or not target.strip():
                return
        if target in selected:
            return
        try:
            affected = self._repo.merge_tags(selected, target)
        except Exception as exc:
            QMessageBox.warning(self, "合并失败", str(exc))
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(
            self, "完成", f"已合并 {len(selected)} 个标签到「{target}」, 影响 {affected} 个条目。"
        )

    def recolor_selected(self) -> None:
        selected = self._selected()
        if len(selected) != 1:
            return
        tag = selected[0]
        initial = QColor(self._repo.tag_color(tag))
        color = QColorDialog.getColor(initial, self, f"选择「{tag}」的颜色")
        if not color.isValid():
            return
        self._repo.set_tag_color(tag, color.name())
        self.refresh()
        self.changed.emit()

    def delete_selected(self) -> None:
        selected = self._selected()
        if not selected:
            return
        answer = QMessageBox.question(
            self,
            "确认删除",
            "将从所有条目中移除以下标签:\n\n"
            + "\n".join("• " + t for t in selected)
            + "\n\n该操作会写入历史, 可以回滚。确定继续吗?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        affected = 0
        for tag in selected:
            affected += self._repo.delete_tag(tag)
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "完成", f"已删除 {len(selected)} 个标签, 影响 {affected} 个条目。")


__all__ = ["TagManagerDialog"]
