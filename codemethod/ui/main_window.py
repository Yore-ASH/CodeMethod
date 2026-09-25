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
from typing import Dict, List, Optional

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTextBrowser,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_VERSION
from ..core.languages import get_language
from ..core.models import STATUS_LABELS, STATUS_ORDER, Entry, format_ts
from ..core.query import QuerySpec, TagMatch, query_entries, related_tags
from ..core.repository import Repository, RepositoryError
from ..storage import exporter
from ..storage.container import BINARY_EXTENSIONS, TEXT_EXTENSIONS, human_size
from ..storage.database import Database, DatabaseError
from .dialogs.about_dialog import AboutDialog
from .dialogs.container_dialog import ContainerInfoDialog, VerifyResultDialog
from .dialogs.entry_editor import EntryEditorDialog
from .dialogs.tag_manager import TagManagerDialog
from .resources import app_icon
from .theme import Theme, apply_theme, get_theme
from .widgets.detail_panel import DetailPanel
from .widgets.entry_list import EntryListView
from .widgets.history_panel import HistoryPanel
from .widgets.search_bar import SearchBar
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
        self._suspend_refresh = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1080, 700)
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
        self.act_quit = self._act("退出", self.close, shortcut="Ctrl+Q")

        self.act_new_entry = self._act("新建条目", self.new_entry, shortcut="Ctrl+N", tip="新建一个功能条目")
        self.act_edit_entry = self._act("编辑条目", self.edit_entry, shortcut="Ctrl+E", tip="编辑当前条目与全部实现")
        self.act_duplicate_entry = self._act("创建副本", self.duplicate_entry)
        self.act_delete_entry = self._act("删除条目", self.delete_entry, shortcut="Ctrl+Delete", tip="移入回收站 (可在历史中恢复)")
        self.act_restore_entry = self._act("从回收站恢复", self.restore_entry)
        self.act_purge_entry = self._act("彻底删除…", self.purge_entry, tip="不可撤销地移除条目")
        self.act_toggle_favorite = self._act("收藏 / 取消收藏", self.toggle_favorite, shortcut="Ctrl+D")
        self.act_set_status = self._act("设置规划状态…", self.set_planning_status)

        self.act_undo = self._act("撤销", self.undo, shortcut="Ctrl+Z", tip="回退当前条目的上一条修订")
        self.act_redo = self._act("重做", self.redo, shortcut="Ctrl+Y", tip="前进当前条目的下一条修订")
        self.act_show_history = self._act("显示历史面板", self._toggle_history, shortcut="Ctrl+H", checkable=True, tip="显示/隐藏历史面板")
        self.act_show_history.setChecked(True)

        self.act_copy_code = self._act("复制当前实现代码", self.copy_current_code, shortcut="Ctrl+Shift+C", tip="复制到剪贴板")
        self.act_copy_entry_md = self._act("复制条目为 Markdown", lambda: self.copy_entry_as("markdown"), shortcut="Ctrl+Shift+M")
        self.act_copy_entry_json = self._act("复制条目为 JSON", lambda: self.copy_entry_as("json"))

        self.act_manage_tags = self._act("标签管理…", self.manage_tags, shortcut="Ctrl+T")
        self.act_new_tag_on_entry = self._act("给当前条目添加标签…", self.add_tag_to_current, shortcut="Ctrl+Shift+T")
        self.act_focus_search = self._act("检索…", self.focus_search, shortcut="Ctrl+F")
        self.act_clear_search = self._act("清空检索条件", self.clear_search, shortcut="Escape")
        self.act_refresh = self._act("刷新", self.refresh_all, shortcut="F5")

        self.act_dark_theme = self._act("Dark+ 主题", lambda: self.set_theme_key("dark+"))
        self.act_light_theme = self._act("Light+ 主题", lambda: self.set_theme_key("light"))
        self.act_about = self._act("关于 CodeMethod", self.show_about)
        self.act_shortcuts = self._act("快捷键说明", self.show_shortcuts, shortcut="F1")
        self.act_autosave = self._act("自动保存 (每 2 分钟)", self._toggle_autosave, checkable=True)
        self.act_autosave.setChecked(self.settings.value("autosave", False, type=bool))

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

        # ---- 中间: 检索 + 列表 ----
        left = QWidget(central)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self.search_bar = SearchBar(left, theme=self.theme)
        left_layout.addWidget(self.search_bar)
        self.entry_list = EntryListView(left, theme=self.theme)
        self.entry_list.setMinimumWidth(300)
        left_layout.addWidget(self.entry_list, 1)
        central.addWidget(left)

        # ---- 右侧: 详情 ----
        self.detail_panel = DetailPanel(central, theme=self.theme)
        self.detail_panel.setMinimumWidth(340)
        central.addWidget(self.detail_panel)

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
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        edit_menu = bar.addMenu("编辑(&E)")
        edit_menu.addAction(self.act_new_entry)
        edit_menu.addAction(self.act_edit_entry)
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
        view_menu.addSeparator()
        sidebar_menu = view_menu.addMenu("侧边栏")
        self.act_sidebar_tags = self._act("标签筛选", lambda: self._show_sidebar(0), shortcut="Ctrl+1")
        self.act_sidebar_stats = self._act("统计信息", lambda: self._show_sidebar(1), shortcut="Ctrl+2")
        sidebar_menu.addAction(self.act_sidebar_tags)
        sidebar_menu.addAction(self.act_sidebar_stats)
        theme_menu = view_menu.addMenu("主题")
        theme_menu.addAction(self.act_dark_theme)
        theme_menu.addAction(self.act_light_theme)
        view_menu.addSeparator()
        view_menu.addAction(self.act_autosave)

        tools_menu = bar.addMenu("工具(&T)")
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
        bar.addAction(self.act_edit_entry)
        bar.addAction(self.act_delete_entry)
        bar.addSeparator()
        bar.addAction(self.act_undo)
        bar.addAction(self.act_redo)
        bar.addSeparator()
        bar.addAction(self.act_open)
        bar.addAction(self.act_save)
        bar.addSeparator()
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

        self.history_panel.restore_requested.connect(self.restore_revision)
        self.history_panel.undo_requested.connect(lambda _id: self.undo())
        self.history_panel.redo_requested.connect(lambda _id: self.redo())

    # ==================================================================================
    # 刷新
    # ==================================================================================
    def refresh_all(self, *, select_id: str = "", keep_selection: bool = True) -> None:
        if self._suspend_refresh:
            return
        previous = select_id or (self._current_entry_id if keep_selection else "")
        self.refresh_tags()
        self.refresh_list(select_id=previous)
        self.refresh_stats()
        self.update_titles()
        self.update_actions()

    def refresh_tags(self) -> None:
        colors = {key: info.color for key, info in self.db.repository.tags.items()}
        self.tag_panel.set_tags(
            self.db.repository.all_tags(include_deleted=False),
            self.db.repository.tag_usage(),
            colors,
        )

    def refresh_list(self, *, select_id: str = "") -> None:
        revision_counts = {
            entry.id: self.db.repository.history.count(entry.id)
            for entry in self.db.repository.entries.values()
        }
        tag_colors = {key: info.color for key, info in self.db.repository.tags.items()}
        entries = query_entries(self.db.repository, self.query)
        self.entry_list.set_entries(
            entries, revision_counts=revision_counts, tag_colors=tag_colors
        )
        if select_id:
            self.entry_list.select_entry(select_id)
        self.update_status_counts(len(entries))

    def refresh_stats(self) -> None:
        self.stats_panel.update_stats(self.db.repository, self.db)

    def refresh_detail(self) -> None:
        entry = self.db.repository.get(self._current_entry_id) if self._current_entry_id else None
        tag_colors = {key: info.color for key, info in self.db.repository.tags.items()}
        self.detail_panel.set_entry(entry, tag_colors=tag_colors)
        self.refresh_history()

    def refresh_history(self) -> None:
        entry = self.db.repository.get(self._current_entry_id) if self._current_entry_id else None
        if entry is None:
            self.history_panel.set_entry(None)
            return
        revisions = self.db.repository.revisions(entry.id, descending=False)
        cursor_rev = self.db.repository.history.current_revision(entry.id)
        self.history_panel.set_entry(
            entry,
            list(reversed(revisions)),
            can_undo=self.db.repository.can_undo(entry.id),
            can_redo=self.db.repository.can_redo(entry.id),
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
            f"显示 {visible} / 共 {stats['entries']} 条目 · {stats['implementations']} 实现 · "
            f"{stats['revisions']} 修订"
        )

    def update_actions(self) -> None:
        has_entry = bool(self._current_entry_id)
        entry = self.db.repository.get(self._current_entry_id) if has_entry else None
        self.act_edit_entry.setEnabled(has_entry)
        self.act_delete_entry.setEnabled(has_entry and not (entry and entry.deleted))
        self.act_restore_entry.setEnabled(bool(entry and entry.deleted))
        self.act_purge_entry.setEnabled(has_entry)
        self.act_duplicate_entry.setEnabled(has_entry)
        self.act_toggle_favorite.setEnabled(has_entry)
        self.act_set_status.setEnabled(has_entry)
        self.act_new_tag_on_entry.setEnabled(has_entry)
        self.act_undo.setEnabled(has_entry and self.db.repository.can_undo(self._current_entry_id))
        self.act_redo.setEnabled(has_entry and self.db.repository.can_redo(self._current_entry_id))
        self.act_copy_code.setEnabled(has_entry)
        self.act_copy_entry_md.setEnabled(has_entry)
        self.act_copy_entry_json.setEnabled(has_entry)
        self.act_container_info.setEnabled(self.db.path is not None)

    def set_status(self, message: str, timeout: int = 6000) -> None:
        self.status_message.setText(message)
        if timeout:
            QTimer.singleShot(timeout, lambda: self.status_message.setText("就绪"))

    # ==================================================================================
    # 选择 / 检索
    # ==================================================================================
    def on_entry_selected(self, entry_id: str) -> None:
        if entry_id == self._current_entry_id:
            return
        self._current_entry_id = entry_id
        self.refresh_detail()
        self.update_actions()
        entry = self.db.repository.get(entry_id)
        if entry is not None:
            self.status_entry.setText(
                f"{entry.display_title} · {entry.status_label} · "
                f"{len(entry.active_implementations)} 实现 · {len(entry.tags)} 标签"
            )
        else:
            self.status_entry.setText("")

    def on_entry_activated(self, entry_id: str) -> None:
        self._current_entry_id = entry_id
        self.edit_entry()

    def on_query_changed(self, text: str) -> None:
        self.query.text = text
        self.refresh_list(select_id=self._current_entry_id)
        self.update_actions()

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
        self.update_actions()

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
        entry = self._current_entry()
        if entry is None:
            return
        clone = self.db.repository.duplicate_entry(entry.id)
        self._current_entry_id = clone.id
        self.refresh_all(select_id=clone.id)
        self.set_status(f"已创建副本《{clone.display_title}》")

    def delete_entry(self) -> None:
        entry = self._current_entry()
        if entry is None or entry.deleted:
            return
        answer = QMessageBox.question(
            self,
            "删除条目",
            f"确定把《{entry.display_title}》移入回收站吗?\n\n"
            "条目与全部实现都会被保留在历史中, 可以随时从回收站恢复或回滚。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.repository.delete_entry(entry.id)
        self.refresh_all(select_id=entry.id)
        self.set_status(f"《{entry.display_title}》已移入回收站 (可在历史中恢复)")

    def restore_entry(self) -> None:
        entry = self._current_entry()
        if entry is None or not entry.deleted:
            return
        self.db.repository.restore_entry(entry.id)
        self.refresh_all(select_id=entry.id)
        self.set_status(f"《{entry.display_title}》已从回收站恢复")

    def purge_entry(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        answer = QMessageBox.warning(
            self,
            "彻底删除",
            f"将**彻底删除**《{entry.display_title}》及其全部实现。\n\n"
            "历史记录仍会保留一条删除记录, 但条目本体不再显示。\n确定继续吗?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.repository.delete_entry(entry.id, hard=True)
        self._current_entry_id = ""
        self.refresh_all()
        self.set_status(f"《{entry.display_title}》已彻底删除")

    def toggle_favorite(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        self.db.repository.toggle_favorite(entry.id)
        self.refresh_all(select_id=entry.id)
        self.set_status("已更新收藏状态")

    def set_planning_status(self) -> None:
        """设置当前条目在规划流程中的阶段。"""
        entry = self._current_entry()
        if entry is None:
            return
        labels = [STATUS_LABELS[s] for s in STATUS_ORDER]
        current = STATUS_ORDER.index(entry.status) if entry.status in STATUS_ORDER else 0
        label, ok = QInputDialog.getItem(
            self, "设置规划状态", "选择该条目所处的规划阶段:", labels, current, False
        )
        if not ok:
            return
        status = STATUS_ORDER[labels.index(label)]
        self.db.repository.update_entry(entry.id, status=status)
        self.refresh_all(select_id=entry.id)
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
        entry = self._current_entry()
        if entry is None:
            return
        if not self.db.repository.can_undo(entry.id):
            self.set_status("已经是最早的版本, 无法继续撤销")
            return
        result = self.db.repository.undo(entry.id)
        self.refresh_all(select_id=entry.id)
        if result is not None:
            self.set_status(
                f"已撤销到 {format_ts(self.db.repository.history.current_revision(entry.id).timestamp)} "
                f"的版本 (历史记录未被删除)"
            )

    def redo(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        if not self.db.repository.can_redo(entry.id):
            self.set_status("已经是最新的版本")
            return
        result = self.db.repository.redo(entry.id)
        self.refresh_all(select_id=entry.id)
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
        entry = self._current_entry()
        if entry is None:
            return
        text, ok = QInputDialog.getText(
            self,
            "添加标签",
            "输入标签 (多个用空格或逗号分隔):\n现有标签: "
            + ("、".join(self.db.repository.all_tags()[:20]) or "（暂无）"),
        )
        if not ok or not text.strip():
            return
        new_tags = list(entry.tags) + text.replace(",", " ").replace("，", " ").split()
        self.db.repository.update_entry(entry.id, tags=new_tags)
        self.refresh_all(select_id=entry.id)
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
    # 复制 / 导出
    # ==================================================================================
    def copy_current_code(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        index = self.detail_panel.tabs.currentIndex()
        previews = self.detail_panel._previews
        if 0 < index <= len(previews):
            preview = previews[index - 1]
        elif previews:
            preview = previews[0]
        else:
            self.set_status("该条目没有可复制的代码")
            return
        text = preview.code()
        QApplication.clipboard().setText(text)
        self.set_status(
            f"已复制 {get_language(preview.language).name} 实现 "
            f"({text.count(chr(10)) + 1 if text else 0} 行, {len(text)} 字符) 到剪贴板"
        )

    def copy_entry_as(self, fmt: str) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        revisions = self.db.repository.revisions(entry.id, descending=False)
        if fmt == "json":
            text = exporter.entry_to_json(entry, revisions=revisions)
        else:
            text = exporter.entry_to_markdown(entry, revisions=revisions)
        QApplication.clipboard().setText(text)
        self.set_status(f"已复制条目的 {fmt.upper()} 表示 ({len(text)} 字符) 到剪贴板")

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
    def set_theme_key(self, key: str) -> None:
        self.theme = get_theme(key)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.theme)
        self.entry_list.set_theme(self.theme)
        self.detail_panel.set_theme(self.theme)
        self.history_panel.set_theme(self.theme)
        self.tag_panel.set_theme(self.theme)
        self.settings.setValue("theme", key)
        self.refresh_all(select_id=self._current_entry_id)
        self.set_status(f"已切换到 {self.theme.label}")

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
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
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
  F1                本帮助

检索语法 (检索框中直接输入)
  socket server            同时包含两个词 (AND)
  "tcp server"             短语匹配
  -deprecated              排除包含该词的结果
  tag:network              仅匹配标签
  lang:python              仅匹配某种语言的实现 (支持 py / go / cpp 等别名)
  status:done              仅匹配某个规划状态
  is:favorite              仅收藏 (还有 is:deleted / is:multi / is:history / is:has_code)

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
    def _current_entry(self) -> Optional[Entry]:
        if not self._current_entry_id:
            return None
        return self.db.repository.get(self._current_entry_id)

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

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._confirm_discard():
            event.ignore()
            return
        self._save_geometry()
        event.accept()


__all__ = ["MainWindow"]
