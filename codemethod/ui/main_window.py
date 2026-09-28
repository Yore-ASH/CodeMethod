"""主窗口: 把仓储、容器与全部界面部件组装成 VSCode 风格的工作台。

布局::

    ┌──────── 菜单栏 ─────────────────────────────────────────────┐
    ├──────── 工具栏 ─────────────────────────────────────────────┤
    │活动 │  检索栏 + 条目列表      │  详情面板                    │
    │栏   │─────────────────────────┴──────────────────────────────│
    │     │  历史 / 差异面板                                      │
    ├──────── 状态栏 ─────────────────────────────────────────────┤
    └─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTextBrowser,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_VERSION
from ..core.functions import FunctionImplementation
from ..core.languages import get_language, language_choices, normalize_language
from ..core.models import STATUS_LABELS, STATUS_ORDER, Entry, format_ts
from ..core.query import QuerySpec, TagMatch, query_entries, related_tags
from ..core.repository import Repository, RepositoryError
from ..core.spaces import human_bytes, normalize_project_path
from ..storage import exporter
from ..storage.container import BINARY_EXTENSIONS, TEXT_EXTENSIONS, human_size
from ..storage.database import Database, DatabaseError
from .dialogs.about_dialog import AboutDialog
from .dialogs.container_dialog import ContainerInfoDialog, VerifyResultDialog
from .dialogs.directory_import import DirectoryImportDialog
from .dialogs.entry_editor import EntryEditorDialog
from .dialogs.function_editor import FunctionEditorDialog
from .dialogs.implementation_dialog import ImplementationDialog
from .dialogs.limits_dialog import LimitsDialog
from .dialogs.new_directory import PLACEHOLDER_NAME, NewDirectoryDialog
from .dialogs.readme_search import ReadmeSearchDialog
from .dialogs.space_editor import SpaceEditorDialog
from .dialogs.tag_manager import TagManagerDialog
from .resources import app_icon
from .theme import THEMES, Theme, apply_theme, get_theme, theme_names
from .widgets.detail_panel import DetailPanel
from .widgets.entry_list import EntryListView
from .widgets.function_detail import FunctionDetailPanel
from .widgets.history_panel import HistoryPanel
from .widgets.search_bar import SearchBar
from .widgets.space_detail import SpaceDetailPanel
from .widgets.tag_panel import TagPanel

FILE_FILTER = (
    "CodeMethod 代码库 (*.cmdb *.cmj);;"
    "二进制容器 (*.cmdb);;"
    "文本容器 (*.cmj);;"
    "所有文件 (*)"
)
IMPORT_FILTER = (
    "CodeMethod 代码库 (*.cmdb *.cmj *.zip *.json);;"
    "二进制容器 (*.cmdb);;"
    "文本容器 (*.cmj);;"
    "ZIP 导出包 (*.zip);;"
    "JSON 导出 (*.json);;"
    "所有文件 (*)"
)


class _StatsPanel(QWidget):
    """统计面板: 语言 / 标签 / 状态分布与容器概况。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SideBar")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("统计信息", self)
        title.setObjectName("SideBarTitle")
        layout.addWidget(title)

        self.browser = QTextBrowser(self)
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        layout.addWidget(self.browser, 1)

    def update_stats(self, repo: Repository, db: Database) -> None:
        stats = repo.statistics()
        language_usage = repo.language_usage()
        tag_usage = repo.tag_usage()

        # 侧边栏宽度有限, 柱状图用少量字符绘制, 避免换行破坏排版
        def bar(count: int, maximum: int, color: str = "#007ACC", width: int = 12) -> str:
            filled = 0 if not maximum or not count else max(1, int(round(width * count / maximum)))
            filled = min(filled, width)
            return (
                f"<span style='color:{color}'>{'█' * filled}</span>"
                f"<span style='color:#3C3C3C'>{'░' * (width - filled)}</span>"
            )

        rows: List[str] = []
        rows.append("<h4 style='margin:6px 0 2px 0'>总览</h4>")
        rows.append("<table cellspacing='2'>")
        for label, value in (
            ("条目", stats["entries"]),
            ("实现", stats["implementations"]),
            ("语言种类", stats["languages"]),
            ("标签", stats["tags"]),
            ("修订记录", stats["revisions"]),
            ("代码行数", stats["code_lines"]),
            ("回收站", stats["deleted_entries"]),
        ):
            rows.append(f"<tr><td style='color:#858585'>{label}</td><td><b>{value}</b></td></tr>")
        rows.append("</table>")

        rows.append("<h4 style='margin:10px 0 2px 0'>状态分布</h4>")
        status_counts: Dict[str, int] = {s: 0 for s in STATUS_ORDER}
        for entry in repo.entries_list():
            status_counts[entry.status] = status_counts.get(entry.status, 0) + 1
        max_status = max(status_counts.values()) if status_counts else 0
        rows.append("<table cellspacing='2'>")
        for status in STATUS_ORDER:
            count = status_counts.get(status, 0)
            rows.append(
                f"<tr><td style='color:#858585'>{STATUS_LABELS[status]}</td>"
                f"<td>{bar(count, max_status)}</td><td>{count}</td></tr>"
            )
        rows.append("</table>")

        if language_usage:
            rows.append("<h4 style='margin:10px 0 2px 0'>语言分布</h4>")
            max_lang = max(language_usage.values())
            rows.append("<table cellspacing='2'>")
            for lang, count in sorted(language_usage.items(), key=lambda kv: -kv[1]):
                rows.append(
                    f"<tr><td style='color:#858585'>{get_language(lang).name}</td>"
                    f"<td>{bar(count, max_lang, '#4EC9B0')}</td><td>{count}</td></tr>"
                )
            rows.append("</table>")

        if tag_usage:
            rows.append("<h4 style='margin:10px 0 2px 0'>热门标签</h4>")
            top = sorted(tag_usage.items(), key=lambda kv: -kv[1])[:12]
            max_tag = top[0][1] if top else 0
            rows.append("<table cellspacing='2'>")
            for key, count in top:
                color = repo.tags.get(key).color if key in repo.tags else "#569CD6"
                rows.append(
                    f"<tr><td style='color:{color}'>#{key}</td>"
                    f"<td>{bar(count, max_tag, color)}</td><td>{count}</td></tr>"
                )
            rows.append("</table>")

        rows.append("<h4 style='margin:10px 0 2px 0'>容器</h4>")
        rows.append("<table cellspacing='2'>")
        path_text = db.path or "（尚未保存到磁盘）"
        rows.append(f"<tr><td style='color:#858585'>文件</td><td>{path_text}</td></tr>")
        if db.info is not None:
            rows.append(
                f"<tr><td style='color:#858585'>格式</td><td>{db.info.kind.label} "
                f"v{db.info.version}</td></tr>"
            )
            rows.append(
                f"<tr><td style='color:#858585'>大小</td><td>{human_size(db.info.file_size)}"
                f"（原始 {human_size(db.info.payload_raw)}，"
                f"压缩率 {db.info.ratio:.1%}）</td></tr>"
            )
        if db.last_saved_at:
            rows.append(
                f"<tr><td style='color:#858585'>上次保存</td>"
                f"<td>{format_ts(db.last_saved_at)}</td></tr>"
            )
        rows.append("</table>")

        self.browser.setHtml("".join(rows))


