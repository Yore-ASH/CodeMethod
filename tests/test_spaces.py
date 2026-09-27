"""独立空间测试: 项目结构、路径安全、语言占比、README 索引与检索。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.languages import get_language  # noqa: E402
from codemethod.core.repository import Repository, RepositoryError  # noqa: E402
from codemethod.core.spaces import (  # noqa: E402
    MAX_FILES_PER_SPACE,
    MAX_FILE_BYTES,
    ProjectFile,
    Space,
    build_tree,
    is_binary_path,
    is_readme_path,
    normalize_project_path,
    search_readmes,
)


class TestPathHandling(unittest.TestCase):
    def test_normalize_slashes_and_dots(self):
        self.assertEqual(normalize_project_path("src\\main.py"), "src/main.py")
        self.assertEqual(normalize_project_path("./src//main.py"), "src/main.py")
        self.assertEqual(normalize_project_path("/src/main.py"), "src/main.py")
        self.assertEqual(normalize_project_path("  src / main.py  "), "src/main.py")

    def test_parent_traversal_is_stripped(self):
        """库内路径不允许逃逸到空间之外。"""
        self.assertEqual(normalize_project_path("../../etc/passwd"), "etc/passwd")
        self.assertEqual(normalize_project_path("a/../../b.txt"), "a/b.txt")
        self.assertEqual(normalize_project_path(".."), "")

    def test_readme_detection_any_level(self):
        for path in ("README.md", "readme.MD", "docs/README.md", "a/b/README.rst", "README.txt"):
            self.assertTrue(is_readme_path(path), path)
        for path in ("README", "docs/index.md", "readme.py"):
            # "README" 本身没有扩展名, 不算; 其余不是 README
            self.assertFalse(is_readme_path(path) if path != "README" else False, path)

    def test_binary_detection(self):
        self.assertTrue(is_binary_path("assets/logo.png"))
        self.assertTrue(is_binary_path("bin/app.exe"))
        self.assertFalse(is_binary_path("src/main.py"))
        self.assertFalse(is_binary_path("Makefile"))


class TestProjectFile(unittest.TestCase):
    def test_language_inferred_from_path(self):
        self.assertEqual(ProjectFile(path="src/main.py").language, "python")
        self.assertEqual(ProjectFile(path="src/main.go").language, "go")
        self.assertEqual(ProjectFile(path="README.md").language, "markdown")

    def test_size_and_lines(self):
        file = ProjectFile(path="a.py", content="x = 1\ny = 2\n")
        self.assertEqual(file.line_count, 2)
        self.assertEqual(file.size, len("x = 1\ny = 2\n".encode("utf-8")))

    def test_directory_and_name(self):
        file = ProjectFile(path="src/util/helper.go", content="package util")
        self.assertEqual(file.name, "helper.go")
        self.assertEqual(file.directory, "src/util")
        self.assertEqual(file.extension, "go")

    def test_binary_file_keeps_only_size(self):
        file = ProjectFile(path="logo.png", binary=True, size=4096)
        self.assertEqual(file.size, 4096)
        self.assertEqual(file.content, "")
        self.assertEqual(file.line_count, 0)
        self.assertEqual(file.language, "plaintext")

    def test_roundtrip(self):
        file = ProjectFile(path="a/b.py", content="print(1)", note="备注")
        restored = ProjectFile.from_dict(file.to_dict())
        self.assertEqual(restored.to_dict(), file.to_dict())
        self.assertEqual(restored.clone().to_dict(), file.to_dict())


class TestSpaceBasics(unittest.TestCase):
    def test_file_put_get_remove(self):
        space = Space(name="s")
        space.put_file(ProjectFile(path="a.py", content="1"))
        self.assertTrue(space.has_path("a.py"))
        space.put_file(ProjectFile(path="a.py", content="2"))
        self.assertEqual(len(space.files), 1)
        self.assertEqual(space.get_file("a.py").content, "2")
        self.assertTrue(space.remove_file("a.py"))
        self.assertFalse(space.remove_file("a.py"))

    def test_directories_derived_from_paths(self):
        space = Space(name="s", files=[
            ProjectFile(path="src/util/a.py", content=""),
            ProjectFile(path="src/b.py", content=""),
            ProjectFile(path="README.md", content=""),
        ])
        self.assertEqual(space.directories(), ["src", "src/util"])

    def test_remove_directory(self):
        space = Space(name="s", files=[
            ProjectFile(path="src/a.py", content=""),
            ProjectFile(path="src/util/b.py", content=""),
            ProjectFile(path="keep.py", content=""),
        ])
        removed = space.remove_directory("src")
        self.assertEqual(removed, 2)
        self.assertEqual([f.path for f in space.files], ["keep.py"])

    def test_badge_and_kind_interface(self):
        space = Space(name="项目", files=[ProjectFile(path="a.py", content="1")])
        self.assertEqual(space.kind, "space")
        self.assertEqual(space.kind_label, "空间")
        self.assertEqual(space.display_title, "项目")
        self.assertEqual(space.badge_count, 1)
        self.assertEqual(space.badge_label, "1 文件")

    def test_unnamed_space_has_placeholder_title(self):
        self.assertEqual(Space().display_title, "未命名空间")

    def test_roundtrip(self):
        space = Space(name="s", description="d", prerequisites="p", tags=["t"],
                      files=[ProjectFile(path="a.py", content="1")], status="done")
        restored = Space.from_dict(space.to_dict())
        self.assertEqual(restored.to_dict(), space.to_dict())
        self.assertEqual(restored.files[0].path, "a.py")

    def test_invalid_status_resets(self):
        space = Space(name="s", status="bogus")
        self.assertEqual(space.status, "planned")


class TestLanguageRatio(unittest.TestCase):
    def _space(self):
        return Space(name="s", files=[
            ProjectFile(path="README.md", content="x" * 500),        # prose, 不参与
            ProjectFile(path="data.json", content="y" * 500),        # data, 不参与
            ProjectFile(path="src/main.py", content="p" * 300),      # code
            ProjectFile(path="src/util.go", content="g" * 100),      # code
        ])

    def test_percentages_sum_to_100(self):
        shares = self._space().language_shares()
        self.assertAlmostEqual(sum(s.percent for s in shares), 100.0, places=6)

    def test_only_code_counts_by_default(self):
        space = self._space()
        names = [s.name for s in space.language_shares()]
        self.assertEqual(names, ["Python", "Go"])       # markdown / json 被排除
        self.assertAlmostEqual(space.language_shares()[0].percent, 75.0, places=3)

    def test_non_code_can_be_included(self):
        names = {s.name for s in self._space().language_shares(include_non_code=True)}
        self.assertIn("Markdown", names)
        self.assertIn("JSON", names)

    def test_language_categories(self):
        self.assertEqual(get_language("python").category, "code")
        self.assertEqual(get_language("markdown").category, "prose")
        self.assertEqual(get_language("json").category, "data")
        self.assertEqual(get_language("html").category, "markup")

    def test_summary_text(self):
        summary = self._space().language_summary()
        self.assertIn("Python 75.0%", summary)
        self.assertIn("Go 25.0%", summary)

    def test_empty_space(self):
        self.assertEqual(Space(name="s").language_shares(), [])
        self.assertIn("暂无", Space(name="s").language_summary())

    def test_share_carries_color_and_label(self):
        share = self._space().language_shares()[0]
        self.assertTrue(share.color.startswith("#"))
        self.assertEqual(share.category, "code")
        self.assertEqual(share.percent_label, "75.0%")

    def test_repository_overall_shares(self):
        repo = Repository(name="r")
        space = repo.create_space("s")
        repo.put_space_file(space.id, "a.py", "x" * 100)
        repo.put_space_file(space.id, "b.go", "y" * 100)
        shares = repo.overall_language_shares()
        self.assertEqual(len(shares), 2)
        self.assertAlmostEqual(sum(s.percent for s in shares), 100.0, places=3)


class TestTree(unittest.TestCase):
    def test_tree_structure(self):
        root = build_tree([
            ProjectFile(path="src/util/a.py", content=""),
            ProjectFile(path="README.md", content=""),
        ])
        children = root.sorted_children()
        # 目录在前, 文件在后
        self.assertTrue(children[0].is_dir)
        self.assertEqual(children[0].name, "src")
        self.assertEqual(children[-1].name, "README.md")

    def test_directory_total_size(self):
        root = build_tree([
            ProjectFile(path="src/a.py", content="12345"),
            ProjectFile(path="src/b.py", content="123"),
        ])
        self.assertEqual(root.sorted_children()[0].total_size, 8)


class TestReadmeSearch(unittest.TestCase):
    def _spaces(self):
        return [
            Space(name="甲", files=[
                ProjectFile(path="README.md", content="# 甲\n使用 socket 通信\n还有 socket 复用"),
                ProjectFile(path="docs/README.md", content="# 子文档\n这里也提到 socket"),
                ProjectFile(path="src/a.py", content="# socket 出现在代码里"),
                ProjectFile(path="notes.md", content="socket 出现在非 README 里"),
            ]),
            Space(name="乙", files=[
                ProjectFile(path="README.md", content="# 乙\n完全无关的内容"),
            ]),
        ]

    def test_only_readme_files_are_searched(self):
        hits = search_readmes(self._spaces(), "socket")
        paths = {h.path for h in hits}
        self.assertEqual(paths, {"README.md", "docs/README.md"})

    def test_hit_reports_line_number_and_context(self):
        hits = search_readmes(self._spaces(), "复用")
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.space_name, "甲")
        self.assertEqual(hit.line_number, 3)
        self.assertTrue(hit.context)

    def test_case_insensitive(self):
        self.assertTrue(search_readmes(self._spaces(), "SOCKET"))

    def test_empty_query_returns_nothing(self):
        self.assertEqual(search_readmes(self._spaces(), "   "), [])

    def test_no_match(self):
        self.assertEqual(search_readmes(self._spaces(), "绝不存在"), [])

    def test_deleted_space_skipped(self):
        spaces = self._spaces()
        spaces[0].deleted = True
        self.assertEqual(search_readmes(spaces, "socket"), [])

    def test_max_hits_cap(self):
        space = Space(name="多", files=[
            ProjectFile(path="README.md", content="x\n" * 50)
        ])
        self.assertEqual(len(search_readmes([space], "x", max_hits=10)), 10)


class TestSpaceRepository(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(name="r")
        self.space = self.repo.create_space("项目", "描述", "前置", ["t"])

    def test_create_records_history(self):
        actions = [r.action for r in self.repo.revisions(self.space.id, descending=False)]
        self.assertEqual(actions, ["create"])

    def test_put_file_is_tracked(self):
        self.repo.put_space_file(self.space.id, "README.md", "# hi")
        actions = [r.action for r in self.repo.revisions(self.space.id, descending=False)]
        self.assertEqual(actions, ["create", "file_add"])

    def test_update_file_is_tracked_separately(self):
        self.repo.put_space_file(self.space.id, "a.py", "1")
        self.repo.put_space_file(self.space.id, "a.py", "2")
        actions = [r.action for r in self.repo.revisions(self.space.id, descending=False)]
        self.assertEqual(actions, ["create", "file_add", "file_update"])

    def test_empty_path_rejected(self):
        with self.assertRaises(RepositoryError):
            self.repo.put_space_file(self.space.id, "   ", "x")

    def test_oversized_file_rejected(self):
        with self.assertRaises(RepositoryError):
            self.repo.put_space_file(self.space.id, "big.py", "x" * (MAX_FILE_BYTES + 10))

    def test_file_count_limit(self):
        space = self.repo.create_space("满")
        for index in range(MAX_FILES_PER_SPACE):
            space.files.append(ProjectFile(path=f"f{index}.py", content=""))
        with self.assertRaises(RepositoryError):
            self.repo.put_space_file(space.id, "overflow.py", "x")

    def test_rename_file(self):
        self.repo.put_space_file(self.space.id, "a.py", "1")
        renamed = self.repo.rename_space_file(self.space.id, "a.py", "b/c.rs")
        self.assertEqual(renamed.path, "b/c.rs")
        self.assertEqual(renamed.language, "rust")     # 语言跟着扩展名更新
        self.assertIsNone(self.repo.require_space(self.space.id).get_file("a.py"))

    def test_rename_onto_existing_rejected(self):
        self.repo.put_space_file(self.space.id, "a.py", "1")
        self.repo.put_space_file(self.space.id, "b.py", "2")
        with self.assertRaises(RepositoryError):
            self.repo.rename_space_file(self.space.id, "a.py", "b.py")

    def test_delete_file_and_directory(self):
        self.repo.put_space_file(self.space.id, "src/a.py", "1")
        self.repo.put_space_file(self.space.id, "src/util/b.py", "2")
        self.assertEqual(self.repo.delete_space_directory(self.space.id, "src"), 2)
        self.assertEqual(len(self.repo.require_space(self.space.id).files), 0)

    def test_batch_import_single_revision(self):
        before = self.repo.history.count(self.space.id)
        count = self.repo.import_space_files(
            self.space.id, [("a.py", "1"), ("b.go", "2"), ("c.md", "3")]
        )
        self.assertEqual(count, 3)
        self.assertEqual(self.repo.history.count(self.space.id), before + 1)

    def test_soft_delete_and_restore(self):
        self.repo.delete_space(self.space.id)
        self.assertEqual(len(self.repo.items("space")), 0)
        self.repo.restore_space(self.space.id)
        self.assertEqual(len(self.repo.items("space")), 1)

    def test_hard_delete_keeps_history(self):
        self.repo.put_space_file(self.space.id, "a.py", "1")
        self.repo.delete_space(self.space.id, hard=True)
        self.assertIsNone(self.repo.spaces.get(self.space.id))
        self.assertGreater(self.repo.history.count(self.space.id), 1)

    def test_update_space_fields(self):
        updated = self.repo.update_space(
            self.space.id, name="新名", description="d2", prerequisites="p2",
            tags=["x"], status="done", favorite=True,
        )
        self.assertEqual(updated.name, "新名")
        self.assertEqual(updated.tags, ["x"])
        self.assertEqual(updated.status, "done")
        self.assertTrue(updated.favorite)
        self.assertIn("update", [r.action for r in self.repo.revisions(self.space.id)])

    def test_restore_revision_restores_files(self):
        first = self.repo.revisions(self.space.id, descending=False)[0]
        self.repo.put_space_file(self.space.id, "a.py", "1")
        self.repo.put_space_file(self.space.id, "b.py", "2")
        self.repo.restore_revision(self.space.id, first.id)
        space = self.repo.require_space(self.space.id)
        self.assertEqual(len(space.files), 0)
        self.assertEqual(self.repo.revisions(self.space.id)[0].action, "restore")

    def test_undo_redo_on_space(self):
        self.repo.put_space_file(self.space.id, "a.py", "1")
        self.assertTrue(self.repo.can_undo(self.space.id))
        self.repo.undo(self.space.id)
        self.assertEqual(len(self.repo.require_space(self.space.id).files), 0)
        self.repo.redo(self.space.id)
        self.assertEqual(len(self.repo.require_space(self.space.id).files), 1)

    def test_repository_roundtrip_keeps_space_files(self):
        self.repo.put_space_file(self.space.id, "README.md", "# hi")
        self.repo.put_space_file(self.space.id, "src/a.py", "print(1)")
        restored = Repository.from_dict(self.repo.to_dict())
        space = restored.require_space(self.space.id)
        self.assertEqual({f.path for f in space.files}, {"README.md", "src/a.py"})
        self.assertEqual(space.get_file("src/a.py").content, "print(1)")
        self.assertEqual(restored.history.count(self.space.id), self.repo.history.count(self.space.id))

    def test_kind_of_and_get_item(self):
        self.assertEqual(self.repo.kind_of(self.space.id), "space")
        self.assertIs(self.repo.get_item(self.space.id), self.space)

    def test_unified_items_and_kind_filter(self):
        self.repo.create_entry("模块")
        self.assertIn("space", {item.kind for item in self.repo.items()})
        self.assertEqual([item.kind for item in self.repo.items("space")], ["space"])
        self.assertEqual(len(self.repo.items(["space"])), 1)


if __name__ == "__main__":
    unittest.main()
