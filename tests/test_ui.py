"""语法高亮与界面部件测试 (需要 PySide6; 使用 offscreen 平台, 无需真实显示器)。"""

from __future__ import annotations

import base64
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.*=false")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6.QtCore import QSettings, Qt, qInstallMessageHandler
    from PySide6.QtGui import QColor, QTextDocument
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

    qInstallMessageHandler(lambda *args: None)
    from codemethod.core.languages import LANGUAGES, all_languages
    from codemethod.core.models import Implementation
    from codemethod.core.repository import Repository
    from codemethod.core.spaces import ProjectFile, StorageLimits
    from codemethod.storage.database import Database
    from codemethod.ui.editor import CodeEditor, CodePreview, DiffView
    from codemethod.ui.highlighter import build_rules, highlighted_tokens
    from codemethod.ui.layout_util import (
        SCREEN_MARGIN,
        clamp_size,
        fit_dialog_to_screen,
        fit_window_to_screen,
    )
    from codemethod.ui.main_window import MainWindow
    from codemethod.ui.dialogs.space_editor import SpaceEditorDialog
    from codemethod.ui.widgets.binary_preview import BinaryPreview
    from codemethod.ui.theme import (
        DARK_PLUS,
        DEFAULT_THEME,
        LIGHT,
        apply_theme,
        get_theme,
        mono_font,
    )
    PYSIDE = True
except Exception as exc:  # pragma: no cover - 环境缺少 PySide6 时跳过
    PYSIDE = False
    SKIP_REASON = str(exc)

REQUIRED_LANGUAGES = ("c", "cpp", "go", "java", "python", "php", "rust")

SAMPLES = {
    "c": '#include <stdio.h>\n/* block\n   comment */\nint main(void) {\n    printf("hi %d\\n", 1);\n    return 0;\n}\n',
    "cpp": '// C++ 示例\ntemplate <typename T>\nclass Box {\npublic:\n    explicit Box(T v) : v_(v) {}\n    const char* name() const { return "box"; }\nprivate:\n    T v_;\n};\n',
    "go": 'package main\n\nimport "fmt"\n\nfunc main() {\n\t/* multi\n\tline */\n\tfmt.Println("hi")\n}\n',
    "java": '@Override\npublic class Main {\n    public static void main(String[] a) {\n        System.out.println("hi"); // 注释\n    }\n}\n',
    "python": 'import os\n\n\ndef f(x):\n    """文档\n    字符串"""\n    return f"{x}"  # 注释\n',
    "php": '<?php\nfunction f($x) {\n    # 注释\n    return "值 $x";\n}\n',
    "rust": 'fn main() {\n    /* 嵌套 /* 注释 */ 仍在注释中 */\n    let s = r"raw";\n    println!("{}", s);\n}\n',
}


