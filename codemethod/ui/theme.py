"""VSCode Dark+ 风格的配色与样式表.

界面颜色、语法高亮 token 颜色、字体都集中在这里, 便于整体换肤。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# --------------------------------------------------------------------------------------
# 调色板 (取自 VSCode Dark+ / Dark Modern)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """一套完整的界面配色。"""

    key: str = "dark+"
    label: str = "Dark+ (默认)"

    # 窗口与容器
    window: str = "#1E1E1E"
    window_border: str = "#3C3C3C"
    sidebar: str = "#252526"
    sidebar_section: str = "#2D2D30"
    activity_bar: str = "#333333"
    title_bar: str = "#3C3C3C"
    panel: str = "#1E1E1E"
    panel_border: str = "#3C3C3C"
    status_bar: str = "#007ACC"
    status_bar_text: str = "#FFFFFF"
    editor: str = "#1E1E1E"
    editor_gutter: str = "#1E1E1E"
    editor_current_line: str = "#2A2D2E"
    editor_selection: str = "#264F78"
    editor_indent_guide: str = "#404040"
    tab_active: str = "#1E1E1E"
    tab_inactive: str = "#2D2D2D"
    menu: str = "#252526"
    tooltip: str = "#252526"

    # 文本
    text: str = "#D4D4D4"
    text_muted: str = "#858585"
    text_dim: str = "#6A6A6A"
    text_bright: str = "#FFFFFF"

    # 交互
    accent: str = "#007ACC"
    accent_hover: str = "#1177BB"
    accent_dim: str = "#04395E"
    button: str = "#0E639C"
    button_hover: str = "#1177BB"
    input_bg: str = "#3C3C3C"
    input_border: str = "#3C3C3C"
    input_focus_border: str = "#007FD4"
    list_hover: str = "#2A2D2E"
    list_selected: str = "#04395E"
    list_selected_inactive: str = "#37373D"
    scrollbar: str = "#424242"
    scrollbar_hover: str = "#4F4F4F"
    border: str = "#3C3C3C"
    divider: str = "#474747"

    # 语义色
    success: str = "#4EC9B0"
    warning: str = "#DCDCAA"
    danger: str = "#F14C4C"
    info: str = "#569CD6"

    # 语法 token (VSCode Dark+ 默认)
    tok_comment: str = "#6A9955"
    tok_keyword: str = "#569CD6"
    tok_control: str = "#C586C0"
    tok_type: str = "#4EC9B0"
    tok_function: str = "#DCDCAA"
    tok_string: str = "#CE9178"
    tok_number: str = "#B5CEA8"
    tok_constant: str = "#4FC1FF"
    tok_builtin: str = "#DCDCAA"
    tok_preprocessor: str = "#C586C0"
    tok_annotation: str = "#DCDCAA"
    tok_variable: str = "#9CDCFE"
    tok_operator: str = "#D4D4D4"
    tok_tag: str = "#569CD6"
    tok_attribute: str = "#9CDCFE"
    tok_key: str = "#9CDCFE"
    tok_error: str = "#F44747"

    # 差异视图
    diff_add_bg: str = "#1E3A1E"
    diff_del_bg: str = "#3A1E1E"
    diff_hunk_bg: str = "#264F78"
    diff_add_text: str = "#B5CEA8"
    diff_del_text: str = "#F14C4C"

    # 字体
    ui_font_family: str = "Microsoft YaHei UI"
    ui_font_size: int = 9
    mono_font_family: str = "Consolas"
    mono_font_size: int = 11


DARK_PLUS = Theme()

LIGHT = Theme(
    key="light",
    label="Light+ (浅色)",
    window="#FFFFFF",
    window_border="#E5E5E5",
    sidebar="#F3F3F3",
    sidebar_section="#ECECEC",
    activity_bar="#2C2C2C",
    title_bar="#DDDDDD",
    panel="#FFFFFF",
    panel_border="#E5E5E5",
    status_bar="#007ACC",
    editor="#FFFFFF",
    editor_gutter="#FFFFFF",
    editor_current_line="#F5F5F5",
    editor_selection="#ADD6FF",
    editor_indent_guide="#D3D3D3",
    tab_active="#FFFFFF",
    tab_inactive="#ECECEC",
    menu="#F3F3F3",
    tooltip="#F3F3F3",
    text="#333333",
    text_muted="#717171",
    text_dim="#999999",
    text_bright="#000000",
    accent="#005FB8",
    accent_hover="#0066CC",
    accent_dim="#CCE4F7",
    button="#007ACC",
    button_hover="#0066B8",
    input_bg="#FFFFFF",
    input_border="#CECECE",
    input_focus_border="#007ACC",
    list_hover="#E8E8E8",
    list_selected="#CCE4F7",
    list_selected_inactive="#E4E6F1",
    scrollbar="#C1C1C1",
    scrollbar_hover="#A8A8A8",
    border="#E5E5E5",
    divider="#D4D4D4",
    success="#008000",
    warning="#795E26",
    danger="#CD3131",
    info="#0000FF",
    tok_comment="#008000",
    tok_keyword="#0000FF",
    tok_control="#AF00DB",
    tok_type="#267F99",
    tok_function="#795E26",
    tok_string="#A31515",
    tok_number="#098658",
    tok_constant="#0070C1",
    tok_builtin="#795E26",
    tok_preprocessor="#AF00DB",
    tok_annotation="#795E26",
    tok_variable="#001080",
    tok_operator="#000000",
    tok_tag="#800000",
    tok_attribute="#E50000",
    tok_key="#0451A5",
    tok_error="#CD3131",
    diff_add_bg="#E6FFEC",
    diff_del_bg="#FFEBE9",
    diff_hunk_bg="#DDEEFF",
    diff_add_text="#22863A",
    diff_del_text="#CB2431",
)

THEMES: Dict[str, Theme] = {DARK_PLUS.key: DARK_PLUS, LIGHT.key: LIGHT}
DEFAULT_THEME = DARK_PLUS


def get_theme(key: Optional[str]) -> Theme:
    return THEMES.get(key or "", DARK_PLUS)


def theme_names() -> List[tuple]:
    return [(t.key, t.label) for t in THEMES.values()]


# --------------------------------------------------------------------------------------
# 字体
# --------------------------------------------------------------------------------------

_MONO_CANDIDATES = (
    "Cascadia Code",
    "Cascadia Mono",
    "JetBrains Mono",
    "Fira Code",
    "Consolas",
    "DejaVu Sans Mono",
    "Courier New",
)

_UI_CANDIDATES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI",
    "Noto Sans CJK SC",
    "PingFang SC",
    "Sans Serif",
)


def _first_available(candidates, fallback: str) -> str:
    try:
        families = set(QFontDatabase.families())
    except Exception:  # pragma: no cover - 无 GUI 环境
        return fallback
    for name in candidates:
        if name in families:
            return name
    return fallback


def mono_font(size: Optional[int] = None, *, bold: bool = False, italic: bool = False) -> QFont:
    """等宽字体 (代码区)。"""
    theme = DEFAULT_THEME
    font = QFont(_first_available(_MONO_CANDIDATES, theme.mono_font_family))
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setPointSize(size or theme.mono_font_size)
    font.setBold(bold)
    font.setItalic(italic)
    return font


def ui_font(size: Optional[int] = None, *, bold: bool = False) -> QFont:
    """界面字体。"""
    theme = DEFAULT_THEME
    font = QFont(_first_available(_UI_CANDIDATES, theme.ui_font_family))
    font.setPointSize(size or theme.ui_font_size)
    font.setBold(bold)
    return font


# --------------------------------------------------------------------------------------
# 样式表
# --------------------------------------------------------------------------------------


def _qss(t: Theme) -> str:
    return f"""
