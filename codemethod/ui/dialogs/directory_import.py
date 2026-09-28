"""整目录导入对话框: 先看清"会发生什么", 再决定要不要导.

为什么需要这个对话框
--------------------
把一整个目录 (含子目录) 打包进空间是个**破坏性**的大动作: 可能几百上千个文件、
几 MB 的图片、还有 ``.git`` 里的内部对象。闷头导进去, 用户既不知道导了什么,
也不知道为什么有些文件没了。

所以这里先跑一次 `scan_external_directory` (只读目录结构 + 每个文件前 8 KB),
把计划完整摊开给用户看:

* 一共多少文件 / 多少字节, 其中文本多少、二进制多少;
* 哪些会被跳过, **以及为什么** (缓存文件 / 超过大小上限 / 隐藏文件);
* 按目录折叠的树, 一眼看清结构;
* 空间二进制余量够不够, 不够就要显式勾「仍然导入」。

用户确认后, 主窗口直接用**同一份计划**执行导入, 不会再扫一遍。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.spaces import (
    CACHE_DIR_NAMES,
    VCS_DIR_NAMES,
    DirectoryScan,
    ImportOptions,
    human_bytes,
    normalize_project_path,
    scan_external_directory,
)
from ..native import ThemedDialog
from ..theme import DEFAULT_THEME, Theme
from ..widgets.action_button import fit_action_button


class DirectoryImportDialog(ThemedDialog):
    """选目录 → 预览计划 → 确认导入。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        theme: Theme = DEFAULT_THEME,
        directory: str = "",
        target_dir: str = "",
        budget_left: Optional[int] = None,
        import_label: str = "导入",
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._scan: Optional[DirectoryScan] = None
        self._budget_left = budget_left

        self.setWindowTitle("把整个目录打包进空间")
        self.setMinimumSize(720, 460)
        self.resize(940, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # ---- 目录选择 ----
        pick = QHBoxLayout()
        pick.setSpacing(6)
        pick.addWidget(QLabel("源目录", self))
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("要打包的整个目录 (含全部子文件夹)")
        self.path_edit.setText(directory)
        self.path_edit.textChanged.connect(lambda _t: self._rescan())
        pick.addWidget(self.path_edit, 1)
        self.browse_button = QPushButton("选择目录…", self)
        self.browse_button.clicked.connect(self._browse)
        pick.addWidget(self.browse_button)
        layout.addLayout(pick)

        # ---- 目标目录 ----
        target = QHBoxLayout()
        target.setSpacing(6)
        target.addWidget(QLabel("放到空间里的", self))
        self.target_edit = QLineEdit(self)
        self.target_edit.setPlaceholderText("留空 = 放在空间根目录; 例如 vendor/ 就会全部落到 vendor/ 下")
        self.target_edit.setText(target_dir)
        self.target_edit.textChanged.connect(lambda _t: self._rescan())
        target.addWidget(self.target_edit, 1)
        layout.addLayout(target)

        # ---- 选项 ----
        options = QHBoxLayout()
        options.setSpacing(10)
        self.recursive_check = QCheckBox("含子文件夹", self)
        self.recursive_check.setChecked(True)
        self.structure_check = QCheckBox("保留目录结构", self)
        self.structure_check.setChecked(True)
        self.structure_check.setToolTip("取消勾选会把所有文件平铺到目标目录, 同名文件会互相覆盖")
        self.hidden_check = QCheckBox("含隐藏文件", self)
        self.hidden_check.setChecked(True)
        self.hidden_check.setToolTip("例如 .gitignore / .env —— 通常是有用的项目文件")
        self.vcs_check = QCheckBox(f"含版本控制目录 ({', '.join(sorted(VCS_DIR_NAMES)[:3])} …)", self)
        self.vcs_check.setToolTip("通常不需要: .git 里是内部对象, 会让库膨胀好几倍")
        self.cache_check = QCheckBox("含缓存目录", self)
        self.cache_check.setToolTip(
            "例如 " + "、".join(sorted(CACHE_DIR_NAMES)[:6])
            + " —— 都是可以重新生成的产物\n"
            "注意: .pyc / .o / .class 这类编译产物始终会跳过 (没有保留价值)"
        )
        for box in (
            self.recursive_check,
            self.structure_check,
            self.hidden_check,
            self.vcs_check,
            self.cache_check,
        ):
            box.stateChanged.connect(lambda _s: self._rescan())
            options.addWidget(box)
        options.addStretch(1)
        layout.addLayout(options)

        # ---- 摘要 ----
        self.summary_label = QLabel("", self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.summary_label)

        self.warning_label = QLabel("", self)
        self.warning_label.setWordWrap(True)
        self.warning_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.warning_label)

        self.force_check = QCheckBox("仍然导入 (会超过空间的二进制软上限)", self)
        self.force_check.setVisible(False)
        # 勾上要立刻解禁「导入」按钮 —— 之前只有重新扫描才会重算, 用户点了没反应
        self.force_check.stateChanged.connect(lambda _s: self._sync_import_button())
        layout.addWidget(self.force_check)

        # ---- 预览树 ----
        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["文件", "大小", "说明"])
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

        # ---- 按钮 ----
        buttons = QDialogButtonBox(self)
        self.import_button = buttons.addButton(import_label, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(self.reject)
        buttons.accepted.connect(self._on_accept)
        layout.addWidget(buttons)

        for button in (self.browse_button, self.import_button):
            fit_action_button(button)

        self._rescan()

    # ----------------------------------------------------------------------------
    def options(self) -> ImportOptions:
        return ImportOptions(
            recursive=self.recursive_check.isChecked(),
            include_hidden=self.hidden_check.isChecked(),
            include_vcs=self.vcs_check.isChecked(),
            include_caches=self.cache_check.isChecked(),
            keep_structure=self.structure_check.isChecked(),
            target_dir=normalize_project_path(self.target_edit.text()),
        )

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择要打包进空间的目录", "")
        if chosen:
            self.path_edit.setText(chosen)

    def rescan(self) -> None:
        """重新扫描 (源目录内容变了之后可以手动调)。"""
        self._rescan()

    def _rescan(self) -> None:
        directory = self.path_edit.text().strip()
        if not directory:
            self._scan = None
            self.tree.clear()
            self.summary_label.setText(
                "<span style='color:#808080'>选一个目录, 这里会先列出会发生什么。</span>"
            )
            self.warning_label.setText("")
            self.force_check.setVisible(False)
            self.import_button.setEnabled(False)
            return

        self._scan = scan_external_directory(directory, self.options())
        self._render(self._scan)

    def _render(self, scan: DirectoryScan) -> None:
        self.tree.clear()
        if scan.error:
            self.summary_label.setText(f"<b style='color:#F44747'>{scan.error}</b>")
            self.warning_label.setText("")
            self.force_check.setVisible(False)
            self.import_button.setEnabled(False)
            return

        included = scan.included
        self.summary_label.setText(
            f"<b>{len(included)}</b> 个文件 · <b>{human_bytes(scan.total_bytes)}</b>"
            f" &nbsp;·&nbsp; 文本 {len(scan.text_files)} 个 ({human_bytes(scan.text_bytes)})"
            f" &nbsp;·&nbsp; 二进制 {len(scan.binary_files)} 个 ({human_bytes(scan.binary_bytes)})"
            f" &nbsp;·&nbsp; 目录 {len(scan.directories)} 个"
            + (f" &nbsp;·&nbsp; <b>跳过 {len(scan.skipped)} 个</b>" if scan.skipped else "")
        )

        warnings: List[str] = []
        if scan.skipped:
            reasons = "、".join(
                f"{reason} × {count}" for reason, count in sorted(scan.reasons().items())
            )
            warnings.append(f"跳过的文件: {reasons}")
        if scan.pruned_dirs:
            reasons = "、".join(
                f"{reason} × {count}"
                for reason, count in sorted(scan.pruned_reasons().items())
            )
            warnings.append(
                f"整个目录被跳过: {reasons}"
                " —— 想连这些一起打包就勾上上面的选项"
            )

        over_budget = False
        if self._budget_left is not None and scan.binary_bytes > self._budget_left:
            over_budget = True
            warnings.append(
                f"⚠ 这次要嵌入 <b>{human_bytes(scan.binary_bytes)}</b> 的二进制内容, "
                f"但空间只剩 <b>{human_bytes(self._budget_left)}</b>。"
                "可以减少文件, 或勾上下面的「仍然导入」。"
            )
        self.force_check.setVisible(over_budget)
        if not over_budget:
            self.force_check.setChecked(False)

        color = self._theme.warning if warnings else self._theme.text_dim
        self.warning_label.setText(
            "<br/>".join(f"<span style='color:{color}'>{w}</span>" for w in warnings)
        )
        self._sync_import_button()

        self._build_rows(scan)

    def _sync_import_button(self) -> None:
        """「导入」是否可点: 有计划、且 (没超预算 或 用户已勾选强制)。"""
        scan = self._scan
        if scan is None or scan.error or not scan.included:
            self.import_button.setEnabled(False)
            return
        over_budget = (
            self._budget_left is not None and scan.binary_bytes > self._budget_left
        )
        self.import_button.setEnabled(not over_budget or self.force_check.isChecked())

    def _build_rows(self, scan: DirectoryScan) -> None:
        """按目录折叠成树, 跳过的用警告色标出来。"""
        nodes = {}
        root = QTreeWidgetItem(self.tree)
        root.setText(0, f"（{scan.root}）")
        root.setExpanded(True)
        nodes[""] = root

        def ensure(path: str) -> QTreeWidgetItem:
            if path in nodes:
                return nodes[path]
            parent = ensure(path.rsplit("/", 1)[0] if "/" in path else "")
            item = QTreeWidgetItem(parent)
            item.setText(0, f"{path.rsplit('/', 1)[-1]}/")
            item.setExpanded(True)
            nodes[path] = item
            return item

        dim = QColor(self._theme.text_dim)
        warn = QColor(self._theme.warning)
        for planned in sorted(scan.included, key=lambda f: f.path) + sorted(
            scan.skipped, key=lambda f: f.path
        ):
            directory = planned.path.rsplit("/", 1)[0] if "/" in planned.path else ""
            parent = ensure(directory)
            item = QTreeWidgetItem(parent)
            item.setText(0, planned.name)
            item.setText(1, planned.size_label)
            if planned.skipped:
                item.setText(2, planned.reason)
                item.setForeground(1, warn)
                item.setForeground(2, warn)
                font = QFont(item.font(0))
                font.setStrikeOut(True)
                item.setFont(0, font)
                item.setForeground(0, warn)
            else:
                item.setText(2, planned.kind_label)
                item.setForeground(1, dim)
                item.setForeground(2, dim)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 整个被剪掉的目录也列出来 —— 否则用户会以为 .git "凭空消失"了
        for path, reason in sorted(scan.pruned_dirs):
            parent_path = path.rsplit("/", 1)[0] if "/" in path else ""
            parent = ensure(parent_path)
            item = QTreeWidgetItem(parent)
            item.setText(0, f"{path.rsplit('/', 1)[-1]}/")
            item.setText(1, "—")
            item.setText(2, reason + " · 已跳过")
            item.setForeground(0, warn)
            item.setForeground(1, warn)
            item.setForeground(2, warn)
            font = QFont(item.font(0))
            font.setStrikeOut(True)
            item.setFont(0, font)

    # ----------------------------------------------------------------------------
    def _on_accept(self) -> None:
        if self._scan is not None and self._scan.error:
            return
        self.accept()

    def scan(self) -> Optional[DirectoryScan]:
        """用户确认后的计划 —— 主窗口直接拿它执行, 不再重复扫描。"""
        return self._scan

    def target_dir(self) -> str:
        return normalize_project_path(self.target_edit.text())

    def force(self) -> bool:
        return self.force_check.isChecked()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme


__all__ = ["DirectoryImportDialog"]
