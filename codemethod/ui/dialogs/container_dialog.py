"""容器信息 / 完整性校验 结果对话框。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...storage.container import ContainerInfo, human_size
from ..native import ThemedDialog
from ..theme import DEFAULT_THEME, Theme


class ContainerInfoDialog(ThemedDialog):
    """展示 ``.cmdb`` / ``.cmj`` 容器的元信息。"""

    def __init__(
        self,
        info: ContainerInfo,
        parent: Optional[QWidget] = None,
        *,
        theme: Theme = DEFAULT_THEME,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("容器信息")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        manifest = info.manifest or {}
        stats = manifest.get("stats") or {}
        integrity = manifest.get("integrity") or {}

        rows = [
            ("文件", info.path),
            ("格式", info.kind.label),
            ("格式版本", info.version),
            ("标记位", f"0x{info.flags:04X}"),
            ("文件大小", human_size(info.file_size)),
            ("载荷大小", human_size(info.payload_len)),
            ("原始大小", human_size(info.payload_raw)),
            ("压缩率", f"{info.ratio:.1%}"),
            ("条目数", str(stats.get("entries", "-"))),
            ("实现数", str(stats.get("implementations", "-"))),
            ("修订数", str(stats.get("revisions", "-"))),
            ("标签数", str(stats.get("tags", "-"))),
            ("语言", ", ".join(stats.get("languages") or []) or "-"),
            ("SHA-256", integrity.get("digest", "-")),
        ]
        for label, value in rows:
            value_label = QLabel(str(value), self)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setWordWrap(True)
            form.addRow(label + ":", value_label)
        root.addLayout(form)

        if info.warnings:
            warning_title = QLabel("警告", self)
            warning_title.setObjectName("SectionLabel")
            root.addWidget(warning_title)
            warnings = QPlainTextEdit(self)
            warnings.setReadOnly(True)
            warnings.setPlainText("\n".join("• " + w for w in info.warnings))
            warnings.setFixedHeight(80)
            root.addWidget(warnings)

        box = QDialogButtonBox(self)
        box.addButton("关闭", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(self.accept)
        root.addWidget(box)


class VerifyResultDialog(ThemedDialog):
    """完整性校验结果。"""

    def __init__(
        self,
        path: str,
        ok: bool,
        problems: List[str],
        info: Optional[ContainerInfo] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("完整性校验")
        self.setMinimumSize(560, 360)

        root = QVBoxLayout(self)
        headline = QLabel(
            ("✔ 校验通过: " if ok else "✘ 校验未通过: ") + path, self
        )
        headline.setWordWrap(True)
        headline.setStyleSheet(
            "font-weight: bold; color: %s;" % ("#4EC9B0" if ok else "#F14C4C")
        )
        root.addWidget(headline)

        body = QPlainTextEdit(self)
        body.setReadOnly(True)
        lines: List[str] = []
        if info is not None:
            lines.append(info.describe())
            lines.append("")
        if problems:
            lines.append("发现的问题:")
            lines.extend("  • " + p for p in problems)
        elif ok:
            lines.append("未发现任何问题。头部、载荷 CRC32、清单与 SHA-256 摘要均一致。")
        body.setPlainText("\n".join(lines))
        root.addWidget(body, 1)

        box = QDialogButtonBox(self)
        box.addButton("关闭", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(self.accept)
        root.addWidget(box)


__all__ = ["ContainerInfoDialog", "VerifyResultDialog"]
