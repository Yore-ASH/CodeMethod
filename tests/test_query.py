"""检索与多标签组合测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.query import (  # noqa: E402
    QuerySpec,
    TagMatch,
    entry_matches,
    full_text_blob,
    parse_query_text,
    query_entries,
    related_tags,
    sort_entries,
)
from codemethod.core.repository import Repository  # noqa: E402


class QueryTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(name="q")
        self.a = self.repo.create_entry(
            "TCP 服务器", "实现一个并发的 socket 服务器", "需要 socket 基础",
            ["network", "server"], status="done", favorite=True,
        )
        self.repo.add_implementation(self.a.id, "python", "import socket\nprint('tcp')")
        self.repo.add_implementation(self.a.id, "go", "package main")

        self.b = self.repo.create_entry(
            "LRU 缓存", "哈希表加双向链表", "需要链表知识",
            ["algorithm", "cache"], status="in_progress",
        )
        self.repo.add_implementation(self.b.id, "cpp", "int main(){}")

        self.c = self.repo.create_entry(
            "废弃的旧实现", "deprecated 的东西", "",
            ["network", "legacy"], status="archived",
        )
        self.repo.delete_entry(self.c.id)

    def ids(self, spec):
        return {e.id for e in query_entries(self.repo, spec)}

    def test_parse_terms(self):
        terms = parse_query_text('socket "tcp server" -deprecated tag:net lang:py is:fav')
        self.assertEqual(len(terms), 6)
        self.assertEqual(terms[1].value, "tcp server")
        self.assertTrue(terms[2].negate)
        self.assertEqual(terms[3].field, "tag")
        self.assertEqual(terms[4].field, "lang")

    def test_default_excludes_deleted(self):
        self.assertNotIn(self.c.id, self.ids(QuerySpec()))

    def test_only_deleted(self):
        self.assertEqual(self.ids(QuerySpec(only_deleted=True)), {self.c.id})

    def test_include_deleted(self):
        self.assertEqual(len(self.ids(QuerySpec(include_deleted=True))), 3)

    def test_tag_and_mode(self):
        self.assertEqual(self.ids(QuerySpec(tags=["network", "server"])), {self.a.id})

    def test_tag_any_mode(self):
        result = self.ids(QuerySpec(tags=["cache", "server"], tag_mode=TagMatch.ANY))
        self.assertEqual(result, {self.a.id, self.b.id})

    def test_tag_none_mode(self):
        result = self.ids(QuerySpec(tags=["network"], tag_mode=TagMatch.NONE))
        self.assertEqual(result, {self.b.id})

    def test_tag_exact_mode(self):
        result = self.ids(
            QuerySpec(tags=["algorithm", "cache"], tag_mode=TagMatch.EXACT)
        )
        self.assertEqual(result, {self.b.id})
        self.assertEqual(
            self.ids(QuerySpec(tags=["algorithm"], tag_mode=TagMatch.EXACT)), set()
        )

    def test_tag_case_insensitive(self):
        self.assertEqual(self.ids(QuerySpec(tags=["NETWORK", "Server"])), {self.a.id})

    def test_language_filter(self):
        self.assertEqual(self.ids(QuerySpec(languages=["python"])), {self.a.id})
        self.assertEqual(self.ids(QuerySpec(languages=["py"])), {self.a.id})
        self.assertEqual(self.ids(QuerySpec(languages=["cpp"])), {self.b.id})

    def test_status_filter(self):
        self.assertEqual(self.ids(QuerySpec(statuses=["done"])), {self.a.id})
        self.assertEqual(
            self.ids(QuerySpec(statuses=["done", "in_progress"])), {self.a.id, self.b.id}
        )

    def test_favorites_only(self):
        self.assertEqual(self.ids(QuerySpec(favorites_only=True)), {self.a.id})

    def test_text_search_in_code(self):
        self.assertEqual(self.ids(QuerySpec(text="import socket")), {self.a.id})

    def test_text_search_scope_can_exclude_code(self):
        self.assertEqual(
            self.ids(QuerySpec(text="import socket", search_code=False)), set()
        )

    def test_text_search_in_description(self):
        self.assertEqual(self.ids(QuerySpec(text="双向链表")), {self.b.id})

    def test_phrase_query(self):
        self.assertEqual(self.ids(QuerySpec(text='"哈希表加双向链表"')), {self.b.id})
        self.assertEqual(self.ids(QuerySpec(text='"双向链表加哈希表"')), set())

    def test_negation(self):
        self.assertEqual(self.ids(QuerySpec(text="-socket")), {self.b.id})

    def test_field_qualifiers(self):
        self.assertEqual(self.ids(QuerySpec(text="lang:go")), {self.a.id})
        self.assertEqual(self.ids(QuerySpec(text="tag:cache")), {self.b.id})
        self.assertEqual(self.ids(QuerySpec(text="status:done")), {self.a.id})
        self.assertEqual(self.ids(QuerySpec(text="is:favorite")), {self.a.id})
        self.assertEqual(self.ids(QuerySpec(text="is:multi")), {self.a.id})
        self.assertEqual(self.ids(QuerySpec(text="is:has_code")), {self.a.id, self.b.id})

    def test_combined_conditions(self):
        spec = QuerySpec(text="socket", tags=["network"], languages=["python"], statuses=["done"])
        self.assertEqual(self.ids(spec), {self.a.id})

    def test_full_text_blob(self):
        blob = full_text_blob(self.repo.require(self.b.id))
        self.assertIn("lru 缓存".casefold(), blob)

    def test_is_empty(self):
        self.assertTrue(QuerySpec().is_empty)
        self.assertFalse(QuerySpec(text="x").is_empty)

    def test_describe(self):
        self.assertIn("标签", QuerySpec(tags=["a"], tag_mode=TagMatch.ALL).describe())


class TestSorting(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(name="s")
        self.first = self.repo.create_entry("B 条目", "", "", ["t"], status="done")
        self.second = self.repo.create_entry("a 条目", "", "", ["t"], status="idea")
        self.repo.add_implementation(self.second.id, "python", "a\nb\nc")
        self.repo.add_implementation(self.second.id, "go", "x")

    def titles(self, key):
        return [e.title for e in sort_entries(self.repo.entries_list(), key, repo=self.repo)]

    def test_title_sort_is_case_insensitive(self):
        self.assertEqual(self.titles("title_asc"), ["a 条目", "B 条目"])

    def test_status_sort_follows_workflow_order(self):
        self.assertEqual(self.titles("status")[0], "a 条目")  # idea 在 done 之前

    def test_lines_sort(self):
        self.assertEqual(self.titles("lines_desc")[0], "a 条目")

    def test_revisions_sort(self):
        # second ("a 条目") 有 1 条 create + 2 条 impl_add = 3
        self.assertEqual(self.titles("revisions_desc")[0], "a 条目")
        # 给 first 多加几条修订后反超
        for index in range(4):
            self.repo.update_entry(self.first.id, description=f"v{index}")
        self.assertEqual(self.titles("revisions_desc")[0], "B 条目")

    def test_unknown_sort_key_falls_back(self):
        self.assertEqual(len(self.titles("nonsense")), 2)


class TestRelatedTags(unittest.TestCase):
    def test_related_tags(self):
        repo = Repository(name="r")
        for _ in range(3):
            repo.create_entry("x", "", "", ["alpha", "beta"])
        repo.create_entry("y", "", "", ["alpha", "gamma"])
        related = related_tags(repo, "alpha")
        self.assertEqual(related[0], "beta")
        self.assertIn("gamma", related)


if __name__ == "__main__":
    unittest.main()