/* ---------- 全局 ---------- */
/* 注意: 这里刻意不设置 font-family / font-size —— 否则会覆盖控件级 setFont(),
   导致代码区无法使用等宽字体与独立字号。界面字体由 app.setFont(ui_font()) 提供。 */
QWidget {{
    background-color: {t.window};
    color: {t.text};
}}
QMainWindow, QDialog {{ background-color: {t.window}; }}
QToolTip {{
    background-color: {t.tooltip};
    color: {t.text};
    border: 1px solid {t.divider};
    padding: 4px 6px;
}}

/* ---------- 菜单 ---------- */
QMenuBar {{
    background-color: {t.title_bar};
    color: {t.text};
    border-bottom: 1px solid {t.window_border};
    padding: 1px 2px;
}}
QMenuBar::item {{ background: transparent; padding: 4px 9px; border-radius: 3px; }}
QMenuBar::item:selected {{ background-color: {t.list_hover}; }}
QMenuBar::item:pressed {{ background-color: {t.accent_dim}; }}
QMenu {{
    background-color: {t.menu};
    border: 1px solid {t.divider};
    padding: 4px 0;
}}
QMenu::item {{ padding: 5px 26px 5px 26px; border: none; }}
QMenu::item:selected {{ background-color: {t.accent}; color: #FFFFFF; }}
QMenu::item:disabled {{ color: {t.text_dim}; }}
QMenu::separator {{ height: 1px; background: {t.divider}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 7px; }}

/* ---------- 工具栏 / 状态栏 ---------- */
QToolBar {{
    background-color: {t.title_bar};
    border: none;
    border-bottom: 1px solid {t.window_border};
    spacing: 3px;
    padding: 3px 6px;
}}
QToolBar::separator {{ background: {t.divider}; width: 1px; margin: 4px 5px; }}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 7px;
    color: {t.text};
}}
QToolButton:hover {{ background-color: {t.list_hover}; }}
QToolButton:pressed, QToolButton:checked {{ background-color: {t.accent_dim}; }}
QToolButton:disabled {{ color: {t.text_dim}; }}

