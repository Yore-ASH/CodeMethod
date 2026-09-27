"""修订历史测试: 全量快照、去重、游标、撤销/重做、差异与序列化。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.history import (  # noqa: E402
    HistoryStore,
    build_revision_diff,
    checksum_of,
    summarize_changes,
    take_snapshot,
)
from codemethod.core.models import Entry, Implementation  # noqa: E402


class TestSnapshot(unittest.TestCase):
    def test_checksum_is_stable_and_order_independent(self):
        a = {"x": 1, "y": [1, 2]}
        b = {"y": [1, 2], "x": 1}
        self.assertEqual(checksum_of(a), checksum_of(b))

    def test_checksum_changes_with_content(self):
        self.assertNotEqual(checksum_of({"x": 1}), checksum_of({"x": 2}))

    def test_take_snapshot_excludes_history(self):
        entry = Entry(title="T")
        snap = take_snapshot(entry)
        self.assertNotIn("revisions", snap)
        self.assertEqual(snap["title"], "T")


class TestHistoryStore(unittest.TestCase):
    def setUp(self):
        self.store = HistoryStore()
        self.entry = Entry(title="A")

    def test_record_and_query(self):
        rev = self.store.record(self.entry, "create", "新建", force=True)
        self.assertIsNotNone(rev)
        self.assertEqual(self.store.count(self.entry.id), 1)
        self.assertIs(self.store.latest(self.entry.id), rev)
        self.assertIs(self.store.first(self.entry.id), rev)
        self.assertIs(self.store.revision(rev.id), rev)

    def test_identical_snapshot_is_deduped(self):
        self.store.record(self.entry, "create", force=True)
        self.assertIsNone(self.store.record(self.entry, "update"))
        self.assertEqual(self.store.count(self.entry.id), 1)

    def test_force_bypasses_dedup(self):
        self.store.record(self.entry, "create", force=True)
        self.assertIsNotNone(self.store.record(self.entry, "update", force=True))
        self.assertEqual(self.store.count(self.entry.id), 2)

    def test_cursor_undo_redo(self):
        self.store.record(self.entry, "create", force=True)
        self.entry.title = "B"
        self.store.record(self.entry, "update", force=True)
        self.entry.title = "C"
        self.store.record(self.entry, "update", force=True)

        self.assertTrue(self.store.can_undo(self.entry.id))
        self.assertFalse(self.store.can_redo(self.entry.id))

        self.assertEqual(self.store.step(self.entry.id, -1).snapshot["title"], "B")
        self.assertTrue(self.store.can_redo(self.entry.id))
        self.assertEqual(self.store.step(self.entry.id, 1).snapshot["title"], "C")

    def test_cursor_bounds(self):
        self.store.record(self.entry, "create", force=True)
        self.assertIsNone(self.store.step(self.entry.id, -1))
        self.assertIsNone(self.store.step(self.entry.id, 1))
        self.assertFalse(self.store.can_undo(self.entry.id))

    def test_revisions_are_append_only_after_undo(self):
        self.store.record(self.entry, "create", force=True)
        self.entry.title = "B"
        self.store.record(self.entry, "update", force=True)
        self.store.step(self.entry.id, -1)  # 回到 A
        # 撤销后再改: 历史不会被截断, 而是继续追加
        self.entry.title = "D"
        self.store.record(self.entry, "update", force=True)
        self.assertEqual(self.store.count(self.entry.id), 3)
        titles = [r.snapshot["title"] for r in self.store.revisions(self.entry.id)]
        self.assertEqual(titles, ["A", "B", "D"])

    def test_parent_chain_recorded(self):
        first = self.store.record(self.entry, "create", force=True)
        self.entry.title = "B"
        second = self.store.record(self.entry, "update", force=True)
        self.assertEqual(second.parent_id, first.id)

    def test_prune_keeps_most_recent(self):
        store = HistoryStore(max_revisions=3)
        session = Entry(title="v0")
        for index in range(6):
            session.title = f"v{index}"
            store.record(session, "update", force=True)
        self.assertEqual(store.count(session.id), 3)
        titles = [r.snapshot["title"] for r in store.revisions(session.id)]
        self.assertEqual(titles, ["v3", "v4", "v5"])

    def test_all_revisions_sorted_desc(self):
        e1 = Entry(title="a")
        e2 = Entry(title="b")
        self.store.record(e1, "create", force=True)
        self.store.record(e2, "create", force=True)
        items = self.store.all_revisions()
        self.assertEqual(len(items), 2)
        self.assertGreaterEqual(items[0].timestamp, items[1].timestamp)

    def test_drop_and_clear(self):
        self.store.record(self.entry, "create", force=True)
        self.store.drop_entry(self.entry.id)
        self.assertEqual(self.store.count(self.entry.id), 0)
        self.assertEqual(self.store.total_count(), 0)

    def test_serialization_roundtrip(self):
        self.store.record(self.entry, "create", force=True)
        self.entry.title = "B"
        self.store.record(self.entry, "update", force=True)
        data = self.store.to_dict()
        restored = HistoryStore.from_dict(data)
        self.assertEqual(restored.count(self.entry.id), 2)
        self.assertEqual(
            [r.snapshot["title"] for r in restored.revisions(self.entry.id)], ["A", "B"]
        )
        self.assertEqual(restored.cursor(self.entry.id), 1)

    def test_from_dict_recomputes_missing_checksum(self):
        data = {"timelines": {"e1": [{"id": "r1", "entry_id": "e1", "snapshot": {"a": 1}}]}}
        store = HistoryStore.from_dict(data)
        self.assertTrue(store.revision("r1").checksum)


class TestDiff(unittest.TestCase):
    def test_field_diff(self):
        before = {"title": "A", "description": "x", "tags": []}
        after = {"title": "B", "description": "x", "tags": []}
        text = build_revision_diff(before, after)
        self.assertIn("### 标题", text)
        self.assertIn("-A", text)
        self.assertIn("+B", text)
        self.assertNotIn("描述", text)

    def test_tag_diff(self):
        text = build_revision_diff({"tags": ["a", "b"]}, {"tags": ["b", "c"]})
        self.assertIn("-a", text)   # 被移除
        self.assertIn("+c", text)   # 被新增
        self.assertNotIn("-b", text)  # b 保留, 不应出现在差异里

    def test_code_diff(self):
        before = {"implementations": [{"id": "i1", "language": "python", "code": "a\nb\nc"}]}
        after = {"implementations": [{"id": "i1", "language": "python", "code": "a\nB\nc"}]}
        text = build_revision_diff(before, after)
        self.assertIn("-b", text)
        self.assertIn("+B", text)

    def test_added_and_removed_implementation(self):
        added = build_revision_diff({}, {"implementations": [{"id": "i", "language": "go", "code": "x"}]})
        self.assertIn("新增实现", added)
        removed = build_revision_diff({"implementations": [{"id": "i", "language": "go", "code": "x"}]}, {})
        self.assertIn("删除实现", removed)

    def test_per_language_prerequisites_diff(self):
        """改某种语言的前置要求要出现在差异里 (前置要求是分语言的)。"""
        before = {
            "implementations": [
                {"id": "i1", "language": "python", "code": "x", "prerequisites": "Python 3.8+"},
                {"id": "i2", "language": "go", "code": "y", "prerequisites": "Go 1.20+"},
            ]
        }
        after = {
            "implementations": [
                {"id": "i1", "language": "python", "code": "x", "prerequisites": "Python 3.12+"},
                {"id": "i2", "language": "go", "code": "y", "prerequisites": "Go 1.20+"},
            ]
        }
        text = build_revision_diff(before, after)
        self.assertIn("前置要求", text)
        self.assertIn("-Python 3.8+", text)
        self.assertIn("+Python 3.12+", text)
        # Go 的前置要求没变, 就不该出现在任何以 +/- 开头的变更行里
        changed_lines = [
            line for line in text.splitlines()
            if line.startswith(("-Go", "+Go", "-Go ", "+Go "))
        ]
        self.assertEqual(changed_lines, [], f"未变更的语言不应出现在差异里: {changed_lines}")

    def test_summary_mentions_prerequisites_change(self):
        before = {"implementations": [{"id": "i", "language": "rust", "code": "x", "prerequisites": ""}]}
        after = {"implementations": [{"id": "i", "language": "rust", "code": "x", "prerequisites": "cargo"}]}
        summary = summarize_changes(before, after)
        self.assertIn("前置要求", summary)
        self.assertNotIn("无实质变更", summary)

    def test_new_and_deleted_entry(self):
        self.assertIn("新增条目", build_revision_diff(None, {"title": "t"}))
        self.assertIn("删除条目", build_revision_diff({"title": "t"}, None))

    def test_summarize(self):
        summary = summarize_changes(
            {"title": "A", "implementations": []}, {"title": "B", "implementations": []}
        )
        self.assertIn("标题", summary)
        self.assertEqual(summarize_changes(None, {}), "新建条目")

    def test_no_diff_for_identical(self):
        snap = {"title": "A", "implementations": []}
        self.assertEqual(build_revision_diff(snap, dict(snap)), "")


if __name__ == "__main__":
    unittest.main()