class MainWindow(QMainWindow):
    """CodeMethod 主窗口。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        parent: Optional[QWidget] = None,
        *,
        theme_key: str = "dark+",
    ) -> None:
        super().__init__(parent)
        self.settings = QSettings("CodeMethod", APP_NAME)
        self.db = db or Database.create()
        self.theme: Theme = get_theme(theme_key)
        self.query = QuerySpec()
        self._current_entry_id: str = ""
        # 当前选中对象属于哪一类 (module / space / function); 详情面板据此切换
        self._current_kind: str = "module"
        # 类别切换条当前的选择 ("all" / "module" / "space" / "function")
        self._active_kind: str = "all"
        self._suspend_refresh = False
        # refresh_list 重建列表期间置位: 抑制 currentChanged 引发的重复详情刷新
        self._suppress_selection_signal = False
        # 窗口正在关闭/析构: 此后所有刷新槽函数直接返回
        self._tearing_down = False
        # 首次显示时是否已经按屏幕可用区域夹过尺寸
        self._geometry_fitted = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        # 最小尺寸要留给小屏: 三栏各自的硬性最小宽度合计约 840, 所以 880 是底线。
        # 首选仍是 1440×900, 显示时再按屏幕可用区域夹一次 (见 showEvent)。
        self.setMinimumSize(880, 560)
        self.resize(1440, 900)

        self._build_actions()
        self._build_ui()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._wire_signals()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(120_000)
        self._autosave_timer.timeout.connect(self._autosave)
        if self.settings.value("autosave", False, type=bool):
            self._autosave_timer.start()

        self._exclusive_switch = QAction(self)
        self._exclusive_switch.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self._exclusive_switch.triggered.connect(self._toggle_exclusive_tag_mode)
        self.addAction(self._exclusive_switch)

        self.refresh_all()
        self._restore_geometry()

    # ==================================================================================
    # 构建: 动作
    # ==================================================================================
    def _act(
        self,
        text: str,
        slot,
        *,
        shortcut: Optional[str] = None,
        tip: str = "",
        checkable: bool = False,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.setStatusTip(tip or text)
        action.setToolTip(tip or text)
        action.setCheckable(checkable)
        if checkable:
            action.toggled.connect(slot)
        else:
            action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self.act_new_library = self._act("新建代码库", self.new_library, shortcut="Ctrl+Shift+N", tip="新建一个空的代码库")
        self.act_open = self._act("打开…", self.open_library, shortcut="Ctrl+O", tip="打开 .cmdb / .cmj 代码库")
        self.act_save = self._act("保存", self.save_library, shortcut="Ctrl+S", tip="保存到当前文件")
        self.act_save_as = self._act("另存为…", self.save_library_as, shortcut="Ctrl+Shift+S", tip="保存为新文件")
        self.act_import = self._act("导入 / 合并…", self.import_library, shortcut="Ctrl+I", tip="把另一个代码库的条目并入当前库")
        self.act_export_md = self._act("导出 Markdown…", self.export_markdown, tip="导出为可读的 Markdown 文档")
        self.act_export_json = self._act("导出 JSON…", self.export_json, tip="导出为 JSON")
        self.act_export_zip = self._act("导出 ZIP (含源码文件)…", self.export_zip, tip="导出为目录结构 + 真实源码文件")
        self.act_convert_binary = self._act("转换为二进制容器 (.cmdb)…", lambda: self.convert_format(True))
        self.act_convert_text = self._act("转换为文本容器 (.cmj)…", lambda: self.convert_format(False))
        self.act_container_info = self._act("容器信息…", self.show_container_info, tip="查看当前文件的格式、大小、清单")
        self.act_verify = self._act("校验文件完整性…", self.verify_file, tip="检查魔数、CRC32、SHA-256")
        self.act_limits = self._act(
            "存储限制…", self.show_limits_dialog,
            tip="文件大小/数量限制 (默认全部不限制, 想设才设)",
        )
        self.act_quit = self._act("退出", self.close, shortcut="Ctrl+Q")

        # ---- 模块 (原"条目", 换个更贴切的名字) ----
        self.act_new_entry = self._act("新建模块", self.new_entry, shortcut="Ctrl+N", tip="新建一个功能模块 (一个问题 + 多语言实现)")
        self.act_edit_entry = self._act("编辑模块", self.edit_entry, shortcut="Ctrl+E", tip="编辑当前模块与全部语言实现")
        self.act_add_impl = self._act(
            "添加语言实现…",
            lambda: self.add_implementation(),
            shortcut="Ctrl+L",
            tip="为当前模块再添加一种语言的实现 — 同一个问题, 不同语言的解法",
        )
        self.act_edit_impl = self._act(
            "编辑当前语言实现…",
            lambda: self.edit_implementation(),
            shortcut="Ctrl+Shift+E",
            tip="编辑当前页签对应语言的实现",
        )

        # ---- 独立空间 / 函数体 ----
        self.act_new_space = self._act(
            "新建空间", self.new_space, shortcut="Ctrl+Shift+W",
            tip="在代码库内新建一个完整项目 (所有文件都存进同一个 .cmdb)",
        )
        self.act_edit_space = self._act("编辑空间", lambda: self.edit_space(), shortcut="Ctrl+Shift+O")
        self.act_import_directory = self._act(
            "把整个目录打包进空间…",
            self.import_directory_into_space,
            shortcut="Ctrl+Shift+I",
            tip="选一个目录, 连同全部子文件夹一起嵌进空间 (先预览再导入)",
        )
        self.act_new_function = self._act(
            "新建函数体", self.new_function, shortcut="Ctrl+Shift+F",
            tip="写一个特定语言的函数, 自动检测变量并要求填写含义",
        )
        self.act_edit_function = self._act("编辑函数体", lambda: self.edit_function(), shortcut="Ctrl+Shift+G")
        self.act_redetect_symbols = self._act(
            "重新检测变量", lambda: self.redetect_symbols(), shortcut="Ctrl+Shift+R",
            tip="按当前代码重新扫描变量声明 (已填写的含义不会被覆盖)",
        )
        self.act_readme_search = self._act(
            "README 检索…", self.search_readmes, shortcut="Ctrl+Shift+D",
            tip="在全部空间的 README 文件里搜索",
        )
        self.act_kind_all = self._act("全部", lambda: self.set_kind_filter("all"), shortcut="Alt+0")
        self.act_kind_module = self._act("只看模块", lambda: self.set_kind_filter("module"), shortcut="Alt+1")
        self.act_kind_space = self._act("只看空间", lambda: self.set_kind_filter("space"), shortcut="Alt+2")
        self.act_kind_function = self._act("只看函数体", lambda: self.set_kind_filter("function"), shortcut="Alt+3")

        self.act_duplicate_entry = self._act("创建副本", self.duplicate_entry)
        self.act_delete_entry = self._act("删除", self.delete_entry, shortcut="Ctrl+Delete", tip="移入回收站 (可在历史中恢复)")
        self.act_restore_entry = self._act("从回收站恢复", self.restore_entry)
        self.act_purge_entry = self._act("彻底删除…", self.purge_entry, tip="不可撤销地移除对象")
        self.act_toggle_favorite = self._act("收藏 / 取消收藏", self.toggle_favorite, shortcut="Ctrl+D")
        self.act_set_status = self._act("设置规划状态…", self.set_planning_status)

        self.act_undo = self._act("撤销", self.undo, shortcut="Ctrl+Z", tip="回退当前对象的上一条修订")
        self.act_redo = self._act("重做", self.redo, shortcut="Ctrl+Y", tip="前进当前对象的下一条修订")
        self.act_show_history = self._act("显示历史面板", self._toggle_history, shortcut="Ctrl+H", checkable=True, tip="显示/隐藏历史面板")
        self.act_show_history.setChecked(True)

        self.act_copy_code = self._act("复制当前实现代码", self.copy_current_code, shortcut="Ctrl+Shift+C", tip="复制到剪贴板")
        self.act_copy_entry_md = self._act("复制为 Markdown", lambda: self.copy_entry_as("markdown"), shortcut="Ctrl+Shift+M")
        self.act_copy_entry_json = self._act("复制为 JSON", lambda: self.copy_entry_as("json"))

        self.act_manage_tags = self._act("标签管理…", self.manage_tags, shortcut="Ctrl+T")
        self.act_new_tag_on_entry = self._act("给当前对象添加标签…", self.add_tag_to_current, shortcut="Ctrl+Shift+T")
        self.act_focus_search = self._act("检索…", self.focus_search, shortcut="Ctrl+F")
        self.act_clear_search = self._act("清空检索条件", self.clear_search, shortcut="Escape")
        self.act_refresh = self._act("刷新", self.refresh_all, shortcut="F5")

        self.act_dark_theme = self._act("Dark+ 主题", lambda: self.set_theme_key("dark+"))
        self.act_light_theme = self._act("Light+ 主题", lambda: self.set_theme_key("light"))
        self.act_about = self._act("关于 CodeMethod", self.show_about)
        self.act_shortcuts = self._act("快捷键说明", self.show_shortcuts, shortcut="F1")
        self.act_autosave = self._act("自动保存 (每 2 分钟)", self._toggle_autosave, checkable=True)
        self.act_autosave.setChecked(self.settings.value("autosave", False, type=bool))
        self.act_cycle_theme = self._act(
            "循环切换主题",
            self.cycle_theme,
            shortcut="Ctrl+Shift+Y",
            tip="在全部主题之间依次切换",
        )

    # ==================================================================================
    # 构建: 界面
    # ==================================================================================
    def _build_ui(self) -> None:
        central = QSplitter(Qt.Orientation.Horizontal, self)
        central.setChildrenCollapsible(False)

        # ---- 侧边栏 ----
        self.sidebar_stack = QStackedWidget(central)
        self.tag_panel = TagPanel(self.sidebar_stack, theme=self.theme)
        self.stats_panel = _StatsPanel(self.sidebar_stack)
        self.sidebar_stack.addWidget(self.tag_panel)
        self.sidebar_stack.addWidget(self.stats_panel)
        self.sidebar_stack.setMinimumWidth(190)
        central.addWidget(self.sidebar_stack)

        # ---- 中间: 类别切换 + 检索 + 列表 ----
        left = QWidget(central)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self._build_kind_bar(left))
        self.search_bar = SearchBar(left, theme=self.theme)
        left_layout.addWidget(self.search_bar)
        self.entry_list = EntryListView(left, theme=self.theme)
        self.entry_list.setMinimumWidth(300)
        left_layout.addWidget(self.entry_list, 1)
        central.addWidget(left)

        # ---- 右侧: 详情 (模块 / 空间 / 函数体 三种面板) ----
        self.detail_stack = QStackedWidget(central)
        self.detail_panel = DetailPanel(self.detail_stack, theme=self.theme)
        self.space_panel = SpaceDetailPanel(self.detail_stack, theme=self.theme)
        self.function_panel = FunctionDetailPanel(self.detail_stack, theme=self.theme)
        self.detail_stack.addWidget(self.detail_panel)
        self.detail_stack.addWidget(self.space_panel)
        self.detail_stack.addWidget(self.function_panel)
        self.detail_stack.setMinimumWidth(340)
        central.addWidget(self.detail_stack)

        central.setStretchFactor(0, 0)
        central.setStretchFactor(1, 4)
        central.setStretchFactor(2, 6)
        central.setSizes([230, 460, 720])

        # ---- 历史面板在下方 ----
        self.history_panel = HistoryPanel(self, theme=self.theme)
        self.history_panel.setMinimumHeight(120)

        self.main_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(central)
        self.main_splitter.addWidget(self.history_panel)
        self.main_splitter.setStretchFactor(0, 4)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([620, 240])
        self.setCentralWidget(self.main_splitter)

    def _build_kind_bar(self, parent: QWidget) -> QWidget:
        """类别切换条: 全部 / 模块 / 空间 / 函数体。

        三类实体共用一个列表, 这里决定筛选哪一类。
        """
        bar = QWidget(parent)
        bar.setObjectName("CodeHeader")
        bar.setFixedHeight(32)
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(4)

        label = QLabel("类别", bar)
        label.setObjectName("MutedLabel")
        row.addWidget(label)

        self._kind_buttons: Dict[str, QToolButton] = {}
        for key, text, tip in (
            ("all", "全部", "显示模块、空间与函数体"),
            ("module", "模块", "一个问题 + 多种语言实现"),
            ("space", "空间", "库内的完整项目结构 (多文件)"),
            ("function", "函数体", "单个语言的函数 + 变量含义表"),
        ):
            button = QToolButton(bar)
            button.setText(text)
            button.setToolTip(tip)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setChecked(key == "all")
            button.clicked.connect(lambda _c=False, k=key: self.set_kind_filter(k))
            row.addWidget(button)
            self._kind_buttons[key] = button

        row.addStretch(1)
        self.kind_count_label = QLabel("", bar)
        self.kind_count_label.setObjectName("DimLabel")
        row.addWidget(self.kind_count_label)
        return bar

    def set_kind_filter(self, kind: str) -> None:
        """切换列表里显示哪一类实体。"""
        self.query.kinds = [] if kind == "all" else [kind]
        self._active_kind = kind
        for key, button in getattr(self, "_kind_buttons", {}).items():
            button.setChecked(key == kind)
        # 切换类别后原来的选中项多半不在列表里了, 让 refresh_list 自己挑一个
        self.refresh_list(select_id="")

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("文件(&F)")
        file_menu.addAction(self.act_new_library)
        file_menu.addAction(self.act_open)

        self.recent_menu = file_menu.addMenu("最近打开")
        self.recent_menu.setToolTipsVisible(True)
        self._rebuild_recent_menu()

        file_menu.addSeparator()
        file_menu.addAction(self.act_save)
        file_menu.addAction(self.act_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self.act_import)
        file_menu.addAction(self.act_import_directory)
        export_menu = file_menu.addMenu("导出")
        export_menu.addAction(self.act_export_md)
        export_menu.addAction(self.act_export_json)
        export_menu.addAction(self.act_export_zip)
        convert_menu = file_menu.addMenu("容器格式转换")
        convert_menu.addAction(self.act_convert_binary)
        convert_menu.addAction(self.act_convert_text)
        file_menu.addSeparator()
        file_menu.addAction(self.act_container_info)
        file_menu.addAction(self.act_verify)
        file_menu.addAction(self.act_limits)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        edit_menu = bar.addMenu("编辑(&E)")
        new_menu = edit_menu.addMenu("新建")
        new_menu.addAction(self.act_new_entry)
        new_menu.addAction(self.act_new_space)
        new_menu.addAction(self.act_new_function)
        edit_menu.addAction(self.act_edit_entry)
        edit_menu.addAction(self.act_edit_space)
        edit_menu.addAction(self.act_edit_function)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_add_impl)
        edit_menu.addAction(self.act_edit_impl)
        edit_menu.addAction(self.act_redetect_symbols)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_duplicate_entry)
        edit_menu.addAction(self.act_delete_entry)
        edit_menu.addAction(self.act_restore_entry)
        edit_menu.addAction(self.act_purge_entry)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_toggle_favorite)
        edit_menu.addAction(self.act_set_status)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        edit_menu.addAction(self.act_show_history)

        view_menu = bar.addMenu("视图(&V)")
        view_menu.addAction(self.act_refresh)
        kind_menu = view_menu.addMenu("显示类别")
        kind_menu.addAction(self.act_kind_all)
        kind_menu.addAction(self.act_kind_module)
        kind_menu.addAction(self.act_kind_space)
        kind_menu.addAction(self.act_kind_function)
        view_menu.addSeparator()
        sidebar_menu = view_menu.addMenu("侧边栏")
        self.act_sidebar_tags = self._act("标签筛选", lambda: self._show_sidebar(0), shortcut="Ctrl+1")
        self.act_sidebar_stats = self._act("统计信息", lambda: self._show_sidebar(1), shortcut="Ctrl+2")
        sidebar_menu.addAction(self.act_sidebar_tags)
        sidebar_menu.addAction(self.act_sidebar_stats)
        theme_menu = view_menu.addMenu("主题")
        self._build_theme_menu(theme_menu)
        view_menu.addSeparator()
        view_menu.addAction(self.act_autosave)

        tools_menu = bar.addMenu("工具(&T)")
        tools_menu.addAction(self.act_readme_search)
        tools_menu.addSeparator()
        tools_menu.addAction(self.act_manage_tags)
        tools_menu.addAction(self.act_new_tag_on_entry)
        tools_menu.addSeparator()
        tools_menu.addAction(self.act_copy_code)
        tools_menu.addAction(self.act_copy_entry_md)
        tools_menu.addAction(self.act_copy_entry_json)

        help_menu = bar.addMenu("帮助(&H)")
        help_menu.addAction(self.act_shortcuts)
        help_menu.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        bar = QToolBar("主工具栏", self)
        bar.setObjectName("MainToolBar")  # saveState()/restoreState() 需要
        bar.setMovable(False)
        bar.setIconSize(bar.iconSize())
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)
        self.toolbar = bar

        bar.addAction(self.act_new_entry)
        bar.addAction(self.act_new_space)
        bar.addAction(self.act_new_function)
        bar.addSeparator()
        bar.addAction(self.act_edit_entry)
        bar.addAction(self.act_add_impl)
        bar.addAction(self.act_delete_entry)
        bar.addSeparator()
        bar.addAction(self.act_undo)
        bar.addAction(self.act_redo)
        bar.addSeparator()
        bar.addAction(self.act_open)
        bar.addAction(self.act_save)
        bar.addSeparator()
        bar.addAction(self.act_import_directory)
        bar.addAction(self.act_readme_search)
        bar.addAction(self.act_copy_code)
        bar.addAction(self.act_manage_tags)
        bar.addAction(self.act_refresh)

    def _build_statusbar(self) -> None:
        status = QStatusBar(self)
        self.setStatusBar(status)

        self.status_message = QLabel("就绪", self)
        self.status_db = QLabel("", self)
        self.status_counts = QLabel("", self)
        self.status_entry = QLabel("", self)
        self.status_dirty = QLabel("", self)

        status.addWidget(self.status_message, 1)
        status.addPermanentWidget(self.status_entry)
        status.addPermanentWidget(self.status_counts)
        status.addPermanentWidget(self.status_dirty)
        status.addPermanentWidget(self.status_db)

    # ==================================================================================
    # 信号
    # ==================================================================================
    def _wire_signals(self) -> None:
        self.entry_list.selection_changed.connect(self.on_entry_selected)
        self.entry_list.entry_activated.connect(self.on_entry_activated)
        self.entry_list.context_requested.connect(self.on_entry_context_menu)

        self.search_bar.query_changed.connect(self.on_query_changed)
        self.search_bar.sort_changed.connect(self.on_sort_changed)
        self.search_bar.scope_changed.connect(self.on_scope_changed)

        self.tag_panel.selection_changed.connect(self.on_tags_changed)
        self.tag_panel.tag_context_requested.connect(self.on_tag_context_menu)

        self.detail_panel.edit_requested.connect(lambda _id: self.edit_entry())
        self.detail_panel.history_requested.connect(lambda _id: self.show_history_panel())
        self.detail_panel.favorite_toggled.connect(lambda _id: self.toggle_favorite())
        self.detail_panel.delete_requested.connect(lambda _id: self.delete_entry())
        self.detail_panel.duplicate_requested.connect(lambda _id: self.duplicate_entry())
        self.detail_panel.tag_clicked.connect(self.on_tag_chip_clicked)
        self.detail_panel.copy_done.connect(self.set_status)
        self.detail_panel.export_requested.connect(self.on_export_requested)
        self.detail_panel.add_implementation_requested.connect(self.add_implementation)
        self.detail_panel.edit_implementation_requested.connect(self.edit_implementation)
        self.detail_panel.delete_implementation_requested.connect(self.delete_implementation)

        # 空间详情
        self.space_panel.edit_requested.connect(lambda _id: self.edit_space())
        self.space_panel.history_requested.connect(lambda _id: self.show_history_panel())
        self.space_panel.favorite_toggled.connect(lambda _id: self.toggle_favorite())
        self.space_panel.delete_requested.connect(lambda _id: self.delete_entry())
        self.space_panel.duplicate_requested.connect(lambda _id: self.duplicate_entry())
        self.space_panel.export_requested.connect(self.on_export_requested)
        self.space_panel.copy_done.connect(self.set_status)
        self.space_panel.open_file_requested.connect(self.open_space_file)
        self.space_panel.add_file_requested.connect(self.add_space_file)
        self.space_panel.edit_file_requested.connect(self.edit_space_file)
        self.space_panel.delete_file_requested.connect(self.delete_space_path)
        self.space_panel.add_directory_requested.connect(self.add_space_directory)
        self.space_panel.rename_file_requested.connect(self.rename_space_file)

        # 函数体详情
        self.function_panel.edit_requested.connect(lambda _id: self.edit_function())
        self.function_panel.history_requested.connect(lambda _id: self.show_history_panel())
        self.function_panel.favorite_toggled.connect(lambda _id: self.toggle_favorite())
        self.function_panel.delete_requested.connect(lambda _id: self.delete_entry())
        self.function_panel.duplicate_requested.connect(lambda _id: self.duplicate_entry())
        self.function_panel.export_requested.connect(self.on_export_requested)
        self.function_panel.copy_done.connect(self.set_status)
        self.function_panel.redetect_requested.connect(
            lambda fid, iid: self.redetect_symbols(fid, iid)
        )
        self.function_panel.add_implementation_requested.connect(
            lambda fid: self.add_function_implementation(fid)
        )
        self.function_panel.edit_implementation_requested.connect(
            lambda fid, iid: self.edit_function_implementation(fid, iid)
        )
        self.function_panel.delete_implementation_requested.connect(
            lambda fid, iid: self.delete_function_implementation(fid, iid)
        )
        self.function_panel.restore_requested.connect(lambda _id: self.restore_entry())
        self.function_panel.purge_requested.connect(lambda _id: self.purge_entry())
        self.space_panel.restore_requested.connect(lambda _id: self.restore_entry())
        self.space_panel.purge_requested.connect(lambda _id: self.purge_entry())
        self.space_panel.import_files_requested.connect(self.import_space_files)
        self.space_panel.export_file_requested.connect(self.export_space_file)
        self.space_panel.remove_placeholder_requested.connect(self.remove_space_placeholder)
        self.detail_panel.restore_requested.connect(lambda _id: self.restore_entry())
        self.detail_panel.purge_requested.connect(lambda _id: self.purge_entry())

        self.history_panel.restore_requested.connect(self.restore_revision)
        self.history_panel.undo_requested.connect(lambda _id: self.undo())
        self.history_panel.redo_requested.connect(lambda _id: self.redo())

    # ==================================================================================
    # 刷新
    # ==================================================================================
    #
    # 关键: 详情面板的刷新**不能依赖列表的选中信号**。
    # 重新选中"同一行"时 Qt 不会发出 currentChanged, 因此
    #   "编辑条目 / 改标签 / 切状态 / 回滚之后调用 refresh_all()"
    # 这类"内容变了但选中行没变"的场景会漏刷新, 界面停在旧内容上。
    # 所以 refresh_list() 重建列表后会显式同步选中项并刷新详情, 不再依赖信号。
    def refresh_all(self, *, select_id: str = "", keep_selection: bool = True) -> None:
        if self._suspend_refresh or self._tearing_down:
            return
        desired = select_id or (self._current_entry_id if keep_selection else "")
        self.refresh_tags()
        self.refresh_list(select_id=desired)
        self.refresh_stats()
        self.update_titles()

    def refresh_tags(self) -> None:
        colors = {key: info.color for key, info in self.db.repository.tags.items()}
        self.tag_panel.set_tags(
            self.db.repository.all_tags(include_deleted=False),
            self.db.repository.tag_usage(),
            colors,
            detail=self.db.repository.tag_usage_by_kind(),
        )

    def refresh_list(self, *, select_id: str = "", sync_detail: bool = True) -> None:
        """重建条目列表。``sync_detail=True`` 时一并同步选中项与详情面板。"""
        if self._tearing_down:
            return
        revision_counts = {
            entry.id: self.db.repository.history.count(entry.id)
            for entry in self.db.repository.entries.values()
        }
        tag_colors = {key: info.color for key, info in self.db.repository.tags.items()}
        entries = query_entries(self.db.repository, self.query)

        # 重建期间抑制选中信号: 模型 reset 会产生"先失效再选中"的中间态, 没有必要反复重建详情
        found = True
        self._suppress_selection_signal = True
        try:
            self.entry_list.set_entries(
                entries, revision_counts=revision_counts, tag_colors=tag_colors
            )
            if select_id:
                found = self.entry_list.select_entry(select_id)
        finally:
            self._suppress_selection_signal = False

        # 以列表真实的选中项为准, 保证 _current_entry_id 与界面高亮永远一致。
        # 唯一的例外: 显式点名了 select_id 却在列表里找不到它, 而库里**还有**这个对象
        # (只是被软删除了) —— 这时保留它, 详情面板才能显示"在回收站里"并让人恢复。
        # 纯粹被检索条件过滤掉的项不在此列, 那种情况详情面板应当清空。
        self._current_entry_id = self.entry_list.current_entry_id()
        if select_id and not found:
            candidate = self.db.repository.get_item(select_id)
            if candidate is not None and candidate.deleted:
                self._current_entry_id = select_id
        self.update_status_counts(len(entries))

        if sync_detail:
            self.refresh_detail()
            self.update_actions()

    def refresh_stats(self) -> None:
        self.stats_panel.update_stats(self.db.repository, self.db)

    def refresh_detail(self) -> None:
        if self._tearing_down:
            return
        item = (
            self.db.repository.get_item(self._current_entry_id)
            if self._current_entry_id else None
        )
        tag_colors = {key: info.color for key, info in self.db.repository.tags.items()}

        # 三类实体各有自己的详情面板, 按类别切到对应的一页
        self._current_kind = getattr(item, "kind", self._current_kind) if item else "module"
        if item is None:
            self.detail_panel.set_entry(None)
            self.space_panel.set_space(None)
            self.function_panel.set_function(None)
            self.detail_stack.setCurrentWidget(self.detail_panel)
        elif self._current_kind == "space":
            self.detail_stack.setCurrentWidget(self.space_panel)
            self.space_panel.set_space(item, tag_colors=tag_colors)
        elif self._current_kind == "function":
            self.detail_stack.setCurrentWidget(self.function_panel)
            self.function_panel.set_function(item)
        else:
            self.detail_stack.setCurrentWidget(self.detail_panel)
            self.detail_panel.set_entry(item, tag_colors=tag_colors)

        # 当前对象在回收站里时明确提示, 并让「恢复」触手可及
        if item is not None:
            self._sync_deleted_banner(item)
        self.refresh_history()

    def _sync_deleted_banner(self, item) -> None:
        is_deleted = bool(item.deleted)
        for panel in (self.detail_panel, self.space_panel, self.function_panel):
            setter = getattr(panel, "set_deleted_notice", None)
            if setter is not None:
                setter(is_deleted)
        if is_deleted:
            self.set_status(
                f"《{item.display_title}》在回收站里 —— 用「编辑 → 从回收站恢复」或顶部提示条恢复",
                timeout=0,
            )

    def refresh_history(self) -> None:
        if self._tearing_down:
            return
        item = (
            self.db.repository.get_item(self._current_entry_id)
            if self._current_entry_id else None
        )
        if item is None:
            self.history_panel.set_entry(None)
            return
        revisions = self.db.repository.revisions(item.id, descending=False)
        cursor_rev = self.db.repository.history.current_revision(item.id)
        self.history_panel.set_entry(
            item,
            list(reversed(revisions)),
            can_undo=self.db.repository.can_undo(item.id),
            can_redo=self.db.repository.can_redo(item.id),
            cursor_revision_id=cursor_rev.id if cursor_rev else "",
            diff_provider=lambda rid: self.db.repository.revision_diff(rid),
        )

    def update_titles(self) -> None:
        name = self.db.repository.name
        path = self.db.path or "未保存"
        dirty = " •" if self.db.repository.dirty else ""
        self.setWindowTitle(f"{name}{dirty} — {os.path.basename(path) if self.db.path else path} — {APP_NAME}")
        self.status_db.setText(
            (os.path.basename(self.db.path) if self.db.path else "未保存") + dirty
        )
        self.status_dirty.setText("未保存的改动" if self.db.repository.dirty else "")

    def update_status_counts(self, visible: int) -> None:
        repo = self.db.repository
        stats = repo.statistics()
        self.status_counts.setText(
            f"显示 {visible} / 模块 {stats['modules']} · 空间 {stats['spaces']} · "
            f"函数体 {stats['functions']} · {stats['revisions']} 修订"
        )
        if hasattr(self, "kind_count_label"):
            self.kind_count_label.setText(
                f"模块 {stats['modules']} · 空间 {stats['spaces']} · 函数体 {stats['functions']}"
            )

    def update_actions(self) -> None:
        has_item = bool(self._current_entry_id)
        item = self.db.repository.get_item(self._current_entry_id) if has_item else None
        kind = getattr(item, "kind", "module") if item else "module"

        # 通用
        self.act_delete_entry.setEnabled(has_item and not (item and item.deleted))
        self.act_restore_entry.setEnabled(bool(item and item.deleted))
        self.act_purge_entry.setEnabled(has_item)
        self.act_duplicate_entry.setEnabled(has_item)
        self.act_toggle_favorite.setEnabled(has_item)
        self.act_set_status.setEnabled(has_item)
        self.act_new_tag_on_entry.setEnabled(has_item)
        self.act_undo.setEnabled(has_item and self.db.repository.can_undo(self._current_entry_id))
        self.act_redo.setEnabled(has_item and self.db.repository.can_redo(self._current_entry_id))
        self.act_copy_entry_md.setEnabled(has_item)
        self.act_copy_entry_json.setEnabled(has_item)
        self.act_container_info.setEnabled(self.db.path is not None)

        # 按类别启用专属动作
        self.act_edit_entry.setEnabled(has_item and kind == "module")
        self.act_add_impl.setEnabled(has_item and kind == "module")
        self.act_edit_impl.setEnabled(has_item and kind == "module")
        self.act_copy_code.setEnabled(has_item and not (item and item.deleted))
        self.act_edit_space.setEnabled(has_item and kind == "space")
        self.act_edit_function.setEnabled(has_item and kind == "function")
        self.act_redetect_symbols.setEnabled(has_item and kind == "function")

    def set_status(self, message: str, timeout: int = 6000) -> None:
        self.status_message.setText(message)
        if timeout:
            QTimer.singleShot(timeout, lambda: self.status_message.setText("就绪"))

    # ==================================================================================
    # 选择 / 检索
    # ==================================================================================
    def on_entry_selected(self, entry_id: str) -> None:
        """用户在列表里切换选中项 (由 QListView.currentChanged 驱动)。

        ``refresh_list`` 重建列表期间会把该信号关掉 (``_suppress_selection_signal``),
        由它自己统一收口, 避免出现"先清空再填充"的中间态。
        """
        if self._suppress_selection_signal or self._tearing_down:
            return
        self._current_entry_id = entry_id
        item = self.db.repository.get_item(entry_id) if entry_id else None
        self._current_kind = getattr(item, "kind", "module") if item else "module"
        self.refresh_detail()
        self.update_actions()
        self.update_status_entry()

    def update_status_entry(self) -> None:
        item = (
            self.db.repository.get_item(self._current_entry_id)
            if self._current_entry_id else None
        )
        if item is None:
            self.status_entry.setText("")
            return
        self.status_entry.setText(
            f"[{item.kind_label}] {item.display_title} · {item.status_label} · "
            f"{item.badge_label} · {len(item.tags)} 标签"
        )

    def on_entry_activated(self, entry_id: str) -> None:
        """双击列表项: 按类别打开对应的编辑器。"""
        self._current_entry_id = entry_id
        item = self.db.repository.get_item(entry_id)
        self._current_kind = getattr(item, "kind", "module") if item else "module"
        self.refresh_detail()
        if self._current_kind == "space":
            self.edit_space()
        elif self._current_kind == "function":
            self.edit_function()
        else:
            self.edit_entry()

    def on_query_changed(self, text: str) -> None:
        self.query.text = text
        self.refresh_list(select_id=self._current_entry_id)

    def on_sort_changed(self, sort_key: str) -> None:
        self.query.sort_key = sort_key or "updated_desc"
        self.refresh_list(select_id=self._current_entry_id)

    def on_scope_changed(self, code: bool, description: bool) -> None:
        self.query.search_code = code
        self.query.search_description = description
        self.refresh_list(select_id=self._current_entry_id)

    def on_tags_changed(self, tags: List[str], mode: str) -> None:
        self.query.tags = list(tags)
        self.query.tag_mode = TagMatch(mode)
        self.refresh_list(select_id=self._current_entry_id)

    def on_tag_chip_clicked(self, tag: str) -> None:
        self.tag_panel.toggle_tag(tag)
        self._show_sidebar(0)

    def focus_search(self) -> None:
        self.search_bar.input.setFocus()
        self.search_bar.input.selectAll()

    def clear_search(self) -> None:
        self.search_bar.clear()
        self.tag_panel.clear_selection()
        self.query = QuerySpec(sort_key=self.search_bar.sort_key())
        self.refresh_list(select_id=self._current_entry_id)

    def _show_sidebar(self, index: int) -> None:
        self.sidebar_stack.setCurrentIndex(index)

    def _toggle_exclusive_tag_mode(self) -> None:
        self.tag_panel.set_mode(TagMatch.EXACT.value)

    # ==================================================================================
    # 条目 CRUD
    # ==================================================================================
    def new_entry(self) -> None:
        dialog = EntryEditorDialog(
            self,
            existing_tags=self.db.repository.all_tags(),
            tag_colors={k: v.color for k, v in self.db.repository.tags.items()},
            theme=self.theme,
        )
        if dialog.exec() != EntryEditorDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        try:
            entry = self.db.repository.create_entry(
                str(data["title"]),
                str(data["description"]),
                str(data["prerequisites"]),
                list(data["tags"]),
                status=str(data["status"]),
                favorite=bool(data["favorite"]),
                implementations=list(data["implementations"]),
            )
        except RepositoryError as exc:
            self.show_error("新建失败", str(exc))
            return
        self._current_entry_id = entry.id
        self.refresh_all(select_id=entry.id)
        self.set_status(f"已新建条目《{entry.display_title}》, 历史已记录 (1 条修订)")

    def edit_entry(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        dialog = EntryEditorDialog(
            self,
            entry=entry,
            existing_tags=self.db.repository.all_tags(),
            tag_colors={k: v.color for k, v in self.db.repository.tags.items()},
            theme=self.theme,
        )
        if dialog.exec() != EntryEditorDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        before_revisions = self.db.repository.history.count(entry.id)
        try:
            self.db.repository.apply_entry(
                entry.id,
                title=str(data["title"]),
                description=str(data["description"]),
                prerequisites=str(data["prerequisites"]),
                tags=list(data["tags"]),
                status=str(data["status"]),
                favorite=bool(data["favorite"]),
                implementations=list(data["implementations"]),
            )
        except RepositoryError as exc:
            self.show_error("保存失败", str(exc))
            return
        after_revisions = self.db.repository.history.count(entry.id)
        self.refresh_all(select_id=entry.id)
        if after_revisions > before_revisions:
            latest = self.db.repository.revisions(entry.id, descending=True)[0]
            self.set_status(f"已保存 — {latest.action_label}: {latest.summary}")
        else:
            self.set_status("内容没有变化, 未产生新的修订")

    def duplicate_entry(self) -> None:
        item = self._current_item()
        if item is None:
            return
        clone = self.db.repository.duplicate_item(item.id)
        self._current_entry_id = clone.id
        self._current_kind = clone.kind
        self.refresh_all(select_id=clone.id)
        self.set_status(f"已创建副本《{clone.display_title}》")

    def delete_entry(self) -> None:
        """删除当前对象 (软删除进回收站) —— 三类实体通用。"""
        item = self._current_item()
        if item is None:
            return
        if item.deleted:
            self.set_status("该对象已经在回收站里了")
            return
        answer = QMessageBox.question(
            self,
            f"删除{item.kind_label}",
            f"确定把{item.kind_label}《{item.display_title}》移入回收站吗?\n\n"
            "内容与全部实现都会保留在历史中, 可以随时从回收站恢复或回滚。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.repository.delete_item(item.id)
        self.refresh_all(select_id=item.id)
        self.set_status(f"《{item.display_title}》已移入回收站 (可在历史中恢复)")

    def restore_entry(self) -> None:
        item = self._current_item()
        if item is None or not item.deleted:
            return
        self.db.repository.restore_item(item.id)
        self.refresh_all(select_id=item.id)
        self.set_status(f"《{item.display_title}》已从回收站恢复")

    def purge_entry(self) -> None:
        item = self._current_item()
        if item is None:
            return
        answer = QMessageBox.warning(
            self,
            "彻底删除",
            f"将**彻底删除**{item.kind_label}《{item.display_title}》及其全部内容。\n\n"
            "历史记录仍会保留一条删除记录, 但对象本体不再显示。\n确定继续吗?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.repository.delete_item(item.id, hard=True)
        self._current_entry_id = ""
        self.refresh_all()
        self.set_status(f"《{item.display_title}》已彻底删除")

    def toggle_favorite(self) -> None:
        item = self._current_item()
        if item is None:
            return
        self.db.repository.toggle_favorite_item(item.id)
        self.refresh_all(select_id=item.id)
        self.set_status("已更新收藏状态")

    def set_planning_status(self) -> None:
        """设置当前对象在规划流程中的阶段 (三类实体通用)。"""
        item = self._current_item()
        if item is None:
            return
        labels = [STATUS_LABELS[s] for s in STATUS_ORDER]
        current = STATUS_ORDER.index(item.status) if item.status in STATUS_ORDER else 0
        label, ok = QInputDialog.getItem(
            self, "设置规划状态", "选择该对象所处的规划阶段:", labels, current, False
        )
        if not ok:
            return
        status = STATUS_ORDER[labels.index(label)]
        self.db.repository.set_item_status(item.id, status)
        self.refresh_all(select_id=item.id)
        self.set_status(f"状态已更新为「{STATUS_LABELS[status]}」")

    def set_status(self, message: str, timeout: int = 6000) -> None:
        """在状态栏显示一条消息 (``timeout=0`` 表示不自动清除)。"""
        self.status_message.setText(message)
        if timeout:
            QTimer.singleShot(timeout, self._reset_status_message)

    def _reset_status_message(self) -> None:
        self.status_message.setText("就绪")

    # ==================================================================================
    # 撤销 / 重做 / 回滚
    # ==================================================================================
    def undo(self) -> None:
        item = self._current_item()
        if item is None:
            return
        if not self.db.repository.can_undo(item.id):
            self.set_status("已经是最早的版本, 无法继续撤销")
            return
        result = self.db.repository.undo(item.id)
        self.refresh_all(select_id=item.id)
        if result is not None:
            self.set_status(
                f"已撤销到 {format_ts(self.db.repository.history.current_revision(item.id).timestamp)} "
                f"的版本 (历史记录未被删除)"
            )

    def redo(self) -> None:
        item = self._current_item()
        if item is None:
            return
        if not self.db.repository.can_redo(item.id):
            self.set_status("已经是最新的版本")
            return
        result = self.db.repository.redo(item.id)
        self.refresh_all(select_id=item.id)
        if result is not None:
            self.set_status("已重做到下一条修订")

    def restore_revision(self, entry_id: str, revision_id: str) -> None:
        revision = self.db.repository.history.revision(revision_id)
        if revision is None:
            return
        answer = QMessageBox.question(
            self,
            "回滚到历史版本",
            f"将把条目**全量**恢复到 {format_ts(revision.timestamp)} 的版本:\n\n"
            f"动作: {revision.action_label}\n说明: {revision.summary}\n\n"
            "当前内容会先被记录为一条新修订, 因此这个操作本身也可以再被撤销。确定继续吗?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.repository.restore_revision(entry_id, revision_id)
        self.refresh_all(select_id=entry_id)
        self.set_status(f"已全量回滚到 {format_ts(revision.timestamp)} 的版本, 并记录为一条新的修订")

    # ==================================================================================
    # 多语言实现 (同一个问题, 多种语言的解法)
    # ==================================================================================
    def add_implementation(self, entry_id: str = "") -> None:
        """为条目添加**另一种语言**的实现 (详情面板的「＋ 语言实现」)。"""
        entry = self.db.repository.get(entry_id) if entry_id else self._current_entry()
        if entry is None:
            self.set_status("请先选中一个条目")
            return

        dialog = ImplementationDialog(self, entry=entry, theme=self.theme)
        if dialog.exec() != ImplementationDialog.DialogCode.Accepted:
            return

        impl = dialog.result_implementation()
        created = self.db.repository.add_implementation(
            entry.id,
            impl.language,
            impl.code,
            title=impl.title,
            filename=impl.filename,
            notes=impl.notes,
        )
        self.refresh_all(select_id=entry.id)
        self.detail_panel.show_implementation(created.id)
        self.set_status(
            f"已为《{entry.display_title}》添加 {get_language(created.language).name} 实现 — "
            f"该条目现在有 {len(self.db.repository.require(entry.id).active_implementations)} 种语言实现"
        )

    def edit_implementation(self, entry_id: str = "", implementation_id: str = "") -> None:
        """编辑条目下的某一种语言实现。"""
        entry = self.db.repository.get(entry_id) if entry_id else self._current_entry()
        if entry is None:
            return
        impl_id = implementation_id or self.detail_panel.current_implementation_id()
        impl = entry.get_implementation(impl_id) if impl_id else None
        if impl is None:
            self.set_status("请先在「概览」里选择要编辑的语言实现")
            return

        dialog = ImplementationDialog(
            self, entry=entry, implementation=impl.clone(), theme=self.theme
        )
        if dialog.exec() != ImplementationDialog.DialogCode.Accepted:
            return

        updated = dialog.result_implementation()
        before_code = impl.code
        self.db.repository.update_implementation(
            entry.id,
            impl.id,
            language=updated.language,
            code=updated.code,
            title=updated.title,
            filename=updated.filename,
            notes=updated.notes,
        )
        self.refresh_all(select_id=entry.id)
        self.detail_panel.show_implementation(impl.id)
        if before_code != updated.code:
            added = sum(
                1 for line in updated.code.splitlines() if line not in before_code.splitlines()
            )
            self.set_status(
                f"已保存 {get_language(updated.language).name} 实现 "
                f"(代码 {len(before_code.splitlines())} → {len(updated.code.splitlines())} 行), 已记录到历史"
            )
        else:
            self.set_status(f"已保存 {get_language(updated.language).name} 实现的元信息")

    def delete_implementation(self, entry_id: str = "", implementation_id: str = "") -> None:
        """删除条目下的某一种语言实现 (软删除, 可从历史回滚)。"""
        entry = self.db.repository.get(entry_id) if entry_id else self._current_entry()
        if entry is None or not implementation_id:
            return
        impl = entry.get_implementation(implementation_id)
        if impl is None:
            return
        language = get_language(impl.language).name
        self.db.repository.delete_implementation(entry.id, implementation_id)
        self.refresh_all(select_id=entry.id)
        self.set_status(
            f"已删除 {language} 实现 (内容仍在历史中, 可用 Ctrl+Z 或回滚恢复)"
        )

    def show_history_panel(self) -> None:
        self.history_panel.setVisible(True)
        self.act_show_history.setChecked(True)
        self.main_splitter.setSizes([max(300, self.height() - 320), 300])

    def _toggle_history(self, visible: bool) -> None:
        # 该动作在 _build_ui() 之前就被 setChecked(True) 触发一次, 因此必须容错
        panel = getattr(self, "history_panel", None)
        splitter = getattr(self, "main_splitter", None)
        if panel is None or splitter is None:
            return
        panel.setVisible(visible)
        if visible:
            splitter.setSizes([max(300, self.height() - 300), 280])

    # ==================================================================================
    # 标签
    # ==================================================================================
    def manage_tags(self) -> None:
        dialog = TagManagerDialog(self.db.repository, self, theme=self.theme)
        dialog.changed.connect(lambda: self.refresh_all(select_id=self._current_entry_id))
        dialog.exec()
        self.refresh_all(select_id=self._current_entry_id)

    def add_tag_to_current(self) -> None:
        item = self._current_item()
        if item is None:
            return
        text, ok = QInputDialog.getText(
            self,
            "添加标签",
            "输入标签 (多个用空格或逗号分隔):\n现有标签: "
            + ("、".join(self.db.repository.all_tags()[:20]) or "（暂无）"),
        )
        if not ok or not text.strip():
            return
        new_tags = list(item.tags) + text.replace(",", " ").replace("，", " ").split()
        self.db.repository.set_item_tags(item.id, new_tags)
        self.refresh_all(select_id=item.id)
        self.set_status(f"已更新标签: {', '.join('#' + t for t in new_tags)}")

    def on_tag_context_menu(self, tag: str, global_pos) -> None:
        menu = QMenu(self)
        menu.addAction(f"筛选 #{tag}", lambda: self.on_tag_chip_clicked(tag))
        menu.addAction(f"仅显示 #{tag}", lambda: self._only_tag(tag))
        menu.addSeparator()
        related = related_tags(self.db.repository, tag, limit=8)
        if related:
            submenu = menu.addMenu("相关标签")
            for item in related:
                submenu.addAction("#" + item, lambda t=item: self.on_tag_chip_clicked(t))
        menu.addSeparator()
        menu.addAction("重命名 / 合并 / 删除…", self.manage_tags)
        menu.exec(global_pos)

    def _only_tag(self, tag: str) -> None:
        self.tag_panel.set_selected_tags([tag])
        self.tag_panel.set_mode(TagMatch.ALL.value)

    # ==================================================================================
    # 条目右键菜单
    # ==================================================================================
    def on_entry_context_menu(self, entry_id: str, global_pos) -> None:
        if entry_id:
            self._current_entry_id = entry_id
            self.entry_list.select_entry(entry_id)
            self.refresh_detail()
            self.update_actions()

        entry = self._current_entry()
        menu = QMenu(self)
        menu.addAction(self.act_edit_entry)
        menu.addAction(self.act_duplicate_entry)
        if entry is not None and entry.deleted:
            menu.addAction(self.act_restore_entry)
        else:
            menu.addAction(self.act_delete_entry)
        menu.addSeparator()
        menu.addAction(self.act_toggle_favorite)
        menu.addAction(self.act_set_status)
        menu.addAction(self.act_new_tag_on_entry)
        menu.addSeparator()
        menu.addAction(self.act_copy_code)
        menu.addAction(self.act_copy_entry_md)
        menu.addAction(self.act_copy_entry_json)
        menu.addSeparator()
        menu.addAction("打开历史面板", self.show_history_panel)
        menu.exec(global_pos)

    # ==================================================================================
    # 独立空间 (库内的完整项目)
    # ==================================================================================
    def new_space(self) -> None:
        dialog = SpaceEditorDialog(
            self,
            existing_tags=self.db.repository.all_tags(),
            theme=self.theme,
            limits=self.db.repository.limits,
        )
        if dialog.exec() != SpaceEditorDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        try:
            space = self.db.repository.create_space(
                str(data["name"]),
                str(data["description"]),
                str(data["prerequisites"]),
                list(data["tags"]),
                status=str(data["status"]),
                favorite=bool(data["favorite"]),
                files=list(data["files"]),
                entry_point=str(data["entry_point"]),
            )
        except RepositoryError as exc:
            self.show_error("新建空间失败", str(exc))
            return
        self.set_kind_filter("all")
        self._current_entry_id = space.id
        self._current_kind = "space"
        self.refresh_all(select_id=space.id)
        self.set_status(
            f"已新建空间《{space.display_title}》: {len(space.files)} 个文件 · {space.language_summary()}"
        )

    def edit_space(self, space_id: str = "") -> None:
        space = self._current_space(space_id)
        if space is None:
            self.set_status("请先选中一个空间")
            return
        dialog = SpaceEditorDialog(
            self,
            space=space,
            existing_tags=self.db.repository.all_tags(),
            theme=self.theme,
            limits=self.db.repository.limits,
        )
        if dialog.exec() != SpaceEditorDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        before = len(space.files)
        try:
            updated = self.db.repository.update_space(
                space.id,
                name=str(data["name"]),
                description=str(data["description"]),
                prerequisites=str(data["prerequisites"]),
                tags=list(data["tags"]),
                status=str(data["status"]),
                favorite=bool(data["favorite"]),
                entry_point=str(data["entry_point"]),
                files=list(data["files"]),
            )
        except RepositoryError as exc:
            self.show_error("保存空间失败", str(exc))
            return
        self.refresh_all(select_id=space.id)
        changed = len(updated.files) - before
        self.set_status(
            f"已保存空间《{updated.display_title}》"
            + (f" (文件数 {before} → {len(updated.files)})" if changed else "")
            + f" · {updated.language_summary()}"
        )

    def _current_space(self, space_id: str = ""):
        if space_id:
            return self.db.repository.spaces.get(space_id)
        item = self.db.repository.get_item(self._current_entry_id) if self._current_entry_id else None
        if getattr(item, "kind", "") == "space":
            return item
        return None

    def _require_space(self, space_id: str):
        space = self.db.repository.spaces.get(space_id)
        if space is None:
            self.set_status("请先选中一个空间")
        return space

    def open_space_file(self, space_id: str, path: str) -> None:
        if self._require_space(space_id) is None:
            return
        self.space_panel.show_file(path)
        self.set_status(f"已打开 {path}")

    def add_space_file(self, space_id: str, directory: str = "") -> None:
        """新建一个文件。``space_id`` 允许带 ``\\x00<目录>`` 后缀以指定所在目录。"""
        if "\x00" in space_id:
            space_id, directory = space_id.split("\x00", 1)
        space = self._require_space(space_id)
        if space is None:
            return
        default = f"{directory}/new_file.py" if directory else "new_file.py"
        text, ok = QInputDialog.getText(self, "新建文件", "相对路径 (用 / 分隔):", text=default)
        if not ok:
            return
        path = str(text).strip()
        if not path:
            return
        try:
            file = self.db.repository.put_space_file(space.id, path, "")
        except RepositoryError as exc:
            self.show_error("新建文件失败", str(exc))
            return
        self.refresh_all(select_id=space.id)
        self.space_panel.show_file(file.path)
        self.edit_space_file(space.id, file.path)

    def edit_space_file(self, space_id: str, path: str) -> None:
        space = self._require_space(space_id)
        if space is None:
            return
        file = space.get_file(path)
        if file is None:
            self.set_status(f"文件不存在: {path}")
            return
        if file.binary:
            self.reimport_space_file(space.id, path)
            return

        dialog = ThemedDialog(self)
        dialog.setWindowTitle(f"编辑 {path}")
        # 最小尺寸留小 + 显示时按屏幕夹一次, 保证底部的「保存」永远在屏幕里
        dialog.setMinimumSize(460, 300)
        dialog.resize(900, 660)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        info = QLabel(f"{path} · {get_language(file.language).name}", dialog)
        info.setObjectName("MutedLabel")
        layout.addWidget(info)
        editor = CodeEditor(dialog, language=file.language, theme=self.theme)
        editor.setPlainText(file.content)
        layout.addWidget(editor, 1)
        note_row = QHBoxLayout()
        note_row.addWidget(QLabel("文件说明", dialog))
        note_edit = QLineEdit(dialog)
        note_edit.setPlaceholderText("可选")
        note_edit.setText(file.note)
        note_row.addWidget(note_edit, 1)
        layout.addLayout(note_row)
        buttons = QDialogButtonBox(dialog)
        save_button = buttons.addButton("保存 (Ctrl+S)", QDialogButtonBox.ButtonRole.AcceptRole)
        save_button.setToolTip("快捷键 Ctrl+S")
        buttons.addButton("取消 (Esc)", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(
            dialog.reject
        )
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        # Ctrl+S 直接保存 —— 不用去够那个按钮
        save_action = QAction(dialog)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(dialog.accept)
        dialog.addAction(save_action)
        editor.setFocus()

        if dialog.exec() != ThemedDialog.DialogCode.Accepted:
            return
        try:
            self.db.repository.put_space_file(
                space.id, path, editor.toPlainText(), note=note_edit.text().strip()
            )
        except RepositoryError as exc:
            self.show_error("保存文件失败", str(exc))
            return
        self.refresh_all(select_id=space.id)
        self.space_panel.show_file(path)
        self.set_status(f"已保存 {path}")

    # ==================================================================================
    # 导入 / 导出外部文件 (内容直接嵌入 .cmdb, 不是引用)
    # ==================================================================================
    def import_space_files(self, space_id: str, target: str = "") -> None:
        """导入磁盘上的一个或多个文件。``target`` 可以是目录, 或 ``\\x00<路径>`` 表示替换某个文件。"""
        space = self._require_space(space_id)
        if space is None:
            return
        replace_path = ""
        directory = target
        if target.startswith("\x00"):
            replace_path = target[1:]
            directory = ""
        elif target == "\x01":
            # "导入整个目录"
            self.import_space_directory(space.id)
            return

        if replace_path:
            source, _filter = QFileDialog.getOpenFileName(
                self, f"选择要嵌入 {replace_path} 的文件", "", "所有文件 (*.*)"
            )
            if not source:
                return
            try:
                raw = Path(source).read_bytes()
            except OSError as exc:
                self.show_error("读取失败", str(exc.strerror or exc))
                return
            try:
                file = self.db.repository.put_space_file(
                    space.id, replace_path, binary_data=raw
                )
            except RepositoryError as exc:
                self.show_error("导入失败", str(exc))
                return
            self.refresh_all(select_id=space.id)
            self.space_panel.show_file(file.path)
            self.set_status(
                f"已把 {Path(source).name} ({human_bytes(file.size)}) 嵌入 {file.path}"
            )
            return

        chosen, _filter = QFileDialog.getOpenFileNames(
            self,
            "导入外部文件 (内容会被嵌入代码库, 不是引用)",
            "",
            "所有文件 (*.*)",
        )
        if not chosen:
            return
        self._embed_external(space.id, chosen, base_dir="", target_dir=directory)

    def show_limits_dialog(self) -> None:
        """查看 / 修改当前代码库的存储限制 (默认全部不限制)。"""
        dialog = LimitsDialog(self, repository=self.db.repository, theme=self.theme)
        if dialog.exec() != LimitsDialog.DialogCode.Accepted:
            return
        limits = self.db.repository.set_limits(dialog.result_limits())
        self.refresh_stats()
        self.set_status(f"存储限制已更新: {limits.summary}")

    def import_directory_into_space(self, space_id: str = "") -> None:
        """「把整个目录打包进空间」的统一入口 (菜单 / 工具栏 / 详情面板都走这里)。

        没有指定空间时, 按当前选中项推断; 推断不出来就**让用户挑一个** ——
        这个功能不该因为"当前选中的是模块"就用不了。
        """
        target = space_id or ""
        if not target:
            item = self._current_item()
            if getattr(item, "kind", "") == "space":
                target = item.id
        if not target:
            spaces = [s for s in self.db.repository.spaces.values() if not s.deleted]
            if not spaces:
                answer = QMessageBox.question(
                    self,
                    "还没有空间",
                    "整目录导入需要先有一个空间来承载。\n\n现在新建一个吗?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                self.new_space()
                return
            labels = [f"{s.display_title}  ({len(s.files)} 个文件)" for s in spaces]
            label, ok = QInputDialog.getItem(
                self, "选择目标空间", "把整个目录打包进哪个空间:", labels, 0, False
            )
            if not ok:
                return
            target = spaces[labels.index(label)].id
        self.import_space_directory(target)

    def import_space_directory(self, space_id: str) -> None:
        """把一整个外部目录 (含全部子文件夹) 打包进空间。

        先用 :class:`DirectoryImportDialog` 把计划完整摊给用户看 (多少文件、多少字节、
        哪些会被跳过以及为什么), 确认后直接用**同一份计划**执行, 不再重复扫描。
        """
        space = self._require_space(space_id)
        if space is None:
            return
        dialog = DirectoryImportDialog(
            self,
            theme=self.theme,
            budget_left=(
                space.binary_budget_left(self.db.repository.limits.max_space_binary_bytes)
                if self.db.repository.limits.max_space_binary_bytes
                else None
            ),
            max_text_bytes=self.db.repository.limits.max_text_bytes,
            max_binary_bytes=self.db.repository.limits.max_binary_bytes,
        )
        if dialog.exec() != DirectoryImportDialog.DialogCode.Accepted:
            return
        scan = dialog.scan()
        if scan is None or scan.error or not scan.included:
            return
        try:
            report = self.db.repository.import_scan(space.id, scan, force=dialog.force())
        except RepositoryError as exc:
            self.show_error("导入目录失败", str(exc))
            return
        self.set_kind_filter("all")
        self._current_entry_id = space.id
        self._current_kind = "space"
        self.refresh_all(select_id=space.id)
        self.set_status(
            f"已把 {Path(scan.root).name}/ 打包进《{space.display_title}》: {report.summary()}"
        )
        if report.skipped:
            QMessageBox.warning(
                self,
                "部分文件没有导入",
                f"成功 {report.changed} 个, 跳过 {len(report.skipped)} 个:\n\n"
                + "\n".join(f"{p} — {why}" for p, why in report.skipped[:12]),
            )

    def _embed_external(
        self, space_id: str, sources: List[str], *, base_dir: str, target_dir: str = ""
    ) -> None:
        """把外部文件嵌进空间; ``base_dir`` 非空时保留相对目录结构。"""
        space = self._require_space(space_id)
        if space is None:
            return
        try:
            report = self.db.repository.import_external_files(
                space_id, sources, base_dir=base_dir, target_dir=target_dir
            )
        except RepositoryError as exc:
            self.show_error("导入失败", str(exc))
            return
        self.refresh_all(select_id=space.id)
        self.set_status(f"已导入 {space.display_title}: {report.summary()}")
        if report.skipped:
            QMessageBox.warning(
                self,
                "部分文件未导入",
                f"成功 {report.changed} 个, 跳过 {len(report.skipped)} 个:\n\n"
                + "\n".join(f"{p} — {why}" for p, why in report.skipped[:12]),
            )

    def export_space_file(self, space_id: str, path: str) -> None:
        """把空间里的一个文件 (含嵌入的二进制) 还原到磁盘。"""
        space = self._require_space(space_id)
        if space is None:
            return
        file = space.get_file(path)
        if file is None:
            self.set_status(f"文件不存在: {path}")
            return
        target, _filter = QFileDialog.getSaveFileName(
            self, f"导出 {path}", Path(path).name, "所有文件 (*.*)"
        )
        if not target:
            return
        try:
            written = self.db.repository.export_space_file(space.id, path, target)
        except RepositoryError as exc:
            self.show_error("导出失败", str(exc))
            return
        self.set_status(f"已导出 {path} → {target} ({human_bytes(written)})")

    def add_space_directory(self, space_id: str) -> None:
        space = self._require_space(space_id)
        if space is None:
            return
        dialog = NewDirectoryDialog(
            self,
            theme=self.theme,
            default="src",
            already_exists=lambda path: any(
                f.path.startswith(path + "/") for f in space.files
            ),
        )
        if dialog.exec() != NewDirectoryDialog.DialogCode.Accepted:
            return
        directory, use_placeholder = dialog.result_data()
        if not directory:
            return
        if not use_placeholder:
            QMessageBox.information(
                self,
                "空目录不会保留",
                f"已记住你的选择: 新建目录不再自动放 {PLACEHOLDER_NAME}。\n\n"
                "容器只存文件, 所以往这个目录里加文件之前, 它不会出现在库里。",
            )
            return
        placeholder = f"{directory}/{PLACEHOLDER_NAME}"
        try:
            self.db.repository.put_space_file(space.id, placeholder, "")
        except RepositoryError as exc:
            self.show_error("新建目录失败", str(exc))
            return
        self.refresh_all(select_id=space.id)
        self.space_panel.show_file(placeholder)
        self.set_status(f"已新建目录 (占位文件 {placeholder})")

    def remove_space_placeholder(self, space_id: str, path: str) -> None:
        """删掉目录里的 .gitkeep 占位文件 (用户说这是"去留"的另一半)。"""
        space = self._require_space(space_id)
        if space is None:
            return
        directory = normalize_project_path(path)
        placeholder = f"{directory}/{PLACEHOLDER_NAME}" if directory else PLACEHOLDER_NAME
        file = space.get_file(placeholder)
        if file is None:
            self.set_status(f"{directory}/ 里没有 {PLACEHOLDER_NAME}")
            return
        siblings = [
            f for f in space.files
            if f.path.startswith(f"{directory}/" if directory else "") and f.path != placeholder
        ]
        warning = (
            f"{directory}/ 里还有 {len(siblings)} 个文件, 删掉占位文件后目录仍然存在。"
            if siblings
            else f"⚠ {directory}/ 里**没有其它文件**, 删掉占位文件后这个空目录就会消失。"
        )
        answer = QMessageBox.question(
            self,
            f"删除 {PLACEHOLDER_NAME}",
            f"确定删除 {placeholder} 吗?\n\n{warning}\n\n改动会进入历史, 可以回滚。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.repository.delete_space_file(space.id, placeholder)
        self.refresh_all(select_id=space.id)
        self.set_status(f"已删除占位文件 {placeholder}")

    def rename_space_file(self, space_id: str, path: str) -> None:
        space = self._require_space(space_id)
        if space is None:
            return
        text, ok = QInputDialog.getText(self, "重命名文件", "新路径:", text=path)
        if not ok or not text.strip() or text.strip() == path:
            return
        try:
            self.db.repository.rename_space_file(space.id, path, text.strip())
        except RepositoryError as exc:
            self.show_error("重命名失败", str(exc))
            return
        self.refresh_all(select_id=space.id)
        self.set_status(f"已重命名 {path} → {normalize_project_path(text)}")

    def delete_space_path(self, space_id: str, path: str) -> None:
        space = self._require_space(space_id)
        if space is None:
            return
        # 目录还是文件? 目录删除会连带其下全部文件
        is_directory = space.get_file(path) is None
        answer = QMessageBox.question(
            self,
            "删除目录" if is_directory else "删除文件",
            f"确定从空间《{space.display_title}》里删除 {path} 吗?"
            + ("\n(该目录下的所有文件都会被删除)" if is_directory else "")
            + "\n\n改动会进入历史, 可以回滚。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if is_directory:
            count = self.db.repository.delete_space_directory(space.id, path)
            self.set_status(f"已删除目录 {path}/ (共 {count} 个文件)")
        else:
            self.db.repository.delete_space_file(space.id, path)
            self.set_status(f"已删除文件 {path}")
        self.refresh_all(select_id=space.id)

    def search_readmes(self) -> None:
        dialog = ReadmeSearchDialog(self.db.repository, self, theme=self.theme)
        dialog.open_requested.connect(self._open_readme_hit)
        dialog.exec()

    def _open_readme_hit(self, space_id: str, path: str) -> None:
        self.set_kind_filter("all")
        self._current_entry_id = space_id
        self._current_kind = "space"
        self.refresh_all(select_id=space_id)
        self.space_panel.show_file(path)
        self.set_status(f"已跳到 {path}")

    # ==================================================================================
    # 函数体 (自动检测变量 + 含义表)
    # ==================================================================================
    def new_function(self) -> None:
        dialog = FunctionEditorDialog(
            self,
            existing_tags=self.db.repository.all_tags(),
            theme=self.theme,
        )
        if dialog.exec() != FunctionEditorDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        implementations = list(data["implementations"])
        try:
            function = self.db.repository.create_function(
                str(data["name"]),
                implementations=implementations,
                description=str(data["description"]),
                prerequisites=str(data["prerequisites"]),
                tags=list(data["tags"]),
                status=str(data["status"]),
                favorite=bool(data["favorite"]),
                detect=False,   # 符号表已经由对话框填好了
            )
        except RepositoryError as exc:
            self.show_error("新建函数体失败", str(exc))
            return
        self.set_kind_filter("all")
        self._current_entry_id = function.id
        self._current_kind = "function"
        self.refresh_all(select_id=function.id)
        self.set_status(
            f"已新建函数体《{function.display_title}》· {function.badge_label} · "
            f"{function.meanings_summary}"
        )

    def edit_function(self, function_id: str = "") -> None:
        function = self._current_function(function_id)
        if function is None:
            self.set_status("请先选中一个函数体")
            return
        dialog = FunctionEditorDialog(
            self,
            function=function,
            existing_tags=self.db.repository.all_tags(),
            theme=self.theme,
        )
        if dialog.exec() != FunctionEditorDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        try:
            updated = self.db.repository.update_function(
                function.id,
                name=str(data["name"]),
                description=str(data["description"]),
                prerequisites=str(data["prerequisites"]),
                implementations=list(data["implementations"]),
                tags=list(data["tags"]),
                status=str(data["status"]),
                favorite=bool(data["favorite"]),
            )
        except RepositoryError as exc:
            self.show_error("保存函数体失败", str(exc))
            return
        self.refresh_all(select_id=function.id)
        self.set_status(
            f"已保存函数体《{updated.display_title}》· {updated.badge_label} · "
            f"{updated.meanings_summary}"
        )

    def add_function_implementation(self, function_id: str = "") -> None:
        """给已有函数体再加一种语言的实现。"""
        function = self._current_function(function_id)
        if function is None:
            self.set_status("请先选中一个函数体")
            return
        used = set(function.languages)
        default = next(
            (lang for lang, _name in language_choices() if lang not in used), "plaintext"
        )
        dialog = FunctionEditorDialog(
            self,
            function=function,
            existing_tags=self.db.repository.all_tags(),
            theme=self.theme,
            default_language=default,
            initial_implementation=FunctionImplementation(
                language=normalize_language(default)
            ),
        )
        if dialog.exec() != FunctionEditorDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        try:
            updated = self.db.repository.update_function(
                function.id,
                name=str(data["name"]),
                description=str(data["description"]),
                prerequisites=str(data["prerequisites"]),
                implementations=list(data["implementations"]),
                tags=list(data["tags"]),
                status=str(data["status"]),
                favorite=bool(data["favorite"]),
            )
        except RepositoryError as exc:
            self.show_error("添加语言实现失败", str(exc))
            return
        self.refresh_all(select_id=function.id)
        self.set_status(
            f"《{updated.display_title}》现在有 {updated.badge_label} · {updated.meanings_summary}"
        )

    def edit_function_implementation(self, function_id: str, implementation_id: str) -> None:
        """只编辑某一种语言的实现 (对话框切到那一种语言, 其它语言保持不变)。"""
        function = self._current_function(function_id)
        if function is None:
            return
        impl = function.get_implementation(implementation_id)
        if impl is None:
            self.set_status("该语言实现不存在")
            return
        dialog = FunctionEditorDialog(
            self,
            function=function,
            existing_tags=self.db.repository.all_tags(),
            theme=self.theme,
        )
        dialog.pane.set_implementation(impl)
        dialog._focus_implementation(impl)
        if dialog.exec() != FunctionEditorDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        try:
            updated = self.db.repository.update_function(
                function.id,
                name=str(data["name"]),
                description=str(data["description"]),
                prerequisites=str(data["prerequisites"]),
                implementations=list(data["implementations"]),
                tags=list(data["tags"]),
                status=str(data["status"]),
                favorite=bool(data["favorite"]),
            )
        except RepositoryError as exc:
            self.show_error("保存语言实现失败", str(exc))
            return
        self.refresh_all(select_id=function.id)
        self.function_panel.show_implementation(implementation_id)
        self.set_status(f"已保存 {get_language(impl.language).name} 实现")

    def delete_function_implementation(self, function_id: str, implementation_id: str) -> None:
        function = self._current_function(function_id)
        if function is None:
            return
        impl = function.get_implementation(implementation_id)
        if impl is None or impl.deleted:
            return
        if len(function.active_implementations) <= 1:
            self.set_status("至少要保留一种语言的实现; 要删除整个函数体请用「删除函数体」")
            return
        answer = QMessageBox.question(
            self,
            "删除语言实现",
            f"确定删除《{function.display_title}》的 {get_language(impl.language).name} 实现吗?\n\n"
            "只影响这一种语言, 其它语言的实现不受影响; 操作会记入历史, 可以恢复。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.repository.delete_function_implementation(function.id, impl.id)
        self.refresh_all(select_id=function.id)
        self.set_status(
            f"已删除 {get_language(impl.language).name} 实现 — 还剩 "
            f"{len(self.db.repository.require_function(function.id).active_implementations)} 种语言"
        )

    def _current_function(self, function_id: str = ""):
        if function_id:
            return self.db.repository.functions.get(function_id)
        item = self.db.repository.get_item(self._current_entry_id) if self._current_entry_id else None
        if getattr(item, "kind", "") == "function":
            return item
        return None

    def redetect_symbols(self, function_id: str = "", implementation_id: str = "") -> None:
        function = self._current_function(function_id)
        if function is None:
            self.set_status("请先选中一个函数体")
            return
        added, updated = self.db.repository.detect_function_symbols(
            function.id, implementation_id
        )
        self.refresh_all(select_id=function.id)
        if implementation_id:
            self.function_panel.show_implementation(implementation_id)
        scope = (
            get_language(updated.get_implementation(implementation_id).language).name
            if implementation_id and updated.get_implementation(implementation_id)
            else f"{len(updated.active_implementations)} 种语言"
        )
        self.set_status(
            f"检测完成 ({scope}): 新增 {added} 个变量, 共 {updated.total_symbols} 个 · "
            f"{updated.meanings_summary} (已填写的含义保持不变)"
        )

    # ==================================================================================
    # 复制 / 导出
    # ==================================================================================
    def copy_current_code(self) -> None:
        """复制当前选中对象的代码 —— 模块 / 空间 / 函数体三类都支持。"""
        item = self._current_item()
        if item is None:
            return
        kind = getattr(item, "kind", "module")

        if kind == "space":
            self.space_panel.copy_current_file()
            return

        if kind == "function":
            impl_id = self.function_panel.current_implementation_id()
            impl = item.get_implementation(impl_id) if impl_id else None
            if impl is None or impl.deleted:
                actives = item.active_implementations
                if not actives:
                    self.set_status("该函数体还没有任何语言实现, 用「＋ 语言实现」添加")
                    return
                impl = actives[0]
            QApplication.clipboard().setText(impl.code)
            self.set_status(
                f"已复制 {get_language(impl.language).name} 实现 "
                f"({impl.line_count} 行, {len(impl.code)} 字符) 到剪贴板"
            )
            return

        entry = item if isinstance(item, Entry) else None
        if entry is None:
            return
        impl_id = self.detail_panel.current_implementation_id()
        impl = entry.get_implementation(impl_id) if impl_id else None
        if impl is None:
            actives = entry.active_implementations
            if not actives:
                self.set_status("该条目还没有任何语言实现, 用「＋ 语言实现」添加")
                return
            impl = actives[0]
        QApplication.clipboard().setText(impl.code)
        line_count = len(impl.code.splitlines())
        self.set_status(
            f"已复制 {get_language(impl.language).name} 实现 "
            f"({line_count} 行, {len(impl.code)} 字符) 到剪贴板"
        )

    def copy_entry_as(self, fmt: str) -> None:
        item = self._current_item()
        if item is None:
            return
        revisions = self.db.repository.revisions(item.id, descending=False)
        kind = getattr(item, "kind", "module")
        if fmt == "json":
            if kind == "space":
                text = exporter.space_to_json(item, revisions=revisions)
            elif kind == "function":
                text = exporter.function_to_json(item, revisions=revisions)
            else:
                text = exporter.entry_to_json(item, revisions=revisions)
        else:
            if kind == "space":
                text = exporter.space_to_markdown(item, revisions=revisions)
            elif kind == "function":
                text = exporter.function_to_markdown(item, revisions=revisions)
            else:
                text = exporter.entry_to_markdown(item, revisions=revisions)
        QApplication.clipboard().setText(text)
        self.set_status(f"已复制{item.kind_label}的 {fmt.upper()} 表示 ({len(text)} 字符) 到剪贴板")

    def on_export_requested(self, entry_id: str, fmt: str) -> None:
        self._current_entry_id = entry_id
        self.copy_entry_as(fmt)

    # ==================================================================================
    # 文件操作
    # ==================================================================================
    def _confirm_discard(self) -> bool:
        if not self.db.repository.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "有未保存的改动",
            f"《{self.db.repository.name}》有未保存的改动。要先保存吗?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save_library()
        return True

    def new_library(self) -> None:
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(self, "新建代码库", "代码库名称:", text="未命名代码库")
        if not ok:
            return
        self.db = Database.create(name or "未命名代码库")
        self._current_entry_id = ""
        self.query = QuerySpec(sort_key=self.search_bar.sort_key())
        self.refresh_all()
        self.set_status("已新建空代码库")

    def open_library(self) -> None:
        if not self._confirm_discard():
            return
        start_dir = self.settings.value("last_dir", "", type=str)
        path, _ = QFileDialog.getOpenFileName(self, "打开代码库", start_dir, FILE_FILTER)
        if not path:
            return
        self._load_path(path)

    def _load_path(self, path: str) -> bool:
        try:
            db = Database.open(path)
        except DatabaseError as exc:
            self.show_error("打开失败", str(exc))
            return False
        self.db = db
        self._current_entry_id = ""
        self.query = QuerySpec(sort_key=self.search_bar.sort_key())
        self.settings.setValue("last_dir", os.path.dirname(path))
        self._remember_recent(path)
        warnings = db.info.warnings if db.info else []
        self.refresh_all()
        message = f"已打开 {os.path.basename(path)} · {len(db.repository.entries)} 个条目"
        if warnings:
            message += f" · {len(warnings)} 条提示"
            QMessageBox.information(self, "打开提示", "\n".join("• " + w for w in warnings))
        self.set_status(message)
        return True

    def _remember_recent(self, path: str) -> None:
        recent = self.settings.value("recent", [], type=list) or []
        recent = [p for p in recent if p != path]
        recent.insert(0, path)
        self.settings.setValue("recent", recent[:10])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        """重建「最近打开」子菜单。"""
        menu = getattr(self, "recent_menu", None)
        if menu is None:
            return
        menu.clear()
        recent = [p for p in (self.settings.value("recent", [], type=list) or []) if p]
        if not recent:
            action = QAction("（暂无记录）", menu)
            action.setEnabled(False)
            menu.addAction(action)
            return
        for path in recent:
            action = QAction(os.path.basename(path), menu)
            action.setToolTip(path)
            action.setStatusTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self._open_recent(p))
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("清除记录", self._clear_recent)

    def _open_recent(self, path: str) -> None:
        if not os.path.exists(path):
            self.show_error("文件不存在", f"找不到文件:\n{path}\n\n将从最近列表中移除。")
            recent = [p for p in (self.settings.value("recent", [], type=list) or []) if p != path]
            self.settings.setValue("recent", recent)
            self._rebuild_recent_menu()
            return
        if not self._confirm_discard():
            return
        self._load_path(path)

    def _clear_recent(self) -> None:
        self.settings.setValue("recent", [])
        self._rebuild_recent_menu()

    def save_library(self) -> bool:
        if not self.db.path:
            return self.save_library_as()
        try:
            result = self.db.save()
        except DatabaseError as exc:
            self.show_error("保存失败", str(exc))
            return False
        self.settings.setValue("last_dir", os.path.dirname(result.path))
        self._remember_recent(result.path)
        self.refresh_stats()
        self.update_titles()
        self.set_status(result.describe())
        return True

    def save_library_as(self) -> bool:
        start_dir = self.settings.value("last_dir", "", type=str)
        default_name = os.path.join(start_dir or "", "codemethod.cmdb")
        path, _ = QFileDialog.getSaveFileName(self, "另存为", default_name, FILE_FILTER)
        if not path:
            return False
        if not os.path.splitext(path)[1]:
            path += ".cmdb"
        try:
            result = self.db.save(path)
        except DatabaseError as exc:
            self.show_error("保存失败", str(exc))
            return False
        self.settings.setValue("last_dir", os.path.dirname(path))
        self._remember_recent(path)
        self.refresh_all()
        self.set_status(result.describe())
        return True

    def import_library(self) -> None:
        start_dir = self.settings.value("last_dir", "", type=str)
        path, _ = QFileDialog.getOpenFileName(self, "导入 / 合并代码库", start_dir, IMPORT_FILTER)
        if not path:
            return
        try:
            data = self._read_any_source(path)
        except Exception as exc:
            self.show_error("导入失败", str(exc))
            return
        try:
            stats = self.db.merge_from(data)
        except Exception as exc:
            self.show_error("合并失败", str(exc))
            return
        self.refresh_all(select_id=self._current_entry_id)
        self.set_status(
            f"已从 {os.path.basename(path)} 导入 {stats['entries']} 个条目 "
            f"({stats['renamed']} 个因 ID 冲突被重命名, 携带 {stats['revisions']} 条历史)"
        )

    def _read_any_source(self, path: str) -> Dict:
        """从 .cmdb / .cmj / .zip / .json 读取一份仓储 dict。"""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".zip":
            return exporter.read_zip_export(path)
        if ext == ".json":
            with open(path, "r", encoding="utf-8") as handle:
                return exporter.read_json_export(handle.read())
        return Database.open(path).repository.to_dict()

    def export_markdown(self) -> None:
        path = self._ask_export_path("导出 Markdown", "codemethod.md", "Markdown (*.md)")
        if not path:
            return
        exporter.export_markdown(self.db.repository, path)
        self.set_status(f"已导出 Markdown 到 {path} ({human_size(os.path.getsize(path))})")

    def export_json(self) -> None:
        path = self._ask_export_path("导出 JSON", "codemethod.json", "JSON (*.json)")
        if not path:
            return
        size = exporter.export_json(self.db.repository, path)
        self.set_status(f"已导出 JSON 到 {path} ({human_size(size)})")

    def export_zip(self) -> None:
        path = self._ask_export_path("导出 ZIP", "codemethod.zip", "ZIP (*.zip)")
        if not path:
            return
        count = exporter.export_zip(self.db.repository, path)
        size = os.path.getsize(path)
        self.set_status(f"已导出 {count} 个条目 (含源码文件) 到 {path} ({human_size(size)})")

    def _ask_export_path(self, title: str, default_name: str, file_filter: str) -> str:
        start_dir = self.settings.value("last_dir", "", type=str)
        path, _ = QFileDialog.getSaveFileName(
            self, title, os.path.join(start_dir or "", default_name), file_filter
        )
        if path:
            self.settings.setValue("last_dir", os.path.dirname(path))
        return path

    def convert_format(self, binary: bool) -> None:
        extension = BINARY_EXTENSIONS[0] if binary else TEXT_EXTENSIONS[0]
        start_dir = self.settings.value("last_dir", "", type=str)
        base = os.path.splitext(os.path.basename(self.db.path or "codemethod"))[0]
        path, _ = QFileDialog.getSaveFileName(
            self,
            "转换容器格式",
            os.path.join(start_dir or "", base + extension),
            FILE_FILTER,
        )
        if not path:
            return
        try:
            result = self.db.convert(path, binary=binary)
        except DatabaseError as exc:
            self.show_error("转换失败", str(exc))
            return
        self.refresh_stats()
        self.set_status("格式转换完成 · " + result.describe())

    def show_container_info(self) -> None:
        if not self.db.path:
            self.set_status("当前库还没有保存到磁盘, 无法查看容器信息")
            return
        try:
            info = self.db.probe()
        except Exception as exc:
            self.show_error("读取容器信息失败", str(exc))
            return
        ContainerInfoDialog(info, self, theme=self.theme).exec()

    def verify_file(self) -> None:
        start_dir = self.settings.value("last_dir", "", type=str)
        default = self.db.path or start_dir
        path, _ = QFileDialog.getOpenFileName(self, "校验文件", default, FILE_FILTER)
        if not path:
            return
        ok, problems, info = Database.verify(path)
        VerifyResultDialog(path, ok, problems, info, self).exec()
        self.set_status("校验通过" if ok else f"校验未通过 ({len(problems)} 个问题)")

    # ==================================================================================
    # 主题 / 其它
    # ==================================================================================
    def _build_theme_menu(self, menu: QMenu) -> None:
        """按 THEMES 注册表生成主题菜单 (单选), 新增主题无需改这里。"""
        self._theme_actions: Dict[str, QAction] = {}
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for key, label in theme_names():
            theme = get_theme(key)
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(key == self.theme.key)
            action.setToolTip(
                f"{label} · {'深色' if theme.is_dark else '浅色'} · 主色 {theme.accent}"
            )
            action.triggered.connect(lambda _checked=False, k=key: self.set_theme_key(k))
            self._theme_group.addAction(action)
            menu.addAction(action)
            self._theme_actions[key] = action
        menu.addSeparator()
        menu.addAction(self.act_cycle_theme)

    def cycle_theme(self) -> None:
        """在全部主题间循环切换 (Ctrl+Shift+Y)。"""
        keys = list(self._theme_actions.keys()) or [t.key for t in THEMES.values()]
        current = self.theme.key
        index = keys.index(current) if current in keys else -1
        self.set_theme_key(keys[(index + 1) % len(keys)])

    def set_theme_key(self, key: str) -> None:
        self.theme = get_theme(key)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.theme)
        self.restyle()
        self.settings.setValue("theme", self.theme.key)

        action = getattr(self, "_theme_actions", {}).get(self.theme.key)
        if action is not None and not action.isChecked():
            action.setChecked(True)

        self.set_status(f"已切换到 {self.theme.label}")

    def restyle(self) -> None:
        """只按当前主题刷新自绘控件, **不重建数据与页面**。

        切换主题时没有必要重查条目、重建列表与详情页 —— 那既慢又会产生大量
        待销毁的控件。这里只更新各面板持有的 ``_theme`` 与需要重新生成的 HTML。
        """
        self.entry_list.set_theme(self.theme)
        self.detail_panel.set_theme(self.theme)
        self.history_panel.set_theme(self.theme)
        self.tag_panel.set_theme(self.theme)
        self.refresh_stats()      # 统计面板是 HTML, 颜色写死在文本里, 必须重生成
        self.update_titles()

    def _toggle_autosave(self, enabled: bool) -> None:
        self.settings.setValue("autosave", bool(enabled))
        if enabled:
            self._autosave_timer.start()
            self.set_status("已开启自动保存 (每 2 分钟, 仅在已有路径时生效)")
        else:
            self._autosave_timer.stop()
            self.set_status("已关闭自动保存")

    def _autosave(self) -> None:
        if self.db.repository.dirty and self.db.path:
            if self.save_library():
                self.set_status("自动保存完成")

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def show_shortcuts(self) -> None:
        from PySide6.QtWidgets import QDialogButtonBox

        from .native import ThemedDialog

        dialog = ThemedDialog(self)
        dialog.setWindowTitle("快捷键与检索语法")
        dialog.setMinimumSize(560, 480)
        layout = QVBoxLayout(dialog)
        browser = QPlainTextEdit(dialog)
        browser.setReadOnly(True)
        browser.setPlainText(
            """快捷键
  Ctrl+N            新建条目          Ctrl+E        编辑当前条目
  Ctrl+Shift+N      新建代码库        Ctrl+T        标签管理
  Ctrl+O            打开代码库        Ctrl+Shift+T  给当前条目添加标签
  Ctrl+S            保存              Ctrl+D        收藏 / 取消收藏
  Ctrl+Shift+S      另存为            Ctrl+Delete   删除条目 (移入回收站)
  Ctrl+I            导入 / 合并       Ctrl+Z        撤销 (上一条修订)
  Ctrl+F            聚焦检索框        Ctrl+Y        重做 (下一条修订)
  Esc               清空检索条件      Ctrl+H        显示/隐藏历史面板
  Ctrl+Shift+C      复制当前实现代码  Ctrl+Shift+M  复制条目为 Markdown
  F5                刷新              Ctrl+滚轮     缩放代码字号
  Ctrl+1 / Ctrl+2   切换侧边栏 (标签 / 统计)
  Ctrl+Shift+Y      循环切换主题 (5 套配色)
  F1                本帮助

