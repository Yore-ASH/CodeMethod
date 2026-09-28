"""新建目录对话框: 顺带把 ``.gitkeep`` 占位文件的作用讲清楚, 并把决定权交给用户.

为什么会有 ``.gitkeep``
-----------------------
空间的容器格式里**只存文件, 不存目录** —— 目录结构是从文件路径推导出来的
(``src/util/a.py`` 隐含了 ``src/`` 与 ``src/util/``)。这是刻意的设计: 不需要额外
维护一棵树, 也就不会出现"树和文件对不上"的状态。

代价是**空目录无法表达**: 一个没有任何文件的 ``docs/`` 在库里等于不存在。
``.gitkeep`` 就是用来"钉住"这种空目录的占位文件 (Git 社区的习惯做法, 名字本身
没有特殊含义, 也可以叫 ``.keep``)。

什么时候不需要它
----------------
如果这个目录**马上就会放文件**, 占位文件就是多余的 —— 文件一进来目录自然就有
了。所以这里给了一个开关, 默认沿用你上次的选择。
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..native import ThemedDialog
from ..theme import DEFAULT_THEME, Theme

PLACEHOLDER_NAME = ".gitkeep"
SETTINGS_ORG = "CodeMethod"
SETTINGS_APP = "CodeMethod"
SETTINGS_KEY = "space/gitkeep_placeholder"


def placeholder_preference() -> bool:
    """读取"新建目录时是否自动放 .gitkeep" (默认 True, 与旧行为一致)。"""
    value = QSettings(SETTINGS_ORG, SETTINGS_APP).value(SETTINGS_KEY)
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def set_placeholder_preference(enabled: bool) -> None:
    QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(SETTINGS_KEY, bool(enabled))


EXPLANATION = (
    "容器里<b>只存文件、不存目录</b> —— 目录结构由文件路径推导出来, 所以一个"
    "没有任何文件的空目录在库里等于不存在。\n\n"
    f"勾上就会在目录里放一个空的 <code>{PLACEHOLDER_NAME}</code> 把目录「钉住」"
    "(Git 社区的习惯做法, 名字本身没有特殊含义)。\n\n"
    "<b>如果这个目录马上就会放文件, 可以不勾</b> —— 文件一进来目录自然就有了。"
    "你以后也可以随时右键删掉这个占位文件 (删掉后空目录就不再保留)。"
)


class NewDirectoryDialog(ThemedDialog):
    """输入目录路径 + 决定要不要 ``.gitkeep`` 占位。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        theme: Theme = DEFAULT_THEME,
        default: str = "src",
        already_exists=None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._already_exists = already_exists or (lambda _path: False)

        self.setWindowTitle("新建目录")
        self.setMinimumSize(460, 320)
        self.resize(560, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.path_edit = QLineEdit(self)
        self.path_edit.setText(default)
        self.path_edit.setPlaceholderText("用 / 分隔, 例如 src/utils")
        self.path_edit.textChanged.connect(self._validate)
        form.addRow("目录路径 *", self.path_edit)
        layout.addLayout(form)

        self.placeholder_check = QCheckBox(
            f"放一个 {PLACEHOLDER_NAME} 占位文件, 让这个空目录保留下来", self
        )
        self.placeholder_check.setChecked(placeholder_preference())
        layout.addWidget(self.placeholder_check)

        explain = QLabel(EXPLANATION, self)
        explain.setObjectName("DimLabel")
        explain.setWordWrap(True)
        explain.setTextFormat(Qt.TextFormat.RichText)
        explain.setContentsMargins(4, 0, 0, 0)
        layout.addWidget(explain)
        layout.addStretch(1)

        self.hint = QLabel("", self)
        self.hint.setObjectName("DimLabel")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox(self)
        self.ok_button = buttons.addButton("创建", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(
            self.reject
        )
        buttons.accepted.connect(self._on_accept)
        layout.addWidget(buttons)

        self._validate()
        self.path_edit.setFocus()
        self.path_edit.selectAll()

    # ----------------------------------------------------------------------------
    def _validate(self) -> None:
        from ...core.spaces import normalize_project_path

        path = normalize_project_path(self.path_edit.text())
        self.ok_button.setEnabled(bool(path))
        if not path:
            self.hint.setText("")
            return
        if self._already_exists(path):
            self.hint.setText(f"⚠ {path}/ 下已经有文件了, 占位文件没有必要")
            return
        if path.split("/")[-1].startswith("."):
            self.hint.setText(f"⚠ {path} 是一个隐藏目录, 会和占位文件混在一起")
            return
        self.hint.setText("")

    def _on_accept(self) -> None:
        set_placeholder_preference(self.placeholder_check.isChecked())
        self.accept()

    # ----------------------------------------------------------------------------
    def result_data(self) -> Tuple[str, bool]:
        """返回 ``(规范化的目录路径, 是否放占位文件)``。"""
        from ...core.spaces import normalize_project_path

        return normalize_project_path(self.path_edit.text()), self.placeholder_check.isChecked()


__all__ = [
    "NewDirectoryDialog",
    "PLACEHOLDER_NAME",
    "placeholder_preference",
    "set_placeholder_preference",
]
