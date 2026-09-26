"""语法高亮与界面部件测试 (需要 PySide6; 使用 offscreen 平台, 无需真实显示器)。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.*=false")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QApplication, QMessageBox

    qInstallMessageHandler(lambda *args: None)
    from codemethod.core.languages import LANGUAGES, all_languages
    from codemethod.core.models import Implementation
    from codemethod.core.repository import Repository
    from codemethod.storage.database import Database
    from codemethod.ui.editor import CodeEditor, CodePreview, DiffView
    from codemethod.ui.highlighter import build_rules, highlighted_tokens
    from codemethod.ui.main_window import MainWindow
    from codemethod.ui.theme import DARK_PLUS, LIGHT, apply_theme, get_theme, mono_font

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

    def tearDown(self):
        while self._tracked:
            widget = self._tracked.pop()
            try:
                widget.close()          # 触发 closeEvent, 让窗口进入 teardown 状态并断开信号
                widget.setParent(None)
                widget.deleteLater()
            except RuntimeError:  # 已被 C++ 侧回收
                pass
        for _ in range(3):
            self.app.processEvents()

    def track(self, widget):
        """登记部件, 测试结束统一销毁。"""
        self._tracked.append(widget)
        return widget


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
        self.assertEqual(len(window.entry_list.entry_model.entries()), 4)
        self.assertGreater(window.tag_panel.list.count(), 0)
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
        window, _db = self._make_window()
        window.search_bar.set_text("lang:python")
        self.app.processEvents()
        self.assertGreater(len(window.entry_list.entry_model.entries()), 0)
        window.clear_search()
        self.app.processEvents()
        self.assertEqual(len(window.entry_list.entry_model.entries()), 4)
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


if __name__ == "__main__":
    unittest.main()