检索语法 (检索框中直接输入)
  socket server            同时包含两个词 (AND)
  "tcp server"             短语匹配
  -deprecated              排除包含该词的结果
  tag:network              仅匹配标签
  lang:python              仅匹配某种语言的实现 (支持 py / go / cpp 等别名)
  status:done              仅匹配某个规划状态
  prereq:cargo             仅匹配前置要求 (通用 + 各语言自己的)
  is:favorite              仅收藏 (还有 is:deleted / is:multi / is:history / is:has_code)

前置要求 (分语言)
  条目左侧的「通用前置要求」放与语言无关的要求 (例如"需要理解双向链表");
  每种语言自己的工具链 / 版本 / 依赖写在右侧每个实现页签的「前置要求」里。
  同一个问题因此可以是: Python 版要 3.10+、Go 版要 1.21+、Rust 版要 cargo。
  详情页「概览」按语言列出它们, 每种语言的页签顶部也会再显示一次。

多标签组合
  在左侧标签面板勾选多个标签, 并选择:
    同时包含 (AND) / 包含任一 (OR) / 排除所选 (NOT) / 恰好等于 (EXACT)
  点击条目详情里的标签胶囊可以快速切换该标签的筛选状态。"""
        )
        layout.addWidget(browser)
        box = QDialogButtonBox(dialog)
        box.addButton("关闭", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(dialog.accept)
        layout.addWidget(box)
        dialog.exec()

    def show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)
        self.set_status(f"{title}: {message}", timeout=0)

    # ==================================================================================
    # 辅助
    # ==================================================================================
    def _current_item(self):
        """当前选中的对象 —— 可能是模块 / 独立空间 / 函数体中的任意一种。"""
        if not self._current_entry_id:
            return None
        return self.db.repository.get_item(self._current_entry_id)

    def _current_entry(self) -> Optional[Entry]:
        """当前选中的**模块**; 选中的是空间或函数体时返回 None。"""
        item = self._current_item()
        return item if isinstance(item, Entry) else None

    def _restore_geometry(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)
        sizes = self.settings.value("mainSplitter")
        if sizes:
            try:
                self.main_splitter.setSizes([int(s) for s in sizes])
            except (TypeError, ValueError):
                pass
        theme_key = self.settings.value("theme", "dark+", type=str)
        if theme_key != "dark+":
            self.set_theme_key(theme_key)

    def _save_geometry(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("mainSplitter", self.main_splitter.sizes())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 原生标题栏不受样式表控制, 需要在窗口出现后单独染色 (否则深色主题顶部会留白框)
        from .native import apply_titlebar_theme

        apply_titlebar_theme(self, self.theme)

        # 首次显示时把窗口夹进屏幕可用区域。首选尺寸 1440×900 在 1536×864 的屏幕上
        # 比可用高度还高 (还不算任务栏), 于是窗口底部的历史面板沉到屏幕外; 而对话框
        # 默认以父窗口为中心弹出 —— 父窗口下沉会把「保存」一起推到屏幕下方点不到。
        if not self._geometry_fitted:
            self._geometry_fitted = True
            from .layout_util import fit_window_to_screen

            fit_window_to_screen(self)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._confirm_discard():
            event.ignore()
            return
        self._save_geometry()
        # 关窗后组件很快会被析构, 但 QListView 的 currentChanged 仍可能在析构过程中触发;
        # 不断开的话槽函数会去访问已销毁的子控件而抛 RuntimeError (在 PySide6 里会 abort 进程)。
        self._tearing_down = True
        try:
            self.entry_list.selection_changed.disconnect()
            self.entry_list.entry_activated.disconnect()
        except (RuntimeError, TypeError):
            pass
        event.accept()


__all__ = ["MainWindow"]
