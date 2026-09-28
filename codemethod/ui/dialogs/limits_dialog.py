"""存储限制设置对话框.

为什么会有这个对话框
--------------------
早期版本给空间文件**硬编码**了上限 (文本 2 MB / 二进制 4 MB / 单空间二进制 32 MB /
单空间 2000 个文件), 拦下来的时候只说"超过上限", 用户既不知道为什么, 也没法改。

现在默认**全部不限制**, 这个对话框只做两件事:

1. 把每一项的**代价**讲清楚, 让用户在需要时自己决定;
2. 提供一个「恢复推荐值」按钮, 库真的大到卡了可以一键套用。

限制值存在代码库里 (``.cmdb`` 的 ``limits`` 键), 所以换台机器也带着走。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.repository import Repository
from ...core.spaces import SUGGESTED_LIMITS, StorageLimits, human_bytes
from ..native import ThemedDialog
from ..theme import DEFAULT_THEME, Theme

# (字段名, 标签, 单位是 MB 还是个数, 代价说明)
FIELDS = (
    (
        "max_text_bytes",
        "单个文本文件",
        "size",
        "文本文件的内容会进内存、进全文检索索引、进编辑器。超大文本不会让程序出错, "
        "但会让检索索引变重、保存变慢。",
    ),
    (
        "max_binary_bytes",
        "单个二进制文件",
        "size",
        "二进制内容以 base64 存在库里 (体积约 1.33 倍, zlib 后接近原大小)。"
        "图片/压缩包这类已经压过的数据基本压不动。",
    ),
    (
        "max_space_binary_bytes",
        "单个空间嵌入的二进制总量",
        "size",
        "这是最影响库体积的一项。修订历史是**全量快照** —— 同一个大文件改 10 次, "
        "库里就留 10 份。",
    ),
    (
        "max_files_per_space",
        "单个空间的文件数量",
        "count",
        "文件多本身不占多少空间, 但会让项目树、语言占比、全文检索的每次刷新变慢。",
    ),
)


class LimitsDialog(ThemedDialog):
    """查看 / 修改当前代码库的存储限制。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        repository: Repository,
        theme: Theme = DEFAULT_THEME,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._repo = repository

        self.setWindowTitle("存储限制")
        self.setMinimumSize(560, 420)
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        intro = QLabel(
            "这些限制<b>默认全部关闭</b> —— 想塞多大的文件都可以。\n"
            "只有当你觉得库太大、界面变慢时, 才有必要在这里设一个上限。"
            "限制值会跟着这个 <code>.cmdb</code> 一起保存。",
            self,
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        stats = repository.statistics()
        total_bytes = stats["space_bytes"] + stats.get("code_chars", 0)
        layout.addWidget(self._dim_label(
            f"当前库: {stats['spaces']} 个空间 · {stats['space_files']} 个文件 · "
            f"模块代码 {stats['code_lines']} 行 · "
            f"嵌入二进制 {human_bytes(stats.get('space_embedded_binary_bytes', 0) or 0)} · "
            f"共 {stats['revisions']} 条修订"
        ))

        self._rows = {}
        group = QGroupBox("限制 (留空 / 勾上「不限制」= 不检查)", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        for field, label, unit, reason in FIELDS:
            form.addRow(f"{label}", self._build_row(field, unit, reason, form, group))
        layout.addWidget(group)

        layout.addWidget(self._dim_label(
            "说明: 超过限制的文件**不会**被静默丢弃 —— 导入对话框会把它划掉并写明原因。"
        ))
        layout.addStretch(1)

        buttons = QDialogButtonBox(self)
        self.suggest_button = QPushButton("套用推荐值", self)
        self.suggest_button.setToolTip(
            f"文本 {human_bytes(SUGGESTED_LIMITS.max_text_bytes)} · "
            f"二进制 {human_bytes(SUGGESTED_LIMITS.max_binary_bytes)} · "
            f"空间二进制 {human_bytes(SUGGESTED_LIMITS.max_space_binary_bytes)} · "
            f"{SUGGESTED_LIMITS.max_files_per_space} 个文件"
        )
        self.suggest_button.clicked.connect(self._apply_suggested)
        buttons.addButton(self.suggest_button, QDialogButtonBox.ButtonRole.ResetRole)

        self.unlimited_button = QPushButton("全部不限制", self)
        self.unlimited_button.clicked.connect(self._apply_unlimited)
        buttons.addButton(self.unlimited_button, QDialogButtonBox.ButtonRole.ResetRole)

        buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(
            self.reject
        )
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._load(repository.limits)

    # ----------------------------------------------------------------------------
    def _dim_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("DimLabel")
        label.setWordWrap(True)
        return label

    def _build_row(self, field, unit, reason, form, parent) -> QWidget:
        row = QWidget(parent)
        outer = QVBoxLayout(row)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        line = QHBoxLayout()
        line.setSpacing(8)
        unlimited = QCheckBox("不限制", row)
        unlimited.setChecked(True)
        spinner = QDoubleSpinBox(row)
        if unit == "size":
            spinner.setRange(0.0, 1024.0 * 1024.0)      # 到 1 TB
            spinner.setDecimals(1)
            spinner.setSuffix(" MB")
            spinner.setSingleStep(1.0)
            spinner.setValue(2.0)
        else:
            spinner.setRange(0.0, 10_000_000.0)
            spinner.setDecimals(0)
            spinner.setSuffix(" 个")
            spinner.setSingleStep(100.0)
            spinner.setValue(2000.0)
        spinner.setEnabled(False)

        def toggle(checked: bool, spin=spinner) -> None:
            spin.setEnabled(not checked)

        unlimited.toggled.connect(toggle)
        line.addWidget(unlimited)
        line.addWidget(spinner, 1)
        outer.addLayout(line)

        hint = QLabel(reason, row)
        hint.setObjectName("DimLabel")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self._rows[field] = (unlimited, spinner, unit)
        return row

    # ----------------------------------------------------------------------------
    def _load(self, limits: StorageLimits) -> None:
        for field, (unlimited, spinner, unit) in self._rows.items():
            value = getattr(limits, field)
            unlimited.setChecked(not value)
            if value:
                spinner.setValue(
                    value / (1024 * 1024) if unit == "size" else float(value)
                )

    def _apply_suggested(self) -> None:
        self._load(SUGGESTED_LIMITS)

    def _apply_unlimited(self) -> None:
        self._load(StorageLimits())

    def result_limits(self) -> StorageLimits:
        values = {}
        for field, (unlimited, spinner, unit) in self._rows.items():
            if unlimited.isChecked():
                values[field] = 0
            else:
                amount = float(spinner.value())
                values[field] = int(amount * 1024 * 1024) if unit == "size" else int(amount)
        return StorageLimits(**values)


__all__ = ["LimitsDialog", "FIELDS"]
