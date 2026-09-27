"""仓储测试: CRUD 的历史覆盖、回滚、标签操作与撤销/重做。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.models import Implementation  # noqa: E402
from codemethod.core.repository import Repository, RepositoryError  # noqa: E402


class RepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(name="test")
        self.entry = self.repo.create_entry(
            "标题", "描述", "前置", ["alpha", "beta"], status="planned"
        )

    def actions(self, entry_id=None):
        return [r.action for r in self.repo.revisions(entry_id or self.entry.id, descending=False)]


class TestEntryCrud(RepositoryTestCase):
    def test_create_records_history(self):
        self.assertEqual(self.actions(), ["create"])
        self.assertEqual(self.repo.history.count(self.entry.id), 1)

    def test_update_records_history(self):
        self.repo.update_entry(self.entry.id, description="新描述")
        self.assertEqual(self.actions(), ["create", "update"])
        self.assertEqual(self.repo.require(self.entry.id).description, "新描述")

    def test_update_without_changes_creates_no_revision(self):
        self.repo.update_entry(self.entry.id, description="描述")
        self.assertEqual(self.repo.history.count(self.entry.id), 1)

    def test_update_status_records(self):
        self.repo.update_entry(self.entry.id, status="done")
        self.assertIn("update", self.actions())
        self.assertEqual(self.repo.require(self.entry.id).status, "done")

    def test_invalid_status_raises(self):
        with self.assertRaises(RepositoryError):
            self.repo.update_entry(self.entry.id, status="bogus")

    def test_soft_delete_and_restore(self):
        self.repo.delete_entry(self.entry.id)
        self.assertTrue(self.repo.require(self.entry.id).deleted)
        self.assertEqual(len(self.repo.entries_list()), 0)
        self.assertEqual(len(self.repo.entries_list(include_deleted=True)), 1)
        self.assertIn("delete", self.actions())

        self.repo.restore_entry(self.entry.id)
        self.assertFalse(self.repo.require(self.entry.id).deleted)
        self.assertIn("restore_delete", self.actions())

    def test_hard_delete_removes_entry_but_keeps_history(self):
        self.repo.delete_entry(self.entry.id, hard=True)
        self.assertIsNone(self.repo.get(self.entry.id))
        self.assertGreaterEqual(self.repo.history.count(self.entry.id), 2)

    def test_toggle_favorite(self):
        self.repo.toggle_favorite(self.entry.id)
        self.assertTrue(self.repo.require(self.entry.id).favorite)
        self.assertIn("favorite", self.actions())

    def test_duplicate_entry(self):
        clone = self.repo.duplicate_entry(self.entry.id)
        self.assertNotEqual(clone.id, self.entry.id)
        self.assertIn("副本", clone.title)
        self.assertEqual(clone.tags, self.entry.tags)
        self.assertEqual(self.actions(clone.id), ["create"])

    def test_duplicate_gives_implementations_new_ids(self):
        impl = self.repo.add_implementation(self.entry.id, "python", "x")
        clone = self.repo.duplicate_entry(self.entry.id)
        self.assertNotEqual(clone.implementations[0].id, impl.id)

    def test_require_missing_raises(self):
        with self.assertRaises(RepositoryError):
            self.repo.require("nope")


class TestImplementationCrud(RepositoryTestCase):
    def test_add_implementation(self):
        impl = self.repo.add_implementation(self.entry.id, "py", "print(1)", title="t")
        self.assertEqual(impl.language, "python")
        self.assertIn("impl_add", self.actions())
        self.assertEqual(len(self.repo.require(self.entry.id).active_implementations), 1)

    def test_multiple_languages_per_entry(self):
        for language in ("python", "go", "rust", "java", "c", "cpp", "php"):
            self.repo.add_implementation(self.entry.id, language, f"// {language}")
        entry = self.repo.require(self.entry.id)
        self.assertEqual(len(entry.active_implementations), 7)
        self.assertEqual(len(entry.languages), 7)

    def test_update_implementation_code(self):
        impl = self.repo.add_implementation(self.entry.id, "python", "a")
        self.repo.update_implementation(self.entry.id, impl.id, code="b")
        self.assertEqual(self.repo.require(self.entry.id).get_implementation(impl.id).code, "b")
        self.assertIn("impl_update", self.actions())

    def test_update_missing_implementation_raises(self):
        with self.assertRaises(RepositoryError):
            self.repo.update_implementation(self.entry.id, "nope", code="x")

    def test_each_language_keeps_its_own_prerequisites(self):
        """同一个问题, 不同语言的前置要求互不干扰。"""
        py = self.repo.add_implementation(
            self.entry.id, "python", "print(1)", prerequisites="Python 3.10+"
        )
        go = self.repo.add_implementation(
            self.entry.id, "go", "package main", prerequisites="Go 1.21+"
        )
        entry = self.repo.require(self.entry.id)
        self.assertEqual(entry.get_implementation(py.id).prerequisites, "Python 3.10+")
        self.assertEqual(entry.get_implementation(go.id).prerequisites, "Go 1.21+")
        self.assertEqual(
            entry.prerequisites_by_language,
            [("Python", "Python 3.10+"), ("Go", "Go 1.21+")],
        )

    def test_updating_prerequisites_is_tracked_in_history(self):
        impl = self.repo.add_implementation(self.entry.id, "python", "a")
        self.repo.update_implementation(
            self.entry.id, impl.id, prerequisites="Python 3.12+, 需要 aiohttp"
        )
        self.assertEqual(
            self.repo.require(self.entry.id).get_implementation(impl.id).prerequisites,
            "Python 3.12+, 需要 aiohttp",
        )
        self.assertIn("impl_update", self.actions())
        # 摘要里要能看出改的是"前置要求"
        latest = self.repo.revisions(self.entry.id)[0]
        self.assertIn("前置要求", latest.summary)

    def test_prerequisites_change_appears_in_diff(self):
        impl = self.repo.add_implementation(
            self.entry.id, "python", "a", prerequisites="Python 3.8+"
        )
        self.repo.update_implementation(self.entry.id, impl.id, prerequisites="Python 3.12+")
        latest = self.repo.revisions(self.entry.id)[0]
        diff = self.repo.revision_diff(latest.id)
        self.assertIn("前置要求", diff)
        self.assertIn("-Python 3.8+", diff)
        self.assertIn("+Python 3.12+", diff)

    def test_apply_entry_persists_per_language_prerequisites(self):
        from codemethod.core.models import Implementation

        implementations = [
            Implementation(language="python", code="x", prerequisites="Python 3.10+"),
            Implementation(language="go", code="y", prerequisites="Go 1.21+"),
        ]
        self.repo.apply_entry(self.entry.id, implementations=implementations)
        entry = self.repo.require(self.entry.id)
        self.assertEqual(
            [i.prerequisites for i in entry.active_implementations],
            ["Python 3.10+", "Go 1.21+"],
        )
        # 重新载入 (模拟存盘再打开) 后仍然保留
        restored = Repository.from_dict(self.repo.to_dict())
        self.assertEqual(
            [i.prerequisites for i in restored.require(self.entry.id).active_implementations],
            ["Python 3.10+", "Go 1.21+"],
        )

    def test_delete_implementation_is_soft_and_tracked(self):
        impl = self.repo.add_implementation(self.entry.id, "python", "a")
        self.repo.delete_implementation(self.entry.id, impl.id)
        entry = self.repo.require(self.entry.id)
        self.assertEqual(len(entry.active_implementations), 0)
        self.assertEqual(len(entry.implementations), 1)
        self.assertIn("impl_delete", self.actions())

    def test_restore_implementation(self):
        impl = self.repo.add_implementation(self.entry.id, "python", "a")
        self.repo.delete_implementation(self.entry.id, impl.id)
        self.repo.restore_implementation(self.entry.id, impl.id)
        self.assertEqual(len(self.repo.require(self.entry.id).active_implementations), 1)
        self.assertIn("impl_restore", self.actions())

    def test_apply_entry_single_revision(self):
        impl = self.repo.add_implementation(self.entry.id, "python", "a")
        before = self.repo.history.count(self.entry.id)
        self.repo.apply_entry(
            self.entry.id,
            title="新标题",
            description="新描述",
            prerequisites="新前置",
            tags=["gamma"],
            status="done",
            favorite=True,
            implementations=[impl],
        )
        self.assertEqual(self.repo.history.count(self.entry.id), before + 1)
        latest = self.repo.revisions(self.entry.id)[0]
        self.assertEqual(latest.snapshot["title"], "新标题")
        self.assertEqual(latest.snapshot["tags"], ["gamma"])


class TestRestore(RepositoryTestCase):
    def test_restore_revision_is_full_and_recorded(self):
        first = self.repo.revisions(self.entry.id, descending=False)[0]
        self.repo.update_entry(self.entry.id, title="第二版", description="D2")
        self.repo.add_implementation(self.entry.id, "go", "package main")

        self.repo.restore_revision(self.entry.id, first.id)
        entry = self.repo.require(self.entry.id)
        self.assertEqual(entry.title, "标题")
        self.assertEqual(entry.description, "描述")
        self.assertEqual(len(entry.implementations), 0)

        # 回滚本身也进入历史, 且历史没有被截断
        self.assertEqual(self.actions()[-1], "restore")
        self.assertGreaterEqual(self.repo.history.count(self.entry.id), 4)

    def test_restore_foreign_revision_raises(self):
        other = self.repo.create_entry("另一个", "", "", [])
        rev = self.repo.revisions(other.id, descending=False)[0]
        with self.assertRaises(RepositoryError):
            self.repo.restore_revision(self.entry.id, rev.id)

    def test_undo_redo_apply_snapshots(self):
        self.repo.update_entry(self.entry.id, title="B")
        self.repo.update_entry(self.entry.id, title="C")
        self.assertTrue(self.repo.can_undo(self.entry.id))

        self.repo.undo(self.entry.id)
        self.assertEqual(self.repo.require(self.entry.id).title, "B")
        self.repo.undo(self.entry.id)
        self.assertEqual(self.repo.require(self.entry.id).title, "标题")
        self.assertFalse(self.repo.can_undo(self.entry.id))

        self.repo.redo(self.entry.id)
        self.assertEqual(self.repo.require(self.entry.id).title, "B")

    def test_revision_diff_for_current(self):
        self.repo.update_entry(self.entry.id, description="changed")
        latest = self.repo.revisions(self.entry.id)[0]
        text = self.repo.revision_diff(latest.id)
        self.assertIn("+changed", text)


class TestTags(RepositoryTestCase):
    def test_registry_auto_created_with_colors(self):
        self.assertIn("alpha", [t.casefold() for t in self.repo.all_tags()])
        self.assertTrue(self.repo.tag_color("alpha").startswith("#"))

    def test_tag_usage_counts(self):
        second = self.repo.create_entry("二", "", "", ["alpha"])
        self.repo.delete_entry(second.id)
        usage = self.repo.tag_usage()
        self.assertEqual(usage["alpha"], 1)  # 已删除条目不计入

    def test_rename_tag_cascades_and_is_tracked(self):
        affected = self.repo.rename_tag("alpha", "ALPHA2")
        self.assertEqual(affected, 1)
        self.assertIn("ALPHA2", self.repo.require(self.entry.id).tags)
        self.assertIn("tag_change", self.actions())

    def test_rename_to_same_returns_zero(self):
        self.assertEqual(self.repo.rename_tag("alpha", "Alpha"), 0)

    def test_delete_tag_removes_everywhere(self):
        self.repo.delete_tag("beta")
        self.assertNotIn("beta", self.repo.require(self.entry.id).tags)
        self.assertIn("tag_change", self.actions())

    def test_merge_tags(self):
        self.repo.merge_tags(["alpha", "beta"], "merged")
        entry = self.repo.require(self.entry.id)
        self.assertEqual(entry.tags, ["merged"])

    def test_set_tag_color(self):
        self.repo.set_tag_color("alpha", "#123456")
        self.assertEqual(self.repo.tag_color("alpha"), "#123456")

    def test_empty_tag_name_rejected(self):
        with self.assertRaises(RepositoryError):
            self.repo.rename_tag("alpha", "   ")


class TestSerialization(RepositoryTestCase):
    def test_repository_roundtrip_with_history(self):
        self.repo.add_implementation(self.entry.id, "rust", "fn main(){}")
        self.repo.update_entry(self.entry.id, description="D2")
        data = self.repo.to_dict()
        restored = Repository.from_dict(data)

        self.assertEqual(len(restored.entries), len(self.repo.entries))
        self.assertEqual(
            restored.history.count(self.entry.id), self.repo.history.count(self.entry.id)
        )
        self.assertEqual(restored.require(self.entry.id).description, "D2")
        self.assertEqual(restored.name, "test")
        self.assertFalse(restored.dirty)

    def test_from_dict_without_history_backfills(self):
        data = self.repo.to_dict(include_history=False)
        restored = Repository.from_dict(data)
        self.assertEqual(restored.history.count(self.entry.id), 1)

    def test_statistics(self):
        self.repo.add_implementation(self.entry.id, "python", "a\nb")
        stats = self.repo.statistics()
        self.assertEqual(stats["entries"], 1)
        self.assertEqual(stats["implementations"], 1)
        self.assertEqual(stats["code_lines"], 2)
        self.assertEqual(stats["tags"], 2)


if __name__ == "__main__":
    unittest.main()
