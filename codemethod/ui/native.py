"""Windows 原生窗口装饰的主题适配.

问题
----
`QWidget.grab()` 只截客户区, 不含系统绘制的标题栏/边框。因此即使整个界面都是深色,
Windows 仍然会画一条**亮色标题栏**, 顶部看起来就是突兀的一条白框 —— 与深色主题完全不搭。

做法
----
用 DWM (桌面窗口管理器) 接口把窗口的标题栏也染成主题色:

* ``DWMWA_USE_IMMERSIVE_DARK_MODE`` (20, Win10 1809 起; 早期预览版是 19) —— 深色模式开关;
* ``DWMWA_CAPTION_COLOR`` (35) / ``DWMWA_TEXT_COLOR`` (36) / ``DWMWA_BORDER_COLOR`` (34)
  —— Win11 起可以精确指定标题栏、标题文字与边框颜色, 从而和主题完全一致。

非 Windows 平台、旧版 Windows、或调用被系统拒绝时都会**安静地退化为无操作**,
不影响程序运行。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Optional

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from .theme import DEFAULT_THEME, Theme

# DWM 窗口属性编号
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_DARK_MODE_LEGACY = 19  # Win10 1809~1903 预览版用的编号

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:  # pragma: no cover - 平台相关
    try:
        _dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - 极端情况下没有 dwmapi
        _dwmapi = None
else:  # pragma: no cover
    _dwmapi = None


def _colorref(hex_color: str) -> int:
    """Qt 的 #RRGGBB 转成 Win32 的 COLORREF (0x00BBGGRR)。"""
    color = QColor(hex_color)
    if not color.isValid():
        return 0
    return color.red() | (color.green() << 8) | (color.blue() << 16)


def _set_attribute(hwnd: int, attribute: int, value: int) -> bool:
    if _dwmapi is None:
        return False
    try:
        data = ctypes.c_uint(value)
        result = _dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(attribute),
            ctypes.byref(data),
            ctypes.sizeof(data),
        )
        return int(result) == 0
    except Exception:  # pragma: no cover - 防御
        return False


def apply_titlebar_theme(widget: QWidget, theme: Theme = DEFAULT_THEME) -> bool:
    """把 ``widget`` 的原生标题栏染成主题色, 成功返回 True。"""
    if not _IS_WINDOWS or _dwmapi is None or widget is None:
        return False
    try:
        hwnd = int(widget.winId())
    except (RuntimeError, TypeError):  # pragma: no cover - 控件已销毁
        return False
    if not hwnd:
        return False

    dark = 1 if theme.is_dark else 0
    ok = _set_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, dark)
    if not ok:
        # 老版本 Windows 用的是 19
        ok = _set_attribute(hwnd, _DWMWA_DARK_MODE_LEGACY, dark)

    # Windows 11: 精确匹配颜色; 旧系统会返回失败, 忽略即可
    _set_attribute(hwnd, DWMWA_CAPTION_COLOR, _colorref(theme.title_bar))
    _set_attribute(hwnd, DWMWA_TEXT_COLOR, _colorref(theme.text))
    _set_attribute(hwnd, DWMWA_BORDER_COLOR, _colorref(theme.window_border))
    return ok


class TitleBarThemer(QObject):
    """持有"当前主题", 并在主题切换时刷新所有已打开的顶层窗口。

    刻意**不使用应用级事件过滤器**: 那会让每一个 Qt 事件都跨界到 Python 一次,
    实测会把整份测试套件的耗时放大数倍。新窗口的着色由 :class:`ThemedDialog`
    与 ``MainWindow.showEvent`` 主动调用, 代价只在窗口创建时付一次。
    """

    def __init__(self, app: QApplication, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__(app)
        self._theme = theme
        self._active = _IS_WINDOWS and _dwmapi is not None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def theme(self) -> Theme:
        return self._theme

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        if not self._active:
            return
        app = QApplication.instance()
        if app is None:  # pragma: no cover - 防御
            return
        for widget in app.topLevelWidgets():
            self.apply(widget)

    def apply(self, widget: Optional[QWidget]) -> bool:
        if not self._active or widget is None:
            return False
        try:
            if not widget.isWindow():
                return False
        except RuntimeError:  # pragma: no cover - 控件已销毁
            return False
        return apply_titlebar_theme(widget, self._theme)


class ThemedDialog(QDialog):
    """带原生标题栏着色的对话框基类。

    所有对话框都继承它, 这样在 Windows 上不会出现"深色界面 + 白色标题栏"的割裂感。
    """

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        themer = current_themer()
        apply_titlebar_theme(self, themer.theme if themer is not None else DEFAULT_THEME)


# 应用级单例 (由 ui.theme.apply_theme 创建/复用)
_THEMER: Optional[TitleBarThemer] = None


def install_titlebar_themer(app: QApplication, theme: Theme = DEFAULT_THEME) -> TitleBarThemer:
    """安装 (或复用) 标题栏主题化器, 并套用 ``theme``。"""
    global _THEMER
    if _THEMER is None:
        _THEMER = TitleBarThemer(app, theme)
    else:
        _THEMER.set_theme(theme)
    return _THEMER


def current_themer() -> Optional[TitleBarThemer]:
    return _THEMER


__all__ = [
    "TitleBarThemer",
    "ThemedDialog",
    "apply_titlebar_theme",
    "install_titlebar_themer",
    "current_themer",
]