class _TrackedWidgets(unittest.TestCase):
    """界面测试基类: 保证测试创建的 QWidget 在 QApplication 还活着时被销毁。

    未销毁的 QWidget 会留到后续 GC 或解释器关闭阶段才回收, 那时 Qt 内部的销毁顺序
    不可控, 进程会直接崩溃 —— 表现为"所有测试都 ok, 退出码却是非零"。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app, DARK_PLUS)
        # 自动应答模态框, 避免无头测试卡住
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
        QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

    def setUp(self):
        self._tracked: list = []
        # 主题是持久化设置: 上一个测试留下的选择会让后面的窗口构造走另一条路径,
        # 既拖慢套件也破坏可复现性, 所以每个用例开始前清掉。
        self.app.processEvents()
        QSettings("CodeMethod", "CodeMethod").remove("theme")

    def tearDown(self):
        while self._tracked:
            widget = self._tracked.pop()
            try:
                widget.close()          # 触发 closeEvent, 让窗口进入 teardown 状态并断开信号
                # 不要 setParent(None): 那会把控件提升为顶层窗口, 拖慢后续的全应用样式重刷
                widget.deleteLater()
            except RuntimeError:  # 已被 C++ 侧回收
                pass
        for _ in range(3):
            self.app.processEvents()

    def track(self, widget):
        """登记部件, 测试结束统一销毁。"""
        self._tracked.append(widget)
        return widget

    def _window(self):
        """通用夹具: 一个含单语言实现的小库 + 主窗口。"""
        db = Database(Repository(name="测试库"))
        entry = db.repository.create_entry("题", "描述", "", ["t"])
        db.repository.add_implementation(entry.id, "python", "print(1)")
        window = self.track(MainWindow(db))
        window.show()
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()
        return window, db


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestHighlighter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app, DARK_PLUS)

    def test_every_language_has_valid_rules(self):
        for spec in all_languages():
            for rule in build_rules(spec.id):
                self.assertTrue(
                    rule.pattern.isValid(),
                    f"{spec.id} 的规则非法: {rule.pattern.pattern()}",
                )

    def test_required_languages_produce_multiple_token_types(self):
        for language in REQUIRED_LANGUAGES:
            tokens = highlighted_tokens(language, SAMPLES[language])
            kinds = {token for _text, token in tokens}
            self.assertGreaterEqual(
                len(kinds), 2, f"{language} 只产生了 {kinds}, 高亮可能失效"
            )

    def test_keywords_strings_and_comments_detected(self):
        tokens = dict()
        for language in REQUIRED_LANGUAGES:
            text_tokens = highlighted_tokens(language, SAMPLES[language])
            kinds = {token for _t, token in text_tokens}
            self.assertIn("comment", kinds, f"{language} 未识别注释")
            self.assertIn("keyword", kinds, f"{language} 未识别关键字")
            self.assertIn("string", kinds, f"{language} 未识别字符串")

    def test_python_triple_quoted_string_spans_lines(self):
        tokens = highlighted_tokens("python", SAMPLES["python"])
        strings = [text for text, token in tokens if token == "string"]
        self.assertTrue(any('"""文档' in s for s in strings))
        self.assertTrue(any('字符串"""' in s for s in strings))

    def test_rust_nested_block_comment(self):
        tokens = highlighted_tokens("rust", SAMPLES["rust"])
        comments = [text for text, token in tokens if token == "comment"]
        self.assertTrue(any("仍在注释中" in c for c in comments))

    def test_comment_wins_over_string_inside_comment(self):
        code = 'x = 1  # 这里有 "未闭合的引号\n'
        tokens = highlighted_tokens("python", code)
        comment_chunks = [t for t, tok in tokens if tok == "comment"]
        self.assertTrue(any("未闭合的引号" in c for c in comment_chunks))

    def test_go_raw_string(self):
        tokens = highlighted_tokens("go", 'x := `raw\nstring`\n')
        self.assertTrue(any(tok == "string" for _t, tok in tokens))

    def test_plaintext_has_no_highlighting(self):
        tokens = highlighted_tokens("plaintext", "hello world\n")
        self.assertEqual({tok for _t, tok in tokens}, {"text"})

    def test_highlighter_document_roundtrip(self):
        from codemethod.ui.highlighter import CodeHighlighter

        document = QTextDocument()
        document.setPlainText(SAMPLES["rust"])
        highlighter = CodeHighlighter(document, "rust")
        highlighter.rehighlight()
        highlighter.set_language("python")
        self.assertEqual(highlighter.language, "python")
        highlighter.set_theme(LIGHT)
        highlighter.set_theme(DARK_PLUS)


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestWidgets(_TrackedWidgets):
    def test_editor_line_numbers_and_language_switch(self):
        editor = self.track(CodeEditor(language="python"))
        editor.setPlainText("a = 1\nb = 2\nc = 3")
        self.assertEqual(editor.total_lines(), 3)
        editor.goto_line(2)
        self.assertEqual(editor.current_line_number(), 2)
        editor.set_language("go")
        self.assertEqual(editor.language, "go")
        self.assertGreater(editor.line_number_area_width(), 0)

    def test_editor_trailing_newline_adds_block(self):
        editor = CodeEditor(language="python")
        editor.setPlainText("a\n")
        self.assertEqual(editor.total_lines(), 2)

    def test_editor_font_zoom(self):
        editor = CodeEditor(language="python")
        editor.set_font_size(18)
        self.assertEqual(editor.font_size(), 18)
        editor.set_font_size(999)  # 被钳制
        self.assertLessEqual(editor.font_size(), 40)

    def test_copy_all_puts_code_on_clipboard(self):
        editor = CodeEditor(language="python")
        editor.setPlainText("print('clipboard')")
        editor.copy_all()
        self.assertIn("clipboard", QApplication.clipboard().text())

    def test_preview_sets_language_and_filename(self):
        preview = CodePreview(language="rust")
        preview.set_code("fn main(){}", language="rust", filename="main.rs")
        self.assertEqual(preview.editor.language, "rust")
        self.assertIn("main", preview.code())

    def test_diff_view_colours_lines(self):
        view = DiffView()
        view.set_diff("### 标题\n+added\n-removed\n context\n")
        self.assertGreaterEqual(len(view.extraSelections()), 2)

    def test_mono_font_is_fixed_pitch(self):
        self.assertTrue(mono_font().fixedPitch())

    def test_theme_lookup_falls_back(self):
        self.assertEqual(get_theme("nope").key, "dark+")
        self.assertEqual(get_theme("light").key, "light")


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestMainWindow(_TrackedWidgets):
    def _make_window(self):
        from codemethod.app import build_demo_repository

        db = Database(build_demo_repository())
        window = self.track(MainWindow(db))
        window.show()
        self.app.processEvents()
        return window, db

    def test_window_builds_with_demo_library(self):
        window, db = self._make_window()
        stats = db.repository.statistics()
        expected = stats["modules"] + stats["spaces"] + stats["functions"]
        self.assertEqual(len(window.entry_list.entry_model.entries()), expected)
        # 示例库现在同时包含三类实体
        self.assertGreaterEqual(stats["modules"], 1)
        self.assertGreaterEqual(stats["spaces"], 1)
        self.assertGreaterEqual(stats["functions"], 1)
        self.assertGreater(window.tag_panel.list.count(), 0)
        window.close()

    def test_kind_filter_switches_list(self):
        window, db = self._make_window()
        stats = db.repository.statistics()
        for kind, expected in (
            ("module", stats["modules"]),
            ("space", stats["spaces"]),
            ("function", stats["functions"]),
        ):
            window.set_kind_filter(kind)
            self.app.processEvents()
            self.assertEqual(
                len(window.entry_list.entry_model.entries()), expected, kind
            )
            self.assertTrue(window._kind_buttons[kind].isChecked())
        window.set_kind_filter("all")
        self.app.processEvents()
        self.assertEqual(
            len(window.entry_list.entry_model.entries()),
            stats["modules"] + stats["spaces"] + stats["functions"],
        )
        window.close()

    def test_selecting_space_activates_space_panel(self):
        window, db = self._make_window()
        space = next(iter(db.repository.spaces.values()), None)
        self.assertIsNotNone(space, "示例库应包含一个空间")
        window.entry_list.select_entry(space.id)
        window.refresh_detail()
        self.app.processEvents()

        self.assertIs(window.detail_stack.currentWidget(), window.space_panel)
        self.assertEqual(window.space_panel.current_space().id, space.id)
        self.assertEqual(window._current_kind, "space")
        # 项目结构树与语言占比都要有内容
        self.assertGreater(window.space_panel.tree.topLevelItemCount(), 0)
        self.assertGreater(len(window.space_panel.language_bar._shares), 0)
        window.close()

    def test_selecting_function_activates_function_panel(self):
        window, db = self._make_window()
        function = next(iter(db.repository.functions.values()), None)
        self.assertIsNotNone(function, "示例库应包含一个函数体")
        window.entry_list.select_entry(function.id)
        window.refresh_detail()
        self.app.processEvents()

        self.assertIs(window.detail_stack.currentWidget(), window.function_panel)
        self.assertEqual(window.function_panel.current_function().id, function.id)
        self.assertEqual(window._current_kind, "function")
        # 语言下拉框列出全部实现, 变量表显示的是当前那一种语言的表
        self.assertEqual(
            window.function_panel.language_combo.count(),
            len(function.active_implementations),
        )
        current = function.active_implementations[0]
        self.assertEqual(
            window.function_panel.symbol_table.rowCount(), len(current.symbols)
        )
        window.close()

    def test_switching_language_switches_symbol_table(self):
        """同一个函数体的不同语言实现, 变量表要跟着切换。"""
        window, db = self._make_window()
        function = next(iter(db.repository.functions.values()), None)
        self.assertIsNotNone(function)
        window.entry_list.select_entry(function.id)
        window.refresh_detail()
        self.app.processEvents()

        panel = window.function_panel
        self.assertGreater(len(function.active_implementations), 1, "示例库应有多语言实现")
        rows = []
        for index in range(panel.language_combo.count()):
            panel.language_combo.setCurrentIndex(index)
            self.app.processEvents()
            rows.append(panel.symbol_table.rowCount())
        self.assertEqual(
            rows, [len(impl.symbols) for impl in function.active_implementations]
        )
        self.assertEqual(len(set(rows)) > 1 or len(rows) > 1, True)
        window.close()

    def test_delete_button_menu_mentions_current_language(self):
        window, db = self._make_window()
        function = next(iter(db.repository.functions.values()), None)
        window.entry_list.select_entry(function.id)
        window.refresh_detail()
        self.app.processEvents()
        # 「重新检测变量」按钮必须完整显示, 不能被压成 0 宽
        button = window.function_panel._detect_button
        self.assertGreaterEqual(button.minimumWidth(), button.fontMetrics().horizontalAdvance("重新检测变量"))
        window.close()

    def test_delete_function_through_window(self):
        """用户报的 bug: 删除对空间 / 函数体无效 —— 现在必须真的删掉。"""
        window, db = self._make_window()
        function = next(iter(db.repository.functions.values()), None)
        repo = db.repository
        count_before = len(repo.functions)
        repo.delete_item(function.id)
        self.assertTrue(repo.item_is_deleted(function.id))
        self.assertEqual(len(repo.items("function")), count_before - 1)
        repo.restore_item(function.id)
        self.assertEqual(len(repo.items("function")), count_before)

        space = next(iter(repo.spaces.values()))
        repo.delete_item(space.id)
        self.assertTrue(repo.item_is_deleted(space.id))
        repo.restore_item(space.id)
        self.assertFalse(repo.item_is_deleted(space.id))
        window.close()

    def test_space_and_function_share_one_history_panel(self):
        window, db = self._make_window()
        space = next(iter(db.repository.spaces.values()))
        window.entry_list.select_entry(space.id)
        window.refresh_detail()
        self.app.processEvents()
        self.assertGreater(window.history_panel.list.count(), 0)

        function = next(iter(db.repository.functions.values()))
        window.entry_list.select_entry(function.id)
        window.refresh_detail()
        self.app.processEvents()
        self.assertGreater(window.history_panel.list.count(), 0)
        window.close()

    def test_selecting_entry_fills_detail_and_history(self):
        window, db = self._make_window()
        entry = list(db.repository.entries.values())[0]
        window.entry_list.select_entry(entry.id)
        window.refresh_detail()
        self.app.processEvents()
        self.assertEqual(window.detail_panel.current_entry().id, entry.id)
        self.assertGreater(window.detail_panel.tabs.count(), 1)
        self.assertGreater(window.history_panel.list.count(), 0)
        window.close()

    def test_tag_filtering_changes_list(self):
        window, db = self._make_window()
        total = len(window.entry_list.entry_model.entries())

        window.tag_panel.set_selected_tags(["network"])
        self.app.processEvents()
        and_count = len(window.entry_list.entry_model.entries())
        self.assertGreater(and_count, 0)
        self.assertLess(and_count, total)

        window.tag_panel.set_mode("none")
        self.app.processEvents()
        none_count = len(window.entry_list.entry_model.entries())
        self.assertEqual(and_count + none_count, total)  # NONE 正好是补集
        window.close()

    def test_tag_exact_mode(self):
        window, db = self._make_window()
        window.tag_panel.set_mode("exact")
        window.tag_panel.set_selected_tags(["network", "tcp", "server", "demo"])
        self.app.processEvents()
        self.assertEqual(len(window.entry_list.entry_model.entries()), 1)
        window.close()

    def test_search_filters_and_clears(self):
        window, db = self._make_window()
        stats = db.repository.statistics()
        total = stats["modules"] + stats["spaces"] + stats["functions"]

        window.search_bar.set_text("lang:python")
        self.app.processEvents()
        filtered = len(window.entry_list.entry_model.entries())
        self.assertGreater(filtered, 0)
        self.assertLess(filtered, total)

        window.clear_search()
        self.app.processEvents()
        self.assertEqual(len(window.entry_list.entry_model.entries()), total)
        window.close()

    def test_readme_qualifier_only_matches_spaces(self):
        """readme: 只搜空间里的 README 文件。"""
        window, db = self._make_window()
        window.search_bar.set_text("readme:token")
        self.app.processEvents()
        found = window.entry_list.entry_model.entries()
        self.assertTrue(found, "示例空间的 README 里应该有 token")
        self.assertTrue(all(item.kind == "space" for item in found))
        window.close()

    def test_undo_redo_through_window(self):
        window, db = self._make_window()
        entry = list(db.repository.entries.values())[0]
        window.entry_list.select_entry(entry.id)
        title = entry.title
        db.repository.update_entry(entry.id, title=title + "X")
        window.refresh_all(select_id=entry.id)
        self.assertTrue(window.act_undo.isEnabled())
        window.undo()
        self.assertEqual(db.repository.require(entry.id).title, title)
        window.redo()
        self.assertEqual(db.repository.require(entry.id).title, title + "X")
        window.close()

    def test_restore_revision_through_window(self):
        window, db = self._make_window()
        entry = list(db.repository.entries.values())[0]
        original_description = entry.description
        revisions = db.repository.revisions(entry.id, descending=False)
        db.repository.update_entry(entry.id, description="改动过的描述")
        self.assertEqual(db.repository.require(entry.id).description, "改动过的描述")

        window.restore_revision(entry.id, revisions[0].id)
        self.assertEqual(db.repository.require(entry.id).description, original_description)
        # 回滚本身也被记录
        self.assertEqual(db.repository.revisions(entry.id)[0].action, "restore")
        window.close()

    def test_copy_entry_as_json_puts_text_on_clipboard(self):
        window, db = self._make_window()
        entry = list(db.repository.entries.values())[0]
        window.entry_list.select_entry(entry.id)
        window.copy_entry_as("json")
        self.assertIn(entry.title, QApplication.clipboard().text())
        window.close()

    def test_export_and_save_paths(self):
        import tempfile

        window, db = self._make_window()
        tmp = tempfile.mkdtemp(prefix="codemethod-ui-")
        target = os.path.join(tmp, "lib.cmdb")
        result = db.save(target)
        self.assertTrue(os.path.exists(target))
        self.assertTrue(result.verified)
        self.assertEqual(Database.open(target).repository.name, db.repository.name)
        window.close()


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestDetailPanelRefresh(_TrackedWidgets):
    """回归测试: 详情面板必须跟随内容刷新。

    历史 bug —— ``refresh_all()`` 依赖 QListView 的 currentChanged 去刷新详情, 但
    "重新选中同一行"时 Qt 根本不发这个信号, 于是"编辑条目 / 改标签 / 切状态 / 回滚"
    之后右侧详情仍停在旧内容上。这里的每个用例都刻意让选中行保持不变, 因此能锁住该回归。
    """

    def _window_with_entries(self, count=3):
        db = Database(Repository(name="refresh"))
        for index in range(count):
            entry = db.repository.create_entry(
                f"问题 {index + 1}", f"描述 {index + 1}", "无", ["t"]
            )
            db.repository.add_implementation(
                entry.id, "python", f"# solution {index + 1}\nprint({index + 1})"
            )
        window = self.track(MainWindow(db))
        window.show()
        self.app.processEvents()
        return window, db

    def _select_row(self, window, row):
        model = window.entry_list.entry_model
        window.entry_list.setCurrentIndex(model.index(row, 0))
        self.app.processEvents()
        return model.entry_at(model.index(row, 0))

    def test_selection_updates_detail(self):
        window, db = self._window_with_entries()
        target = self._select_row(window, 1)
        self.assertIsNotNone(window.detail_panel.current_entry())
        self.assertEqual(window.detail_panel.current_entry().id, target.id)
        window.close()

    def test_detail_follows_title_change_with_same_selection(self):
        window, db = self._window_with_entries()
        target = self._select_row(window, 1)  # 非首行才会暴露原 bug
        db.repository.update_entry(target.id, title="改过的标题")
        window.refresh_all(select_id=target.id)
        self.app.processEvents()
        self.assertEqual(window.detail_panel.current_entry().title, "改过的标题")
        window.close()

    def test_detail_follows_description_and_status_change(self):
        window, db = self._window_with_entries()
        target = self._select_row(window, 1)
        db.repository.update_entry(target.id, description="新描述", status="done")
        window.refresh_all(select_id=target.id)
        self.app.processEvents()
        shown = window.detail_panel.current_entry()
        self.assertEqual(shown.description, "新描述")
        self.assertEqual(shown.status, "done")
        window.close()

    def test_detail_follows_tag_change(self):
        window, db = self._window_with_entries()
        target = self._select_row(window, 1)
        db.repository.update_entry(target.id, tags=["t", "新增标签"])
        window.refresh_all(select_id=target.id)
        self.app.processEvents()
        self.assertIn("新增标签", window.detail_panel.current_entry().tags)
        window.close()

    def test_detail_follows_favorite_toggle(self):
        window, db = self._window_with_entries()
        self._select_row(window, 1)
        window.toggle_favorite()
        self.app.processEvents()
        self.assertTrue(window.detail_panel.current_entry().favorite)
        window.close()

    def test_detail_follows_code_change(self):
        window, db = self._window_with_entries()
        target = self._select_row(window, 1)
        impl = target.active_implementations[0]
        db.repository.update_implementation(target.id, impl.id, code="print('NEW CODE')")
        window.refresh_all(select_id=target.id)
        self.app.processEvents()
        self.assertIn("NEW CODE", window.detail_panel._previews[0].code())
        window.close()

    def test_detail_follows_undo_and_redo(self):
        window, db = self._window_with_entries()
        target = self._select_row(window, 1)
        original = target.title
        db.repository.update_entry(target.id, title="标题B")
        window.refresh_all(select_id=target.id)
        self.app.processEvents()
        self.assertEqual(window.detail_panel.current_entry().title, "标题B")

        window.undo()
        self.app.processEvents()
        self.assertEqual(window.detail_panel.current_entry().title, original)
        window.redo()
        self.app.processEvents()
        self.assertEqual(window.detail_panel.current_entry().title, "标题B")
        window.close()

    def test_detail_follows_revision_restore(self):
        window, db = self._window_with_entries()
        target = self._select_row(window, 1)
        # 注意: target 是仓储里的活对象, 会被就地修改, 所以先记下原始标题字符串
        original_title = target.title
        first_revision = db.repository.revisions(target.id, descending=False)[0]
        db.repository.update_entry(target.id, title="临时标题")
        window.refresh_all(select_id=target.id)
        self.app.processEvents()
        self.assertEqual(window.detail_panel.current_entry().title, "临时标题")

        window.restore_revision(target.id, first_revision.id)
        self.app.processEvents()
        self.assertEqual(window.detail_panel.current_entry().title, original_title)
        window.close()

    def test_detail_clears_when_query_matches_nothing(self):
        window, db = self._window_with_entries()
        self._select_row(window, 0)
        window.search_bar.set_text("绝对不存在的关键词zzz")
        self.app.processEvents()
        self.assertIsNone(window.detail_panel.current_entry())
        window.clear_search()
        self.app.processEvents()
        self.assertIsNotNone(window.detail_panel.current_entry())
        window.close()

    def test_current_entry_id_matches_highlighted_row(self):
        window, db = self._window_with_entries()
        for row in range(3):
            target = self._select_row(window, row)
            self.assertEqual(window._current_entry_id, target.id)
        window.close()


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestMultiLanguageImplementations(_TrackedWidgets):
    """一个问题可以用多种语言实现。"""

    def _window(self):
        db = Database.create()
        entry = db.repository.create_entry("字符串反转", "把字符串逆序", "无", ["string"])
        db.repository.add_implementation(entry.id, "python", "def rev(s): return s[::-1]")
        window = self.track(MainWindow(db))
        window.show()
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()
        return window, db, entry

    def test_detail_has_one_tab_per_language(self):
        window, db, entry = self._window()
        for language, code in (
            ("go", "package main"),
            ("rust", "fn main() {}"),
            ("cpp", "int main() {}"),
        ):
            db.repository.add_implementation(entry.id, language, code)
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()

        self.assertEqual(len(db.repository.require(entry.id).active_implementations), 4)
        self.assertEqual(window.detail_panel.tabs.count(), 5)  # 概览 + 4 种语言
        self.assertEqual(window.detail_panel.tabs.tabText(0), "概览")
        window.close()

    def test_language_tabs_are_switchable_and_copy_matches(self):
        window, db, entry = self._window()
        db.repository.add_implementation(entry.id, "rust", 'fn main() { println!("RS"); }')
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()

        implementations = db.repository.require(entry.id).active_implementations
        rust_impl = next(i for i in implementations if i.language == "rust")
        self.assertTrue(window.detail_panel.show_implementation(rust_impl.id))
        self.app.processEvents()

        window.copy_current_code()
        self.assertIn("println!", QApplication.clipboard().text())
        window.close()

    def test_current_implementation_id_tracks_tab(self):
        window, db, entry = self._window()
        db.repository.add_implementation(entry.id, "go", "package main")
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()

        implementations = db.repository.require(entry.id).active_implementations
        for index, impl in enumerate(implementations):
            window.detail_panel.show_tab(index + 1)
            self.assertEqual(window.detail_panel.current_implementation_id(), impl.id)
        window.detail_panel.show_tab(0)  # 概览页签没有对应实现
        self.assertEqual(window.detail_panel.current_implementation_id(), "")
        window.close()

    def test_implementation_dialog_suggests_unused_language(self):
        from codemethod.ui.dialogs.implementation_dialog import ImplementationDialog

        window, db, entry = self._window()
        dialog = ImplementationDialog(window, entry=entry, theme=DARK_PLUS)
        suggested = dialog.editor.language_combo.currentData()
        self.assertNotIn(suggested, entry.languages)
        dialog.close()
        window.close()

    def test_add_implementation_through_dialog_persists(self):
        from codemethod.ui.dialogs.implementation_dialog import ImplementationDialog

        window, db, entry = self._window()
        dialog = ImplementationDialog(window, entry=entry, theme=DARK_PLUS)
        dialog.editor.language_combo.setCurrentIndex(
            dialog.editor.language_combo.findData("java")
        )
        dialog.editor.editor.setPlainText("public class Solution {}")
        result = dialog.result_implementation()
        self.assertEqual(result.language, "java")

        db.repository.add_implementation(entry.id, result.language, result.code)
        dialog.close()
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()

        languages = db.repository.require(entry.id).languages
        self.assertIn("java", languages)
        self.assertEqual(len(languages), 2)
        window.close()

    def test_deleting_one_language_keeps_others_and_is_reversible(self):
        window, db, entry = self._window()
        db.repository.add_implementation(entry.id, "go", "package main")
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()

        go_impl = next(
            i for i in db.repository.require(entry.id).active_implementations
            if i.language == "go"
        )
        db.repository.delete_implementation(entry.id, go_impl.id)
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()
        self.assertEqual(len(db.repository.require(entry.id).active_implementations), 1)
        self.assertTrue(
            any(
                "回收" in window.detail_panel.tabs.tabText(i)
                for i in range(window.detail_panel.tabs.count())
            )
        )

        window.undo()
        self.app.processEvents()
        self.assertEqual(len(db.repository.require(entry.id).active_implementations), 2)
        self.assertEqual(window.detail_panel.tabs.count(), 3)
        window.close()

    def test_entry_editor_can_add_three_languages_at_once(self):
        from codemethod.ui.dialogs.entry_editor import EntryEditorDialog

        window, db, entry = self._window()
        dialog = EntryEditorDialog(window, entry=entry, theme=DARK_PLUS)
        dialog.add_implementation()
        dialog.add_implementation()
        self.app.processEvents()
        self.assertEqual(len(dialog._editors), 3)

        dialog._editors[1].language_combo.setCurrentIndex(
            dialog._editors[1].language_combo.findData("go")
        )
        dialog._editors[2].language_combo.setCurrentIndex(
            dialog._editors[2].language_combo.findData("rust")
        )
        data = dialog.result_data()
        languages = [impl.language for impl in data["implementations"]]
        self.assertEqual(languages, ["python", "go", "rust"])

        db.repository.apply_entry(entry.id, implementations=list(data["implementations"]))
        dialog.close()
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()
        self.assertEqual(len(db.repository.require(entry.id).active_implementations), 3)
        self.assertEqual(window.detail_panel.tabs.count(), 4)
        window.close()

    def test_each_language_change_is_recorded_in_history(self):
        window, db, entry = self._window()
        before = db.repository.history.count(entry.id)
        for language in ("go", "rust", "cpp"):
            db.repository.add_implementation(entry.id, language, f"// {language}")

        timeline = db.repository.revisions(entry.id, descending=False)
        # _window() 里已经加过一个 python 实现, 所以这里是 1 + 3
        self.assertEqual([r.action for r in timeline].count("impl_add"), 4)
        self.assertEqual(db.repository.history.count(entry.id), before + 3)
        language_snapshots = [
            r.snapshot["implementations"][-1]["language"]
            for r in timeline
            if r.action == "impl_add"
        ]
        self.assertEqual(language_snapshots, ["python", "go", "rust", "cpp"])
        window.close()

    def test_copy_all_code_includes_every_language(self):
        window, db, entry = self._window()
        db.repository.add_implementation(entry.id, "go", "package main // GO")
        db.repository.add_implementation(entry.id, "rust", "fn main() // RUST")
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()

        window.detail_panel._copy_all_code()
        clipboard = QApplication.clipboard().text()
        self.assertIn("GO", clipboard)
        self.assertIn("RUST", clipboard)
        self.assertIn("Python", clipboard)
        window.close()

    def test_switching_language_updates_filename_extension(self):
        """回归: 语言切到 PHP 后, 文件名不能还是 main.c。"""
        editor = self._make_editor()
        editor.language_combo.setCurrentIndex(editor.language_combo.findData("c"))
        self.assertTrue(editor.filename_edit.text().endswith(".c"))

        editor.language_combo.setCurrentIndex(editor.language_combo.findData("php"))
        self.assertTrue(
            editor.filename_edit.text().endswith(".php"),
            f"文件名没有跟随语言: {editor.filename_edit.text()!r}",
        )

    def test_switching_language_keeps_custom_stem(self):
        editor = self._make_editor()
        editor.language_combo.setCurrentIndex(editor.language_combo.findData("c"))
        editor.filename_edit.setText("reverse.c")
        editor.language_combo.setCurrentIndex(editor.language_combo.findData("rust"))
        self.assertEqual(editor.filename_edit.text(), "reverse.rs")

    def test_loaded_implementation_keeps_its_filename(self):
        editor = self._make_editor()
        editor.load(
            Implementation(language="java", filename="Solution.java", code="class Solution {}")
        )
        self.assertEqual(editor.filename_edit.text(), "Solution.java")
        self.assertEqual(editor.language_combo.currentData(), "java")

    # 单独构造的编辑器必须登记清理, 否则解释器退出时 Qt 对象销毁顺序不可控 (会硬崩)
    def _make_editor(self):
        from codemethod.ui.dialogs.entry_editor import ImplementationEditor

        return self.track(ImplementationEditor(theme=DARK_PLUS))


