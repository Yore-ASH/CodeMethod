"""领域模型与语言注册表测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.languages import (  # noqa: E402
    LANGUAGES,
    default_filename,
    detect_language_from_filename,
    get_language,
    is_known_language,
    normalize_language,
)
from codemethod.core.models import (  # noqa: E402
    STATUS_ORDER,
    Entry,
    Implementation,
    Revision,
    diff_tag_sets,
    normalize_tags,
    tag_key,
)


class TestLanguages(unittest.TestCase):
    REQUIRED = ("c", "cpp", "go", "java", "python", "php", "rust")

    def test_required_languages_present(self):
        for language in self.REQUIRED:
            self.assertIn(language, LANGUAGES, f"缺少必需语言 {language}")

    def test_required_languages_have_keywords(self):
        for language in self.REQUIRED:
            spec = LANGUAGES[language]
            self.assertTrue(
                spec.all_keywords, f"{language} 没有任何关键字, 语法高亮会失效"
            )
            self.assertTrue(spec.extensions, f"{language} 没有扩展名")

    def test_normalize_aliases_and_extensions(self):
        cases = {
            "py": "python",
            "Python": "python",
            ".py": "python",
            "python3": "python",
            "c++": "cpp",
            "CPP": "cpp",
            "golang": "go",
            "js": "javascript",
            "c#": "csharp",
            "rs": "rust",
            "shell": "bash",
            "yml": "yaml",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_language(raw), expected, raw)

    def test_unknown_language_falls_back(self):
        self.assertEqual(normalize_language("brainfuck"), "plaintext")
        self.assertEqual(normalize_language(""), "plaintext")
        self.assertEqual(normalize_language(None), "plaintext")
        self.assertFalse(is_known_language("brainfuck"))
        self.assertTrue(is_known_language("py"))

    def test_detect_from_filename(self):
        self.assertEqual(detect_language_from_filename("main.py"), "python")
        self.assertEqual(detect_language_from_filename("a/b/Main.java"), "java")
        self.assertEqual(detect_language_from_filename("x.hpp"), "cpp")
        self.assertEqual(detect_language_from_filename("noext"), "plaintext")
        self.assertEqual(detect_language_from_filename("Dockerfile"), "bash")

    def test_default_filename(self):
        self.assertTrue(default_filename("python").endswith(".py"))
        self.assertTrue(default_filename("rust", "my mod").endswith(".rs"))

    def test_get_language_never_fails(self):
        self.assertEqual(get_language("nope").id, "plaintext")


class TestModels(unittest.TestCase):
    def test_normalize_tags_dedup_case_insensitive(self):
        tags = normalize_tags(["Alpha", "alpha", " beta ", "", "Beta", "g a  m  ma"])
        self.assertEqual(tags, ["Alpha", "beta", "g a m ma"])

    def test_tag_key_casefold(self):
        self.assertEqual(tag_key("Network"), tag_key("network"))

    def test_diff_tag_sets(self):
        added, removed = diff_tag_sets(["a", "b", "c"], ["b", "c", "d"])
        self.assertEqual(added, ["d"])
        self.assertEqual(removed, ["a"])

    def test_implementation_roundtrip(self):
        impl = Implementation(language="py", code="print(1)", title="t")
        self.assertEqual(impl.language, "python")
        restored = Implementation.from_dict(impl.to_dict())
        self.assertEqual(restored.code, "print(1)")
        self.assertEqual(restored.id, impl.id)
        self.assertEqual(restored.line_count, 1)

    def test_implementation_carries_its_own_prerequisites(self):
        """前置要求是分语言的: 每种实现各存一份。"""
        py = Implementation(language="python", prerequisites="Python 3.10+")
        go = Implementation(language="go", prerequisites="Go 1.21+")
        self.assertEqual(py.prerequisites, "Python 3.10+")
        self.assertEqual(go.prerequisites, "Go 1.21+")
        self.assertNotEqual(py.prerequisites, go.prerequisites)

        restored = Implementation.from_dict(py.to_dict())
        self.assertEqual(restored.prerequisites, "Python 3.10+")

    def test_implementation_prerequisites_defaults_to_empty(self):
        """旧数据没有该字段时应安全读成空串 (向后兼容)。"""
        legacy = {
            "id": "impl_old",
            "language": "python",
            "code": "x = 1",
            "title": "",
            "filename": "main.py",
            "notes": "",
        }
        impl = Implementation.from_dict(legacy)
        self.assertEqual(impl.prerequisites, "")
        self.assertEqual(impl.to_dict()["prerequisites"], "")

    def test_clone_keeps_prerequisites(self):
        impl = Implementation(language="rust", prerequisites="Rust 1.75+ / cargo")
        self.assertEqual(impl.clone().prerequisites, "Rust 1.75+ / cargo")
        self.assertEqual(impl.clone(new_identity=True).prerequisites, "Rust 1.75+ / cargo")

    def test_entry_prerequisites_by_language(self):
        entry = Entry(
            title="x",
            prerequisites="通用要求",
            implementations=[
                Implementation(language="python", prerequisites="Python 3.10+"),
                Implementation(language="go", prerequisites=""),
                Implementation(language="rust", prerequisites="cargo"),
                Implementation(language="c", prerequisites="GCC", deleted=True),
            ],
        )
        pairs = entry.prerequisites_by_language
        self.assertEqual(len(pairs), 3)          # 已删除的不算
        self.assertEqual(pairs[0], ("Python", "Python 3.10+"))
        self.assertEqual(pairs[1], ("Go", ""))
        self.assertEqual(pairs[2], ("Rust", "cargo"))

    def test_search_blob_includes_per_language_prerequisites(self):
        entry = Entry(
            title="x",
            implementations=[Implementation(language="rust", prerequisites="需要 cargo")],
        )
        self.assertIn("cargo", entry.search_blob)

    def test_implementation_clone_new_identity(self):
        impl = Implementation(language="go", code="package main")
        clone = impl.clone(new_identity=True)
        self.assertNotEqual(clone.id, impl.id)
        self.assertEqual(clone.code, impl.code)
        self.assertEqual(clone.version, 1)

    def test_implementation_infers_language_from_filename(self):
        impl = Implementation.from_dict({"filename": "main.rs", "code": ""})
        self.assertEqual(impl.language, "rust")

    def test_entry_roundtrip_preserves_everything(self):
        entry = Entry(
            title="T",
            description="D",
            prerequisites="P",
            tags=["x", "y"],
            status="done",
            favorite=True,
            implementations=[Implementation(language="c", code="int main(){}")],
        )
        restored = Entry.from_dict(entry.to_dict())
        self.assertEqual(restored.to_dict(), entry.to_dict())

    def test_entry_invalid_status_resets(self):
        entry = Entry(title="x", status="not-a-status")
        self.assertIn(entry.status, STATUS_ORDER)

    def test_entry_active_implementations_excludes_deleted(self):
        entry = Entry(title="x")
        entry.implementations = [
            Implementation(language="python", code="a"),
            Implementation(language="go", code="b", deleted=True),
        ]
        self.assertEqual(len(entry.active_implementations), 1)
        self.assertEqual(entry.languages, ["python"])

    def test_entry_search_blob_includes_code(self):
        entry = Entry(title="Title", implementations=[Implementation(language="python", code="UNIQUETOKEN")])
        self.assertIn("uniquetoken", entry.search_blob)

    def test_entry_touch_increments_version(self):
        entry = Entry(title="x")
        before = entry.version
        entry.touch()
        self.assertEqual(entry.version, before + 1)

    def test_revision_roundtrip_and_labels(self):
        rev = Revision(entry_id="e1", action="impl_update", summary="s", snapshot={"title": "T"})
        self.assertEqual(rev.action_label, "修改实现")
        self.assertEqual(rev.entry_title, "T")
        restored = Revision.from_dict(rev.to_dict())
        self.assertEqual(restored.id, rev.id)
        self.assertEqual(restored.snapshot, rev.snapshot)

    def test_suggest_filename_matches_language(self):
        self.assertTrue(Implementation(language="java").suggest_filename().endswith(".java"))


if __name__ == "__main__":
    unittest.main()