QStatusBar {{
    background-color: {t.status_bar};
    color: {t.status_bar_text};
    border: none;
    font-size: {t.ui_font_size}pt;
}}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {t.status_bar_text}; background: transparent; padding: 0 6px; }}
QStatusBar QToolButton {{ color: {t.status_bar_text}; padding: 1px 6px; }}
QStatusBar QToolButton:hover {{ background-color: rgba(255,255,255,0.18); }}

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    background-color: {t.input_bg};
    color: {t.text};
    border: 1px solid {t.input_border};
    border-radius: 3px;
    padding: 4px 6px;
    selection-background-color: {t.editor_selection};
    selection-color: {t.text_bright};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus {{
    border: 1px solid {t.input_focus_border};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {t.text_dim}; }}
QLineEdit[echoMode="2"] {{ lineedit-password-character: 9679; }}

QComboBox {{
    background-color: {t.input_bg};
    color: {t.text};
    border: 1px solid {t.input_border};
    border-radius: 3px;
    padding: 4px 6px;
    min-height: 16px;
}}
QComboBox:hover {{ border-color: {t.input_focus_border}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.text_muted};
    width: 0; height: 0; margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {t.menu};
    border: 1px solid {t.divider};
    selection-background-color: {t.accent};
    selection-color: #FFFFFF;
    outline: none;
}}

QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {t.text_muted};
    background: {t.input_bg};
}}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 7px; }}
QCheckBox::indicator:checked {{
    background-color: {t.accent};
    border-color: {t.accent};
    image: none;
}}
QRadioButton::indicator:checked {{ background-color: {t.accent}; border-color: {t.accent}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent_hover}; }}

/* ---------- 按钮 ---------- */
QPushButton {{
    background-color: {t.button};
    color: #FFFFFF;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 5px 14px;
    min-width: 60px;
}}
QPushButton:hover {{ background-color: {t.button_hover}; }}
QPushButton:pressed {{ background-color: {t.accent}; }}
QPushButton:disabled {{ background-color: {t.sidebar_section}; color: {t.text_dim}; }}
QPushButton[flat="true"] {{
    background: transparent; color: {t.text}; border: 1px solid {t.divider};
}}
QPushButton[flat="true"]:hover {{ background-color: {t.list_hover}; }}
QPushButton[danger="true"] {{ background-color: #A1260D; }}
QPushButton[danger="true"]:hover {{ background-color: #C42B1C; }}

/* ---------- 列表 / 树 / 表格 ---------- */
QListView, QTreeView, QTableView, QListWidget, QTreeWidget, QTableWidget {{
    background-color: {t.sidebar};
    border: none;
    outline: none;
    alternate-background-color: {t.sidebar};
    selection-background-color: {t.list_selected};
    selection-color: {t.text_bright};
}}
QListView::item, QTreeView::item, QListWidget::item, QTreeWidget::item {{
    padding: 3px 4px;
    border: none;
}}
QListView::item:hover, QTreeView::item:hover, QListWidget::item:hover, QTreeWidget::item:hover {{
    background-color: {t.list_hover};
}}
QListView::item:selected, QTreeView::item:selected, QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {t.list_selected};
    color: {t.text_bright};
}}
QTreeView::branch {{ background: transparent; }}
QHeaderView::section {{
    background-color: {t.sidebar_section};
    color: {t.text_muted};
    padding: 4px 6px;
    border: none;
    border-right: 1px solid {t.divider};
    border-bottom: 1px solid {t.divider};
}}
QTableView {{ gridline-color: {t.divider}; }}

/* ---------- 标签页 ---------- */
QTabWidget::pane {{ border: 1px solid {t.panel_border}; top: -1px; background: {t.panel}; }}
QTabBar {{ qproperty-drawBase: 0; background: {t.sidebar}; }}
QTabBar::tab {{
    background: {t.tab_inactive};
    color: {t.text_muted};
    padding: 5px 14px;
    border: none;
    border-right: 1px solid {t.window_border};
    min-width: 60px;
}}
QTabBar::tab:selected {{
    background: {t.tab_active};
    color: {t.text_bright};
    border-top: 1px solid {t.accent};
}}
QTabBar::tab:hover:!selected {{ background: {t.list_hover}; color: {t.text}; }}
QTabBar::close-button {{ subcontrol-position: right; }}

/* ---------- 分隔条 ---------- */
QSplitter::handle {{ background-color: {t.window_border}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QSplitter::handle:hover {{ background-color: {t.accent}; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent; width: 12px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {t.scrollbar}; min-height: 24px; border-radius: 6px; margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.scrollbar_hover}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {t.scrollbar}; min-width: 24px; border-radius: 6px; margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t.scrollbar_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---------- 分组框 ---------- */
QGroupBox {{
    border: 1px solid {t.divider};
    border-radius: 4px;
    margin-top: 9px;
    padding-top: 6px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {t.text_muted};
}}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background-color: {t.sidebar_section};
    border: none; border-radius: 3px; text-align: center; height: 6px;
    color: transparent;
}}
QProgressBar::chunk {{ background-color: {t.accent}; border-radius: 3px; }}

/* ---------- 具名部件 ---------- */
#ActivityBar {{
    background-color: {t.activity_bar};
    border-right: 1px solid {t.window_border};
}}
#ActivityBar QToolButton {{
    background: transparent; border: none; border-left: 2px solid transparent;
    padding: 8px 0; color: {t.text_muted};
}}
#ActivityBar QToolButton:hover {{ color: {t.text_bright}; }}
#ActivityBar QToolButton:checked {{
    color: {t.text_bright}; border-left: 2px solid {t.text_bright};
}}