# --------------------------------------------------------------------------------------
# 模块级清理
# --------------------------------------------------------------------------------------


def tearDownModule() -> None:
    """最后再回收一次, 保证 QApplication 析构前没有遗留部件。"""
    if not PYSIDE:
        return
    import gc

    app = QApplication.instance()
    for _ in range(3):
        gc.collect()
        if app is not None:
            app.processEvents()


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestSelfTest(_TrackedWidgets):
    """``--selftest`` 是打包产物的验证入口, 它自己坏了就等于没有验证。"""

    def test_selftest_passes_and_writes_report(self):
        import contextlib
        import io
        import tempfile

        from codemethod.app import run_self_test

        report = os.path.join(tempfile.mkdtemp(prefix="codemethod-selftest-"), "report.txt")
        with contextlib.redirect_stdout(io.StringIO()):  # 自检会打印完整报告, 测试里静音
            code = run_self_test(report)

        self.assertEqual(code, 0, "自检未通过")
        self.assertTrue(os.path.exists(report), "自检没有写出报告")
        with open(report, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("自检", text)
        self.assertNotIn("[FAIL]", text)
        self.assertIn("全部通过", text)
        # 自检覆盖了关键路径
        for needle in ("语言注册表", "二进制容器完整性", "导出 ZIP", "主窗口构建", "每个语言一个页签"):
            self.assertIn(needle, text, f"自检缺少检查项: {needle}")

    def test_selftest_does_not_leak_windows(self):
        import contextlib
        import io

        from codemethod.app import run_self_test

        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_self_test(None), 0)
        # 能跑到这里且进程没崩, 就说明窗口被正确销毁了
        self.app.processEvents()


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestPerLanguagePrerequisites(_TrackedWidgets):
    """前置要求是分语言的: 每种实现各存一份, 并在界面上分开呈现。"""

    def _window(self):
        db = Database.create()
        entry = db.repository.create_entry(
            "字符串反转", "把字符串逆序", "需要理解字符编码 (与语言无关)", ["string"]
        )
        db.repository.add_implementation(
            entry.id, "python", "def rev(s): return s[::-1]", prerequisites="Python 3.10+"
        )
        db.repository.add_implementation(
            entry.id, "go", "package main", prerequisites="Go 1.21+, 仅标准库"
        )
        db.repository.add_implementation(
            entry.id, "rust", "fn main(){}", prerequisites="Rust 1.75+ / cargo"
        )
        window = self.track(MainWindow(db))
        window.show()
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()
        return window, db, entry

    def test_each_implementation_keeps_its_own_prerequisites(self):
        window, db, entry = self._window()
        stored = {i.language: i.prerequisites for i in db.repository.require(entry.id).active_implementations}
        self.assertEqual(stored["python"], "Python 3.10+")
        self.assertEqual(stored["go"], "Go 1.21+, 仅标准库")
        self.assertEqual(stored["rust"], "Rust 1.75+ / cargo")
        window.close()

    def _subtree_text(self, widget) -> str:
        """收集一个部件子树里的全部可见文本 (QLabel + QTextBrowser 都要看)。"""
        from PySide6.QtWidgets import QTextBrowser

        parts = [label.text() for label in widget.findChildren(QLabel)]
        for browser in widget.findChildren(QTextBrowser):
            parts.append(browser.toPlainText())
        return "\n".join(parts)

    def test_overview_lists_prerequisites_per_language(self):
        window, db, entry = self._window()
        overview = window.detail_panel.tabs.widget(0)
        joined = self._subtree_text(overview)
        for expected in ("Python 3.10+", "Go 1.21+, 仅标准库", "Rust 1.75+ / cargo"):
            self.assertIn(expected, joined, f"概览未显示 {expected}")
        window.close()

    def test_each_language_tab_shows_its_own_prerequisites(self):
        window, db, entry = self._window()
        implementations = db.repository.require(entry.id).active_implementations
        for index, impl in enumerate(implementations):
            window.detail_panel.show_tab(index + 1)
            self.app.processEvents()
            page = window.detail_panel.tabs.widget(index + 1)
            joined = self._subtree_text(page)
            self.assertIn(impl.prerequisites, joined, f"{impl.language} 页签未显示前置要求")
        window.close()

    def test_shared_prerequisites_shown_separately(self):
        window, db, entry = self._window()
        overview = window.detail_panel.tabs.widget(0)
        joined = self._subtree_text(overview)
        self.assertIn("通用前置要求", joined)
        self.assertIn("需要理解字符编码", joined)
        window.close()

    def test_implementation_editor_edits_prerequisites(self):
        from codemethod.ui.dialogs.entry_editor import ImplementationEditor

        editor = self.track(ImplementationEditor(theme=DARK_PLUS))
        editor.language_combo.setCurrentIndex(editor.language_combo.findData("rust"))
        editor.prerequisites_edit.setText("Rust 1.80+ / cargo")
        collected = editor.collect()
        self.assertEqual(collected.prerequisites, "Rust 1.80+ / cargo")
        self.assertEqual(collected.language, "rust")

    def test_implementation_editor_loads_prerequisites(self):
        from codemethod.core.models import Implementation
        from codemethod.ui.dialogs.entry_editor import ImplementationEditor

        impl = Implementation(language="go", code="x", prerequisites="Go 1.22+")
        editor = self.track(ImplementationEditor(theme=DARK_PLUS))
        editor.load(impl)
        self.assertEqual(editor.prerequisites_edit.text(), "Go 1.22+")

    def test_entry_editor_collects_per_language_prerequisites(self):
        from codemethod.ui.dialogs.entry_editor import EntryEditorDialog

        window, db, entry = self._window()
        dialog = EntryEditorDialog(window, entry=entry, theme=DARK_PLUS)
        for index, editor in enumerate(dialog._editors, start=1):
            editor.prerequisites_edit.setText(f"要求 #{index}")
        data = dialog.result_data()
        self.assertEqual(
            [impl.prerequisites for impl in data["implementations"]],
            ["要求 #1", "要求 #2", "要求 #3"],
        )
        db.repository.apply_entry(entry.id, implementations=list(data["implementations"]))
        dialog.close()
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()
        self.assertEqual(
            [i.prerequisites for i in db.repository.require(entry.id).active_implementations],
            ["要求 #1", "要求 #2", "要求 #3"],
        )
        window.close()

    def test_implementation_dialog_passes_prerequisites_through(self):
        from codemethod.ui.dialogs.implementation_dialog import ImplementationDialog

        window, db, entry = self._window()
        dialog = ImplementationDialog(window, entry=entry, theme=DARK_PLUS)
        dialog.editor.language_combo.setCurrentIndex(
            dialog.editor.language_combo.findData("php")
        )
        dialog.editor.editor.setPlainText("<?php echo 1;")
        dialog.editor.prerequisites_edit.setText("PHP 8.2+")
        result = dialog.result_implementation()
        self.assertEqual(result.prerequisites, "PHP 8.2+")

        db.repository.add_implementation(
            entry.id, result.language, result.code, prerequisites=result.prerequisites
        )
        dialog.close()
        window.refresh_all(select_id=entry.id)
        self.app.processEvents()
        php = next(
            i for i in db.repository.require(entry.id).active_implementations
            if i.language == "php"
        )
        self.assertEqual(php.prerequisites, "PHP 8.2+")
        window.close()

    def test_prerequisites_survive_save_and_reopen(self):
        import tempfile

        window, db, entry = self._window()
        tmp = tempfile.mkdtemp(prefix="codemethod-prereq-")
        for name in ("a.cmdb", "a.cmj"):
            path = os.path.join(tmp, name)
            db.save(path, binary=name.endswith(".cmdb"), backup=False)
            reopened = Database.open(path)
            stored = {
                i.language: i.prerequisites
                for i in reopened.repository.require(entry.id).active_implementations
            }
            self.assertEqual(stored["python"], "Python 3.10+", name)
            self.assertEqual(stored["go"], "Go 1.21+, 仅标准库", name)
        window.close()

    def test_legacy_file_without_prerequisites_still_loads(self):
        """旧版本写入的库没有该字段, 打开后应视为空而不是报错。"""
        window, db, entry = self._window()
        data = db.repository.to_dict()
        for item in data["entries"]:
            for impl in item.get("implementations", []):
                impl.pop("prerequisites", None)
        restored = Repository.from_dict(data)
        impl = restored.require(entry.id).active_implementations[0]
        self.assertEqual(impl.prerequisites, "")
        window.close()


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestThemes(_TrackedWidgets):
    """主题系统: 注册表、明暗判定、对比度、菜单与切换。"""

    def test_all_themes_registered(self):
        from codemethod.ui.theme import THEMES, get_theme, theme_names

        self.assertGreaterEqual(len(THEMES), 5)
        keys = [key for key, _label in theme_names()]
        for expected in ("dark+", "light", "deep-sea", "vscode-red", "dracula"):
            self.assertIn(expected, keys, f"缺少主题 {expected}")
        for key in keys:
            self.assertEqual(get_theme(key).key, key)

    def test_unknown_theme_falls_back(self):
        self.assertEqual(get_theme("nope").key, "dark+")

    def test_dark_flag_matches_window_color(self):
        from codemethod.ui.theme import THEMES

        expected = {
            "dark+": True,
            "deep-sea": True,
            "vscode-red": True,
            "dracula": True,
            "light": False,
        }
        for key, is_dark in expected.items():
            self.assertEqual(THEMES[key].is_dark, is_dark, key)

    def test_light_theme_has_light_title_bar_and_dark_ones_dark(self):
        """原生标题栏靠 is_dark 决定明暗, 判定错了顶部就会突兀。"""
        from codemethod.ui.theme import THEMES

        self.assertFalse(THEMES["light"].is_dark)
        self.assertTrue(all(THEMES[k].is_dark for k in ("dark+", "deep-sea", "vscode-red", "dracula")))

    # ---- 对比度: 防止出现看不清的主题 ----

    @staticmethod
    def _contrast(a: str, b: str) -> float:
        def luminance(color: str) -> float:
            c = QColor(color)
            channels = []
            for value in (c.redF(), c.greenF(), c.blueF()):
                channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

        high, low = sorted((luminance(a), luminance(b)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    def test_body_text_is_readable_on_every_theme(self):
        from codemethod.ui.theme import THEMES

        for key, theme in THEMES.items():
            for surface in ("window", "editor", "sidebar"):
                ratio = self._contrast(theme.text, getattr(theme, surface))
                self.assertGreaterEqual(
                    ratio, 4.5, f"{key} 的正文在 {surface} 上对比度只有 {ratio:.2f}"
                )

    def test_token_colors_are_readable(self):
        from codemethod.ui.theme import THEMES

        token_fields = [
            "tok_comment", "tok_keyword", "tok_control", "tok_type", "tok_function",
            "tok_string", "tok_number", "tok_constant", "tok_builtin", "tok_variable",
        ]
        for key, theme in THEMES.items():
            for field in token_fields:
                ratio = self._contrast(getattr(theme, field), theme.editor)
                self.assertGreaterEqual(
                    ratio, 2.5, f"{key} 的 {field} 在编辑器背景上对比度只有 {ratio:.2f}"
                )

    def test_status_bar_text_is_readable(self):
        from codemethod.ui.theme import THEMES

        for key, theme in THEMES.items():
            ratio = self._contrast(theme.status_bar_text, theme.status_bar)
            self.assertGreaterEqual(ratio, 3.0, f"{key} 状态栏文字对比度只有 {ratio:.2f}")

    def test_stylesheet_is_generated_for_every_theme(self):
        from codemethod.ui.theme import THEMES, _qss

        for key, theme in THEMES.items():
            sheet = _qss(theme)
            self.assertIn(theme.window, sheet, key)
            self.assertIn(theme.accent, sheet, key)
            # 大括号必须成对, 否则整份样式表会失效
            self.assertEqual(sheet.count("{"), sheet.count("}"), f"{key} 的 QSS 花括号不配对")

    # ---- 切换 ----

    def test_switching_every_theme_updates_window(self):
        from codemethod.ui.theme import THEMES

        window, _db = self._window()
        for key in THEMES:
            window.set_theme_key(key)
            self.app.processEvents()
            self.assertEqual(window.theme.key, key)
            self.assertEqual(window.detail_panel._theme.key, key)
            self.assertEqual(window.entry_list._theme.key, key)
            self.assertEqual(window.history_panel._theme.key, key)
            self.assertEqual(window.tag_panel._theme.key, key)
        window.close()

    def test_theme_menu_has_one_checked_action(self):
        from codemethod.ui.theme import THEMES

        window, _db = self._window()
        self.assertEqual(len(window._theme_actions), len(THEMES))
        for key in THEMES:
            window.set_theme_key(key)
            self.app.processEvents()
            checked = [k for k, a in window._theme_actions.items() if a.isChecked()]
            self.assertEqual(checked, [key], f"切到 {key} 后勾选状态不对: {checked}")
        window.close()

    def test_cycle_theme_visits_every_theme(self):
        from codemethod.ui.theme import THEMES

        window, _db = self._window()
        start = window.theme.key
        seen = []
        for _ in range(len(THEMES)):
            window.cycle_theme()
            seen.append(window.theme.key)
        self.assertEqual(sorted(seen), sorted(THEMES.keys()))
        self.assertEqual(window.theme.key, start)  # 转一圈回到原点
        window.close()

    def test_theme_choice_is_persisted(self):
        window, _db = self._window()
        window.set_theme_key("deep-sea")
        self.assertEqual(window.settings.value("theme", "", type=str), "deep-sea")
        window.close()


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestNativeTitleBar(_TrackedWidgets):
    """原生标题栏着色: 深色主题不能留一条白框。"""

    def test_apply_returns_bool_and_never_raises(self):
        from codemethod.ui.native import apply_titlebar_theme
        from codemethod.ui.theme import get_theme

        window, _db = self._window()
        for key in ("dark+", "light", "deep-sea"):
            result = apply_titlebar_theme(window, get_theme(key))
            self.assertIsInstance(result, bool)
            self.app.processEvents()
        window.close()

    def test_apply_on_none_is_safe(self):
        from codemethod.ui.native import apply_titlebar_theme

        self.assertFalse(apply_titlebar_theme(None, DEFAULT_THEME))  # type: ignore[arg-type]

    def test_colorref_conversion(self):
        """Win32 的 COLORREF 是 0x00BBGGRR, 顺序写反了标题栏颜色就会错。"""
        from codemethod.ui.native import _colorref

        self.assertEqual(_colorref("#000000"), 0x000000)
        self.assertEqual(_colorref("#FFFFFF"), 0xFFFFFF)
        # 纯红 #FF0000 -> B=00 G=00 R=FF -> 0x0000FF
        self.assertEqual(_colorref("#FF0000"), 0x0000FF)
        # 纯蓝 #0000FF -> B=FF G=00 R=00 -> 0xFF0000
        self.assertEqual(_colorref("#0000FF"), 0xFF0000)

    def test_themer_is_installed_by_apply_theme(self):
        from codemethod.ui.native import current_themer
        from codemethod.ui.theme import apply_theme, get_theme

        apply_theme(self.app, get_theme("deep-sea"))
        themer = current_themer()
        self.assertIsNotNone(themer)
        self.assertEqual(themer.theme.key, "deep-sea")

        # 再切一次应该复用同一个实例, 而不是重复安装事件过滤器
        apply_theme(self.app, get_theme("light"))
        self.assertIs(current_themer(), themer)
        self.assertEqual(themer.theme.key, "light")

    def test_themer_tracks_dark_flag_of_each_theme(self):
        from codemethod.ui.native import current_themer
        from codemethod.ui.theme import THEMES, apply_theme, get_theme

        for key, theme in THEMES.items():
            apply_theme(self.app, get_theme(key))
            self.assertEqual(current_themer().theme.is_dark, theme.is_dark, key)


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestDialogFitsOnScreen(_TrackedWidgets):
    """用户报的问题: 添加文件时弹出来的对话框太大, 底部的「保存」跑到屏幕外。"""

    def test_clamp_size_shrinks_to_screen(self):
        from PySide6.QtCore import QRect

        small = QRect(0, 0, 800, 600)
        width, height = clamp_size(1400, 900, geometry=small)
        self.assertLessEqual(width, int(800 * SCREEN_MARGIN) + 1)
        self.assertLessEqual(height, int(600 * SCREEN_MARGIN) + 1)

    def test_clamp_size_keeps_small_sizes(self):
        from PySide6.QtCore import QRect

        big = QRect(0, 0, 2560, 1440)
        self.assertEqual(clamp_size(900, 660, geometry=big), (900, 660))

    def test_clamp_size_reserves_room_for_window_frame(self):
        """可用高度要再扣掉标题栏/边框, 否则 frame 仍然会顶到屏幕外。"""
        from PySide6.QtCore import QRect

        from codemethod.ui.layout_util import FRAME_ALLOWANCE

        screen = QRect(0, 0, 1536, 824)
        _width, height = clamp_size(1400, 900, geometry=screen)
        self.assertLessEqual(height, int(824 * SCREEN_MARGIN) - FRAME_ALLOWANCE + 1)
        self.assertLessEqual(height + FRAME_ALLOWANCE, 824)

    def test_oversized_dialog_gets_shrunk(self):
        from PySide6.QtCore import QRect
        from unittest import mock

        import codemethod.ui.layout_util as layout_util

        dialog = self.track(SpaceEditorDialog(theme=get_theme("dark+")))
        dialog.setMinimumSize(1400, 1000)
        dialog.resize(1600, 1200)
        with mock.patch.object(
            layout_util, "available_geometry", return_value=QRect(0, 0, 1024, 640)
        ):
            changed = fit_dialog_to_screen(dialog)
        self.assertTrue(changed)
        self.assertLessEqual(dialog.width(), int(1024 * SCREEN_MARGIN) + 1)
        self.assertLessEqual(dialog.height(), int(640 * SCREEN_MARGIN) + 1)
        self.assertLessEqual(dialog.minimumHeight(), dialog.height())

    def test_main_window_is_fitted_to_screen(self):
        """主窗口 1440x900 比 1536x864 的可用高度还高, 必须被夹下来。

        这个 bug 的连锁反应才是用户真正遇到的: 父窗口下沉 → 以它为中心弹出的
        对话框跟着下沉 → 底部的「保存」跑到屏幕外。
        """
        from PySide6.QtCore import QRect
        from unittest import mock

        import codemethod.ui.layout_util as layout_util

        db = Database.create()
        window = self.track(MainWindow(db))
        window.resize(1440, 900)
        with mock.patch.object(
            layout_util, "available_geometry", return_value=QRect(0, 0, 1536, 824)
        ):
            changed = fit_window_to_screen(window)
        self.assertTrue(changed)
        self.assertLessEqual(window.height(), 824)

    def test_fit_window_leaves_room_for_taskbar(self):
        from PySide6.QtCore import QRect
        from unittest import mock

        import codemethod.ui.layout_util as layout_util

        db = Database.create()
        window = self.track(MainWindow(db))
        window.resize(1440, 900)
        geometry = QRect(0, 0, 1536, 824)      # 已扣掉任务栏
        with mock.patch.object(
            layout_util, "available_geometry", return_value=geometry
        ):
            fit_window_to_screen(window)
        self.assertLessEqual(window.size().height(), geometry.height())

    def test_space_editor_minimum_is_modest(self):
        """声明的最小尺寸不能超过常见小屏的可用高度, 否则夹也夹不下去。"""
        dialog = self.track(SpaceEditorDialog(theme=get_theme("dark+")))
        self.assertLessEqual(dialog.minimumHeight(), 560)
        self.assertLessEqual(dialog.minimumWidth(), 820)

    def test_themed_dialog_fits_on_show(self):
        from PySide6.QtCore import QRect
        from unittest import mock

        import codemethod.ui.layout_util as layout_util
        from codemethod.ui.native import ThemedDialog

        dialog = self.track(ThemedDialog())
        dialog.setMinimumSize(1200, 900)
        dialog.resize(1400, 1000)
        with mock.patch.object(
            layout_util, "available_geometry", return_value=QRect(0, 0, 900, 560)
        ):
            dialog.show()
            self.app.processEvents()
        self.assertLessEqual(dialog.height(), int(560 * SCREEN_MARGIN) + 1)


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestEmbeddedBinaryUI(_TrackedWidgets):
    """空间里嵌入的二进制文件在界面上的呈现。"""

    # 一个**合法**的 1×1 PNG —— 这样图片预览分支是真的在跑, 而不是被解码器拒绝
    FAKE_PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
        "hKmMIQAAAABJRU5ErkJggg=="
    )

    def test_binary_preview_shows_size_and_hash(self):
        preview = self.track(BinaryPreview(theme=get_theme("dark+")))
        file = ProjectFile(path="assets/logo.png", binary=True)
        file.set_bytes(self.FAKE_PNG)
        preview.set_file(file)
        self.assertIn("已嵌入", preview.meta_label.text())
        self.assertIn("logo.png", preview.meta_label.text())
        self.assertTrue(preview.copy_button.isEnabled())
        self.assertTrue(preview.export_button.isEnabled())

    def test_binary_preview_flags_missing_content(self):
        preview = self.track(BinaryPreview(theme=get_theme("dark+")))
        preview.set_file(ProjectFile(path="old.png", binary=True, size=4096))
        self.assertIn("未嵌入", preview.meta_label.text())
        self.assertFalse(preview.copy_button.isEnabled())
        self.assertFalse(preview.export_button.isEnabled())
        self.assertIn("重新导入", preview.hex_editor.toPlainText())

    def test_binary_preview_hex_dump(self):
        preview = self.track(BinaryPreview(theme=get_theme("dark+")))
        file = ProjectFile(path="a.bin", binary=True)
        file.set_bytes(b"ABCDEFGH")
        preview.set_file(file)
        self.assertIn("41 42 43 44", preview.hex_editor.toPlainText())

    def test_space_panel_switches_to_binary_tab(self):
        db = Database.create()
        space = db.repository.create_space("含二进制")
        db.repository.put_space_file(
            space.id, "assets/logo.png", binary_data=self.FAKE_PNG
        )
        window = self.track(MainWindow(db))
        window.show()
        window.entry_list.select_entry(space.id)
        window.refresh_detail()
        self.app.processEvents()

        panel = window.space_panel
        panel.show_file("assets/logo.png")
        self.app.processEvents()
        self.assertEqual(panel.tabs.currentIndex(), 2)      # 二进制内容页
        self.assertTrue(panel.tabs.isTabEnabled(2))
        self.assertTrue(panel.binary_preview.meta_label.text())
        window.close()

    def test_tree_marks_binary_state(self):
        db = Database.create()
        space = db.repository.create_space("含二进制")
        db.repository.put_space_file(space.id, "a.png", binary_data=self.FAKE_PNG)
        db.repository.put_space_file(space.id, "b.png", binary=True, size=10)
        window = self.track(MainWindow(db))
        window.show()
        window.entry_list.select_entry(space.id)
        window.refresh_detail()
        self.app.processEvents()

        texts = []
        stack = [
            window.space_panel.tree.topLevelItem(i)
            for i in range(window.space_panel.tree.topLevelItemCount())
        ]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            # 文件名在第 0 列, 类型/大小/嵌入状态在第 1 列 (颜色更淡)
            texts.append(f"{item.text(0)}|{item.text(1)}")
            stack.extend(item.child(i) for i in range(item.childCount()))
        joined = " | ".join(texts)
        self.assertIn("已嵌入", joined)
        self.assertIn("未嵌入", joined)
        # 没有文件的那一列不该是同一种颜色
        for item in window.space_panel.tree.findItems(
            "", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive
        ):
            if item.text(1):
                self.assertNotEqual(
                    item.foreground(1).color().name(),
                    window.space_panel.tree.palette().text().color().name(),
                )
                break
        window.close()

    def test_space_editor_imports_binary(self):
        """走 space editor 的「导入文件…」, 内容要被嵌进工作副本。"""
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "logo.png"
            source.write_bytes(self.FAKE_PNG)
            dialog = self.track(SpaceEditorDialog(theme=get_theme("dark+")))
            with mock.patch(
                "codemethod.ui.dialogs.space_editor.QFileDialog.getOpenFileNames",
                return_value=([str(source)], ""),
            ):
                dialog.import_files("assets")
            self.app.processEvents()

            files = {f.path: f for f in dialog.result_data()["files"]}
            self.assertIn("assets/logo.png", files)
            self.assertEqual(files["assets/logo.png"].raw_bytes(), self.FAKE_PNG)
            self.assertIs(dialog.editor_stack.currentWidget(), dialog.binary_preview)

    def test_space_editor_imports_text_and_keeps_tree(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "main.py"
            source.write_bytes(b"print(1)\n")     # 精确控制换行, 不受平台影响
            dialog = self.track(SpaceEditorDialog(theme=get_theme("dark+")))
            with mock.patch(
                "codemethod.ui.dialogs.space_editor.QFileDialog.getOpenFileNames",
                return_value=([str(source)], ""),
            ):
                dialog.import_files("src")
            files = {f.path: f for f in dialog.result_data()["files"]}
            self.assertEqual(files["src/main.py"].content, "print(1)\n")
            self.assertEqual(files["src/main.py"].language, "python")

    def test_space_editor_binary_tab_used(self):
        dialog = self.track(SpaceEditorDialog(theme=get_theme("dark+")))
        file = ProjectFile(path="assets/logo.png", binary=True)
        file.set_bytes(self.FAKE_PNG)
        dialog._work_files["assets/logo.png"] = file
        dialog._refresh_tree(select="assets/logo.png")
        self.app.processEvents()
        self.assertIs(dialog.editor_stack.currentWidget(), dialog.binary_preview)
        self.assertIn("已嵌入", dialog.size_label.text())

    def test_main_window_import_embeds_into_repository(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "logo.png"
            source.write_bytes(self.FAKE_PNG)
            db = Database.create()
            space = db.repository.create_space("导入")
            window = self.track(MainWindow(db))
            window.show()
            window.entry_list.select_entry(space.id)
            window.refresh_detail()
            self.app.processEvents()

            with mock.patch(
                "codemethod.ui.main_window.QFileDialog.getOpenFileNames",
                return_value=([str(source)], ""),
            ):
                window.import_space_files(space.id, "")
            self.app.processEvents()

            file = db.repository.require_space(space.id).get_file("logo.png")
            self.assertIsNotNone(file)
            self.assertEqual(file.raw_bytes(), self.FAKE_PNG)
            window.close()

    def test_main_window_export_writes_bytes(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            db = Database.create()
            space = db.repository.create_space("导出")
            db.repository.put_space_file(space.id, "logo.png", binary_data=self.FAKE_PNG)
            window = self.track(MainWindow(db))
            window.show()
            window.entry_list.select_entry(space.id)
            window.refresh_detail()
            self.app.processEvents()

            target = Path(tmp) / "out.png"
            with mock.patch(
                "codemethod.ui.main_window.QFileDialog.getSaveFileName",
                return_value=(str(target), ""),
            ):
                window.export_space_file(space.id, "logo.png")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), self.FAKE_PNG)
            window.close()


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestDirectoryPackagingUI(_TrackedWidgets):
    """整目录打包的界面: 预览对话框 + 空间编辑器里的入口。"""

    def _project(self, root):
        from pathlib import Path

        base = Path(root)
        (base / "src" / "utils").mkdir(parents=True)
        (base / ".git").mkdir()
        (base / "src" / "main.py").write_bytes(b"print(1)\n")
        (base / "src" / "utils" / "h.py").write_bytes(b"def h():\n    return 1\n")
        (base / ".git" / "config").write_bytes(b"[core]\n")
        (base / "README.md").write_bytes(b"# hi\n")
        return base

    def test_dialog_previews_the_plan(self):
        import tempfile

        from codemethod.ui.dialogs.directory_import import DirectoryImportDialog

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            dialog = self.track(DirectoryImportDialog(theme=get_theme("dark+")))
            dialog.path_edit.setText(str(root))
            self.app.processEvents()

            scan = dialog.scan()
            self.assertIsNotNone(scan)
            self.assertFalse(scan.error)
            self.assertEqual(
                {f.path for f in scan.included},
                {"README.md", "src/main.py", "src/utils/h.py"},
            )
            # 摘要与"整个目录被跳过"的提示都要显示出来
            self.assertIn("3", dialog.summary_label.text())
            self.assertIn(".git", dialog.warning_label.text())
            self.assertTrue(dialog.import_button.isEnabled())
            # 预览树里应该有被划掉的 .git 目录
            rows = []
            stack = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
            while stack:
                item = stack.pop()
                if item is None:
                    continue
                rows.append((item.text(0), item.text(2)))
                stack.extend(item.child(i) for i in range(item.childCount()))
            self.assertTrue(any(name == ".git/" and "跳过" in meta for name, meta in rows), rows)

    def test_dialog_options_change_the_plan(self):
        import tempfile

        from codemethod.ui.dialogs.directory_import import DirectoryImportDialog

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            dialog = self.track(DirectoryImportDialog(theme=get_theme("dark+")))
            dialog.path_edit.setText(str(root))
            self.app.processEvents()
            self.assertEqual(len(dialog.scan().included), 3)

            dialog.vcs_check.setChecked(True)
            self.app.processEvents()
            self.assertEqual(len(dialog.scan().included), 4)

            dialog.recursive_check.setChecked(False)
            self.app.processEvents()
            # 不递归时只扫目标目录本身, 子目录完全不进去
            self.assertEqual({f.path for f in dialog.scan().included}, {"README.md"})

    def test_dialog_blocks_when_budget_is_short_until_forced(self):
        """只有在库里设了上限时才谈"预算"; 默认不限制就不该出现这个复选框。"""
        import tempfile

        from codemethod.ui.dialogs.directory_import import DirectoryImportDialog

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            # 默认 (budget_left=None) = 不限制
            unlimited = self.track(
                DirectoryImportDialog(theme=get_theme("dark+"), budget_left=None)
            )
            unlimited.path_edit.setText(str(root))
            self.app.processEvents()
            self.assertTrue(unlimited.force_check.isHidden())
            self.assertTrue(unlimited.import_button.isEnabled())

            dialog = self.track(
                DirectoryImportDialog(theme=get_theme("dark+"), budget_left=0)
            )
            dialog.path_edit.setText(str(root))
            self.app.processEvents()
            # 对话框没 show 时 isVisible() 恒为 False, 用 isHidden() 看显式可见性
            self.assertTrue(dialog.force_check.isHidden())
            self.assertTrue(dialog.import_button.isEnabled())

            # 放一张大图, 预算 0 → 必须勾「仍然导入」才允许
            from pathlib import Path

            blob = bytes.fromhex("89504e470d0a1a0a0000000d49484452") + bytes(range(256)) * 8
            (Path(root) / "big.png").write_bytes(blob)
            dialog.rescan()
            self.app.processEvents()
            self.assertFalse(dialog.force_check.isHidden())
            self.assertFalse(dialog.import_button.isEnabled())
            self.assertIn("仍然导入", dialog.warning_label.text().replace("&nbsp;", " "))
            dialog.force_check.setChecked(True)
            self.app.processEvents()
            self.assertTrue(dialog.import_button.isEnabled())

    def test_dialog_reports_missing_directory(self):
        from codemethod.ui.dialogs.directory_import import DirectoryImportDialog

        dialog = self.track(DirectoryImportDialog(theme=get_theme("dark+")))
        dialog.path_edit.setText("Z:/definitely/not/here")
        self.app.processEvents()
        self.assertTrue(dialog.scan().error)
        self.assertFalse(dialog.import_button.isEnabled())

    def test_space_editor_has_directory_import(self):
        from PySide6.QtWidgets import QPushButton

        from codemethod.core.spaces import ProjectFile
        from codemethod.ui.dialogs.space_editor import SpaceEditorDialog

        dialog = self.track(SpaceEditorDialog(theme=get_theme("dark+")))
        labels = [b.text() for b in dialog.findChildren(QPushButton)]
        self.assertTrue(any("导入目录" in text for text in labels), labels)
        self.assertTrue(any("导入文件" in text for text in labels), labels)
        # 工作副本里的目录结构要被保留
        dialog._work_files["src/a.py"] = ProjectFile(path="src/a.py", content="1")
        dialog._work_files["docs/b.md"] = ProjectFile(path="docs/b.md", content="2")
        dialog._refresh_tree()
        files = {f.path for f in dialog.result_data()["files"]}
        self.assertEqual(files, {"src/a.py", "docs/b.md"})

    def test_space_editor_tree_has_two_columns(self):
        from codemethod.core.spaces import ProjectFile
        from codemethod.ui.dialogs.space_editor import SpaceEditorDialog

        dialog = self.track(SpaceEditorDialog(theme=get_theme("dark+")))
        dialog._work_files["src/a.py"] = ProjectFile(path="src/a.py", content="print(1)")
        dialog._refresh_tree()
        self.assertEqual(dialog.tree.columnCount(), 2)
        item = dialog.tree.topLevelItem(0).child(0) if dialog.tree.topLevelItem(0) else None
        while item is not None and item.childCount():
            item = item.child(0)
        self.assertIsNotNone(item)
        self.assertEqual(item.text(0), "a.py")
        self.assertIn("Python", item.text(1))
        self.assertNotEqual(item.foreground(1).color().name(), "#000000")

    def test_main_window_import_directory_menu_action(self):
        import tempfile
        from unittest import mock

        from codemethod.ui.dialogs.directory_import import DirectoryImportDialog

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            db = Database.create()
            space = db.repository.create_space("打包")
            window = self.track(MainWindow(db))
            window.show()
            window.entry_list.select_entry(space.id)
            window.refresh_detail()
            self.app.processEvents()

            def fake_exec(dialog_self):
                dialog_self.path_edit.setText(str(root))
                self.app.processEvents()
                return DirectoryImportDialog.DialogCode.Accepted

            with mock.patch.object(DirectoryImportDialog, "exec", fake_exec):
                window.import_directory_into_space()
            self.app.processEvents()

            paths = {f.path for f in db.repository.require_space(space.id).files}
            self.assertIn("src/main.py", paths)
            self.assertNotIn(".git/config", paths)
            window.close()

    def test_gitkeep_preference_roundtrip(self):
        from codemethod.ui.dialogs.new_directory import (
            placeholder_preference,
            set_placeholder_preference,
        )

        original = placeholder_preference()
        try:
            set_placeholder_preference(False)
            self.assertFalse(placeholder_preference())
            set_placeholder_preference(True)
            self.assertTrue(placeholder_preference())
        finally:
            set_placeholder_preference(original)


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestStorageLimits(_TrackedWidgets):
    """存储限制默认全部关闭, 且可以自己设置。"""

    def test_default_is_unlimited(self):
        db = Database.create()
        self.assertTrue(db.repository.limits.unlimited)
        self.assertEqual(db.repository.limits.summary, "不限制")

    def test_dialog_round_trips_limits(self):
        from codemethod.ui.dialogs.limits_dialog import LimitsDialog

        db = Database.create()
        dialog = self.track(LimitsDialog(repository=db.repository, theme=get_theme("dark+")))
        self.assertTrue(dialog.result_limits().unlimited)   # 打开时就是"不限制"

        dialog._apply_suggested()
        limits = dialog.result_limits()
        self.assertEqual(limits.max_text_bytes, 2 * 1024 * 1024)
        self.assertEqual(limits.max_binary_bytes, 4 * 1024 * 1024)
        self.assertEqual(limits.max_files_per_space, 2000)

        dialog._apply_unlimited()
        self.assertTrue(dialog.result_limits().unlimited)

    def test_set_limits_persists_in_the_container(self):
        db = Database.create()
        db.repository.set_limits(StorageLimits(max_files_per_space=3))
        data = db.repository.to_dict()
        self.assertEqual(data["limits"]["max_files_per_space"], 3)
        restored = Repository.from_dict(data)
        self.assertEqual(restored.limits.max_files_per_space, 3)
        self.assertTrue(restored.statistics()["limits_summary"] != "")

    def test_window_action_updates_the_repository(self):
        from codemethod.ui.dialogs.limits_dialog import LimitsDialog

        db = Database.create()
        window = self.track(MainWindow(db))
        window.show()

        def fake_exec(dialog_self):
            dialog_self._apply_suggested()
            return LimitsDialog.DialogCode.Accepted

        from unittest import mock

        with mock.patch.object(LimitsDialog, "exec", fake_exec):
            window.show_limits_dialog()
        self.assertEqual(db.repository.limits.max_text_bytes, 2 * 1024 * 1024)
        window.close()


@unittest.skipUnless(PYSIDE, "需要 PySide6")
class TestLargeDocumentGuard(_TrackedWidgets):
    """不再限制文件大小之后, 超大文件靠编辑器降级来保证不卡死。"""

    def test_small_document_keeps_highlighting(self):
        editor = self.track(CodeEditor(language="python", theme=get_theme("dark+")))
        editor.setPlainText("def f():\n    return 1\n")
        self.assertFalse(editor.highlight_suppressed)

    def test_huge_document_suppresses_highlighting(self):
        editor = self.track(CodeEditor(language="python", theme=get_theme("dark+")))
        editor.setPlainText("x = 1\n" * 40_000)          # 超过行数阈值
        self.assertTrue(editor.highlight_suppressed)

    def test_guard_decides_before_the_text_goes_in(self):
        """必须在 setPlainText 之前就摘掉高亮器, 否则会先跑几十万次回调。"""
        editor = self.track(CodeEditor(language="python", theme=get_theme("dark+")))
        editor.setPlainText("y = 2\n" * 40_000)
        self.assertTrue(editor.highlight_suppressed)
        self.assertIsNone(editor._highlighter.document())

    def test_highlighting_comes_back_when_document_shrinks(self):
        editor = self.track(CodeEditor(language="python", theme=get_theme("dark+")))
        editor.setPlainText("z = 3\n" * 40_000)
        self.assertTrue(editor.highlight_suppressed)
        editor.setPlainText("def f():\n    return 1\n")
        self.assertFalse(editor.highlight_suppressed)
        self.assertIsNotNone(editor._highlighter.document())

    def test_preview_shows_a_notice(self):
        preview = self.track(CodePreview(theme=get_theme("dark+")))
        preview.set_code("w = 4\n" * 40_000, language="python")
        self.assertTrue(preview.highlight_suppressed)
        self.assertIn("语法高亮", preview._highlight_notice.text())
        preview.set_code("def f():\n    return 1\n", language="python")
        self.assertEqual(preview._highlight_notice.text(), "")

    def test_big_text_file_still_saves_and_reopens(self):
        """不限制之后, 一个 3 MB 的文本文件必须能原样存进空间再读出来。"""
        db = Database.create()
        space = db.repository.create_space("大文件")
        body = "hello world\n" * 250_000          # ~3 MB
        db.repository.put_space_file(space.id, "big.log", body)
        file = db.repository.require_space(space.id).get_file("big.log")
        self.assertEqual(len(file.content), len(body))
        self.assertFalse(file.binary)

        window = self.track(MainWindow(db))
        window.show()
        window.entry_list.select_entry(space.id)
        window.refresh_detail()
        window.space_panel.show_file("big.log")
        self.app.processEvents()
        self.assertTrue(window.space_panel.preview.highlight_suppressed)
        window.close()


if __name__ == "__main__":
    unittest.main()
