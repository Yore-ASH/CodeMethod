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
class TestWidgets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app, DARK_PLUS)

    def test_editor_line_numbers_and_language_switch(self):
        editor = CodeEditor(language="python")
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
class TestMainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app, DARK_PLUS)
        # 自动应答模态对话框, 避免测试阻塞
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

    def _make_window(self):
        from codemethod.app import build_demo_repository

        db = Database(build_demo_repository())
        window = MainWindow(db)
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


if __name__ == "__main__":
    unittest.main()