#SideBar {{ background-color: {t.sidebar}; border-right: 1px solid {t.window_border}; }}
#SideBarTitle {{
    background-color: {t.sidebar};
    color: {t.text_muted};
    padding: 6px 10px;
    font-size: 8pt;
    font-weight: bold;
    letter-spacing: 1px;
}}
#SectionHeader {{
    background-color: {t.sidebar_section};
    color: {t.text};
    padding: 4px 8px;
    font-weight: bold;
}}
#EditorArea {{ background-color: {t.editor}; }}
#DetailPane {{ background-color: {t.editor}; }}
#Card {{
    background-color: {t.sidebar};
    border: 1px solid {t.divider};
    border-radius: 4px;
}}
#MutedLabel {{ color: {t.text_muted}; }}
#DimLabel {{ color: {t.text_dim}; }}
#TitleLabel {{ font-size: 15pt; font-weight: bold; color: {t.text_bright}; }}
#SubTitleLabel {{ font-size: 10pt; color: {t.text_muted}; }}
#SectionLabel {{
    color: {t.text_muted}; font-weight: bold; font-size: 8pt;
    letter-spacing: 1px; padding: 2px 0;
}}
#CodeHeader {{
    background-color: {t.sidebar_section};
    border-bottom: 1px solid {t.divider};
}}
#CodeHeader QLabel {{ color: {t.text_muted}; background: transparent; }}
#StatusBadge {{
    border-radius: 8px; padding: 1px 9px; font-size: 8pt; font-weight: bold;
}}
#CountBadge {{
    background-color: {t.sidebar_section};
    color: {t.text_muted};
    border-radius: 8px;
    padding: 0 6px;
    font-size: 8pt;
}}
/* DiffView 是 QPlainTextEdit (与 QTextEdit 是兄弟类而非子类), 两个选择器都要写 */
QTextBrowser, QTextEdit#DiffView, QPlainTextEdit#DiffView {{
    background-color: {t.editor};
    border: none;
    font-family: "{t.mono_font_family}", Consolas, monospace;
}}
#HistoryList::item {{ padding: 5px 6px; }}
#TagChip {{
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 8pt;
}}
"""


def apply_theme(app: QApplication, theme: Theme = DEFAULT_THEME) -> None:
    """把主题应用到整个应用 (样式表 + 调色板)。"""
    app.setStyle("Fusion")
    app.setStyleSheet(_qss(theme))
    app.setFont(ui_font())

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.window))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme.input_bg))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.sidebar))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme.button))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text_bright))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme.text_bright))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(theme.tooltip))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(theme.text_dim))
    app.setPalette(palette)


def token_colors(theme: Theme = DEFAULT_THEME) -> Dict[str, str]:
    """语法高亮使用的 token 颜色表。"""
    return {
        "comment": theme.tok_comment,
        "keyword": theme.tok_keyword,
        "control": theme.tok_control,
        "type": theme.tok_type,
        "function": theme.tok_function,
        "string": theme.tok_string,
        "number": theme.tok_number,
        "constant": theme.tok_constant,
        "builtin": theme.tok_builtin,
        "preprocessor": theme.tok_preprocessor,
        "annotation": theme.tok_annotation,
        "variable": theme.tok_variable,
        "operator": theme.tok_operator,
        "tag": theme.tok_tag,
        "attribute": theme.tok_attribute,
        "key": theme.tok_key,
        "error": theme.tok_error,
        "text": theme.text,
    }
