"""界面层: VSCode 风格主题、语法高亮、编辑器、部件与主窗口."""

from __future__ import annotations

from .editor import CodeEditor, CodePreview, DiffView
from .highlighter import CodeHighlighter, create_highlighter, highlighted_tokens
from .main_window import MainWindow
from .resources import app_icon
from .theme import DARK_PLUS, LIGHT, THEMES, Theme, apply_theme, get_theme, mono_font, ui_font

__all__ = [
    "CodeEditor",
    "CodePreview",
    "DiffView",
    "CodeHighlighter",
    "create_highlighter",
    "highlighted_tokens",
    "MainWindow",
    "app_icon",
    "DARK_PLUS",
    "LIGHT",
    "THEMES",
    "Theme",
    "apply_theme",
    "get_theme",
    "mono_font",
    "ui_font",
]
