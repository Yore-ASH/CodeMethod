"""独立空间测试: 项目结构、路径安全、语言占比、README 索引与检索。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.languages import get_language  # noqa: E402
from codemethod.core.repository import Repository, RepositoryError  # noqa: E402
from codemethod.core.spaces import (  # noqa: E402
    MAX_BINARY_BYTES,
    MAX_FILES_PER_SPACE,
    MAX_FILE_BYTES,
    MAX_SPACE_BINARY_BYTES,
    DirectoryScan,
    ImportOptions,
    PlannedFile,
    ProjectFile,
    Space,
    build_tree,
    decode_text,
    hex_dump,
    human_bytes,
    is_binary_path,
    is_image_path,
    is_readme_path,
    looks_binary,
    normalize_project_path,
    scan_external_directory,
    search_readmes,
)

# 一段"看起来像二进制"的字节 (含 NUL, 且不是合法 UTF-8)
FAKE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) * 4


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


class TestBinarySniffing(unittest.TestCase):
    """扩展名不可靠, 导入外部文件时还要看内容。"""

    def test_nul_byte_means_binary(self):
        self.assertTrue(looks_binary(b"abc\x00def"))

    def test_utf8_text_is_not_binary(self):
        self.assertFalse(looks_binary("中文 README 内容\n第二行\n".encode("utf-8")))
        self.assertFalse(looks_binary(b""))
        self.assertFalse(looks_binary(b"plain ascii\n"))

    def test_invalid_utf8_is_binary(self):
        self.assertTrue(looks_binary(b"\xff\xfe\x00\x01"))

    def test_decode_text_returns_none_for_binary(self):
        self.assertEqual(decode_text("你好".encode("utf-8")), "你好")
        self.assertIsNone(decode_text(b"\xff\xfe\x00"))

    def test_image_detection_needs_binary_flag(self):
        self.assertTrue(is_image_path("a/logo.PNG"))
        self.assertFalse(is_image_path("a/main.py"))

    def test_human_bytes(self):
        self.assertEqual(human_bytes(0), "0 B")
        self.assertEqual(human_bytes(900), "900 B")
        self.assertEqual(human_bytes(1536), "1.5 KB")
        self.assertEqual(human_bytes(5 * 1024 * 1024), "5.0 MB")

    def test_hex_dump_shape(self):
        text = hex_dump(b"ABCD", limit=16)
        self.assertIn("00000000", text)
        self.assertIn("41 42 43 44", text)
        self.assertIn("|ABCD|", text)


class TestEmbeddedBinary(unittest.TestCase):
    """二进制内容是真的嵌进库里的 (base64), 不是引用。"""

    def test_set_bytes_roundtrips_exactly(self):
        file = ProjectFile(path="assets/logo.png")
        file.set_bytes(FAKE_PNG)
        self.assertTrue(file.binary)
        self.assertTrue(file.has_data)
        self.assertEqual(file.size, len(FAKE_PNG))
        self.assertEqual(file.raw_bytes(), FAKE_PNG)
        self.assertEqual(file.language, "plaintext")
        self.assertEqual(file.line_count, 0)

    def test_survives_dict_roundtrip(self):
        file = ProjectFile(path="a.bin")
        file.set_bytes(FAKE_PNG)
        restored = ProjectFile.from_dict(file.to_dict())
        self.assertEqual(restored.raw_bytes(), FAKE_PNG)
        self.assertEqual(restored.to_dict(), file.to_dict())

    def test_sha256_and_hex(self):
        file = ProjectFile(path="a.bin")
        file.set_bytes(b"hello")
        self.assertEqual(
            file.sha256(),
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )
        self.assertIn("68 65 6C 6C 6F", file.hex_preview())

    def test_size_is_raw_bytes_not_base64_length(self):
        file = ProjectFile(path="a.bin")
        file.set_bytes(b"x" * 100)
        self.assertEqual(file.size, 100)
        self.assertEqual(file.stored_bytes, len(file.data))
        self.assertGreater(file.stored_bytes, file.size)      # base64 会放大

    def test_write_to_disk_is_byte_exact(self):
        import tempfile

        file = ProjectFile(path="a.png")
        file.set_bytes(FAKE_PNG)
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "out.png")
            written = file.write_to(target)
            self.assertEqual(written, len(FAKE_PNG))
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), FAKE_PNG)

    def test_legacy_binary_without_data_still_loads(self):
        """1.4 以前的二进制文件只有 size, 读进来必须还能用 (只是"未嵌入")。"""
        legacy = {"path": "old.png", "binary": True, "size": 999, "content": ""}
        file = ProjectFile.from_dict(legacy)
        self.assertTrue(file.binary)
        self.assertFalse(file.has_data)
        self.assertFalse(file.embedded)
        self.assertEqual(file.size, 999)
        self.assertEqual(file.raw_bytes(), b"")

    def test_space_embedded_accounting(self):
        space = Space(name="s", files=[
            ProjectFile(path="a.bin"),
            ProjectFile(path="b.bin"),
        ])
        space.files[0].set_bytes(b"a" * 100)
        space.files[1].set_bytes(b"b" * 50)
        self.assertEqual(space.embedded_binary_bytes, 150)
        self.assertEqual(len(space.embedded_binary_files), 2)
        self.assertEqual(
            space.binary_budget_left(), MAX_SPACE_BINARY_BYTES - 150
        )
        # 覆盖已有文件时不该把这文件自己算两次
        self.assertEqual(
            space.binary_budget_left(exclude_path="a.bin"), MAX_SPACE_BINARY_BYTES - 50
        )

    def test_tree_marks_embedded_state(self):
        space = Space(name="s", files=[
            ProjectFile(path="assets/a.png", binary=True, size=10),
        ])
        node = space.tree()
        child = node.sorted_children()[0].sorted_children()[0]
        self.assertTrue(child.binary)
        self.assertFalse(child.embedded)

    def test_binary_excluded_from_search_blob(self):
        space = Space(name="s", files=[ProjectFile(path="a.bin")])
        space.files[0].set_bytes(b"SECRETBYTES\x00")
        self.assertNotIn("secretbytes", space.search_blob)


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

    def test_oversized_binary_rejected(self):
        with self.assertRaises(RepositoryError):
            self.repo.put_space_file(
                self.space.id, "big.bin", binary_data=b"x" * (MAX_BINARY_BYTES + 1)
            )

    def test_binary_budget_enforced(self):
        """单空间嵌入总量超限时报错 (整体预算用一个很小的假上限来测)。"""
        from unittest import mock

        import codemethod.core.spaces as spaces_mod

        space = self.repo.create_space("预算")
        with mock.patch.object(spaces_mod, "MAX_SPACE_BINARY_BYTES", 4096):
            self.repo.put_space_file(space.id, "first.bin", binary_data=b"y" * 4000)
            self.assertEqual(
                self.repo.require_space(space.id).embedded_binary_bytes, 4000
            )
            with self.assertRaises(RepositoryError) as ctx:
                self.repo.put_space_file(space.id, "second.bin", binary_data=b"z" * 100)
            self.assertIn("只剩", str(ctx.exception))
            # 覆盖同一个文件时可以释放它自己占的额度
            self.repo.put_space_file(space.id, "first.bin", binary_data=b"y" * 4096)

    def test_embed_binary_through_put_space_file(self):
        file = self.repo.put_space_file(
            self.space.id, "assets/logo.png", binary_data=FAKE_PNG
        )
        self.assertTrue(file.binary)
        self.assertEqual(file.raw_bytes(), FAKE_PNG)
        self.assertEqual(file.size, len(FAKE_PNG))
        detail = self.repo.revisions(self.space.id)[0].summary
        self.assertIn("已嵌入", detail)

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

    def test_batch_import_accepts_bytes(self):
        """批量导入里给 bytes 就按二进制嵌进去。"""
        self.repo.import_space_files(
            self.space.id, [("a.py", "1"), ("assets/logo.png", FAKE_PNG)]
        )
        space = self.repo.require_space(self.space.id)
        self.assertEqual(space.get_file("assets/logo.png").raw_bytes(), FAKE_PNG)

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


class TestExternalImport(unittest.TestCase):
    """导入外部文件: 内容直接嵌进 .cmdb, 拷走一个文件就能完整还原。"""

    def setUp(self):
        import tempfile

        from codemethod.storage.database import Database

        self.repo = Repository(name="imp")
        self.space = self.repo.create_space("导入测试")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.db = Database(self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str, data: bytes) -> str:
        target = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        return target

    def test_binary_and_text_are_detected(self):
        png = self._write("logo.png", FAKE_PNG)
        txt = self._write("hello.txt", "你好\n".encode("utf-8"))
        report = self.repo.import_external_files(self.space.id, [png, txt])
        self.assertEqual(report.added, 2)
        self.assertEqual(report.binary, 1)
        self.assertEqual(report.text, 1)
        space = self.repo.require_space(self.space.id)
        self.assertEqual(space.get_file("logo.png").raw_bytes(), FAKE_PNG)
        self.assertEqual(space.get_file("hello.txt").content, "你好\n")

    def test_extensionless_binary_is_sniffed(self):
        blob = self._write("payload", b"\x00\x01\x02\xff\xfe")
        self.repo.import_external_files(self.space.id, [blob])
        file = self.repo.require_space(self.space.id).get_file("payload")
        self.assertTrue(file.binary)
        self.assertEqual(file.raw_bytes(), b"\x00\x01\x02\xff\xfe")

    def test_named_png_with_text_content_is_still_binary(self):
        """扩展名说是二进制, 就按二进制处理 (即使内容碰巧是 ASCII)。"""
        odd = self._write("fake.png", b"just text")
        self.repo.import_external_files(self.space.id, [odd])
        file = self.repo.require_space(self.space.id).get_file("fake.png")
        self.assertTrue(file.binary)
        self.assertEqual(file.raw_bytes(), b"just text")

    def test_base_dir_preserves_tree(self):
        self._write("src/main.py", b"print(1)\n")
        self._write("assets/logo.png", FAKE_PNG)
        import glob

        sources = [
            p for p in glob.glob(os.path.join(self.tmp, "**", "*"), recursive=True)
            if os.path.isfile(p)
        ]
        report = self.repo.import_external_files(
            self.space.id, sources, base_dir=self.tmp
        )
        paths = {f.path for f in self.repo.require_space(self.space.id).files}
        self.assertEqual(paths, {"src/main.py", "assets/logo.png"})
        self.assertEqual(report.binary, 1)
        self.assertEqual(report.text, 1)

    def test_target_dir(self):
        png = self._write("a.png", FAKE_PNG)
        self.repo.import_external_files(self.space.id, [png], target_dir="static/img")
        self.assertIsNotNone(
            self.repo.require_space(self.space.id).get_file("static/img/a.png")
        )

    def test_one_revision_per_batch(self):
        before = self.repo.history.count(self.space.id)
        files = [self._write(f"f{i}.txt", b"x") for i in range(5)]
        self.repo.import_external_files(self.space.id, files)
        self.assertEqual(self.repo.history.count(self.space.id), before + 1)

    def test_missing_and_skipped_reported(self):
        missing = os.path.join(self.tmp, "nope.bin")
        report = self.repo.import_external_files(self.space.id, [missing])
        self.assertEqual(report.changed, 0)
        self.assertEqual(len(report.skipped), 1)

    def test_oversized_binary_is_skipped_not_raised(self):
        big = self._write("big.bin", b"\x00" * (MAX_BINARY_BYTES + 16))
        report = self.repo.import_external_files(self.space.id, [big])
        self.assertEqual(report.changed, 0)
        self.assertIn("上限", report.skipped[0][1])

    def test_import_overwrites_existing_keeps_note(self):
        self.repo.put_space_file(self.space.id, "logo.png", "", binary=True, size=1)
        space = self.repo.require_space(self.space.id)
        space.get_file("logo.png").note = "项目图标"
        source = self._write("logo.png", FAKE_PNG)
        report = self.repo.import_external_files(self.space.id, [source])
        self.assertEqual(report.updated, 1)
        self.assertEqual(report.added, 0)
        file = self.repo.require_space(self.space.id).get_file("logo.png")
        self.assertEqual(file.note, "项目图标")
        self.assertEqual(file.raw_bytes(), FAKE_PNG)

    def test_directory_import_skips_vcs_dirs(self):
        self._write("src/a.py", b"1")
        self._write(".git/config", b"x")
        self._write("__pycache__/a.pyc", b"\x00\x01")
        report = self.repo.import_external_directory(self.space.id, self.tmp)
        paths = {f.path for f in self.repo.require_space(self.space.id).files}
        self.assertEqual(paths, {"src/a.py"})
        self.assertEqual(report.added, 1)

    def test_export_space_file_roundtrip(self):
        png = self._write("logo.png", FAKE_PNG)
        self.repo.import_external_files(self.space.id, [png])
        target = os.path.join(self.tmp, "out", "copy.png")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        written = self.repo.export_space_file(self.space.id, "logo.png", target)
        self.assertEqual(written, len(FAKE_PNG))
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), FAKE_PNG)

    def test_export_legacy_binary_is_refused(self):
        self.repo.put_space_file(self.space.id, "old.png", "", binary=True, size=10)
        with self.assertRaises(RepositoryError):
            self.repo.export_space_file(
                self.space.id, "old.png", os.path.join(self.tmp, "x.png")
            )

    def test_container_roundtrip_keeps_binary_bytes(self):
        """真正的验收点: 存盘再读回来, 二进制字节必须一模一样。"""
        png = self._write("logo.png", FAKE_PNG)
        self._write("hello.txt", "你好\n".encode("utf-8"))
        self.repo.import_external_files(self.space.id, [png])
        path = os.path.join(self.tmp, "lib.cmdb")
        self.db.save_as(path)
        reopened = type(self.db).open(path)
        space = reopened.repository.require_space(self.space.id)
        self.assertEqual(space.get_file("logo.png").raw_bytes(), FAKE_PNG)
        self.assertEqual(space.embedded_binary_bytes, len(FAKE_PNG))

    def test_undo_restores_binary_content(self):
        png = self._write("logo.png", FAKE_PNG)
        self.repo.import_external_files(self.space.id, [png])
        self.assertTrue(self.repo.can_undo(self.space.id))
        self.repo.undo(self.space.id)
        self.assertIsNone(self.repo.require_space(self.space.id).get_file("logo.png"))
        self.repo.redo(self.space.id)
        self.assertEqual(
            self.repo.require_space(self.space.id).get_file("logo.png").raw_bytes(),
            FAKE_PNG,
        )

    def test_zip_export_writes_real_bytes(self):
        import zipfile

        from codemethod.storage import exporter

        png = self._write("assets/logo.png", FAKE_PNG)
        self.repo.import_external_files(self.space.id, [png])
        target = os.path.join(self.tmp, "out.zip")
        exporter.export_zip(self.repo, target)
        with zipfile.ZipFile(target) as archive:
            names = [n for n in archive.namelist() if n.endswith("logo.png")]
            self.assertTrue(names, archive.namelist())
            self.assertEqual(archive.read(names[0]), FAKE_PNG)

    def test_binary_diff_never_dumps_base64(self):
        png = self._write("logo.png", FAKE_PNG)
        self.repo.import_external_files(self.space.id, [png])
        revision = self.repo.revisions(self.space.id)[0]
        diff = self.repo.revision_diff(revision.id)
        self.assertIn("二进制", diff)
        self.assertNotIn(FAKE_PNG[:24].hex(), diff)
        self.assertLess(len(diff), 4000)

    def test_binary_update_summary_mentions_growth(self):
        png = self._write("logo.png", FAKE_PNG)
        self.repo.import_external_files(self.space.id, [png])
        bigger = self._write("logo2.png", FAKE_PNG + b"\x00" * 2048)
        os.replace(bigger, png)
        self.repo.import_external_files(self.space.id, [png])
        summary = self.repo.revisions(self.space.id)[0].summary
        self.assertIn("二进制内容变更", summary)
        self.assertIn(str(len(FAKE_PNG)), summary)


class TestDirectoryPackaging(unittest.TestCase):
    """把一整个目录连同子文件夹打包进空间 —— 先扫描出计划, 再照着计划执行。"""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.repo = Repository(name="pack")
        self.space = self.repo.create_space("打包")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str, data: bytes) -> str:
        target = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        return target

    def _project(self) -> None:
        self._write("README.md", "# 项目\n中文说明\n".encode("utf-8"))
        self._write("src/main.py", b"import os\n")
        self._write("src/utils/helper.py", b"def helper():\n    return 1\n")
        self._write("docs/GUIDE.md", "# 指南\n".encode("utf-8"))
        self._write(".gitignore", b"*.pyc\n")
        self._write(".git/objects/ab/cdef", b"\x00\x01")
        self._write("__pycache__/main.pyc", b"\x00\x00")
        self._write("node_modules/leftpad/index.js", b"module.exports=1\n")
        self._write("assets/logo.png", FAKE_PNG)
        self._write("build/output.bin", b"\x00" * 32)

    # ---- 扫描 ----
    def test_scan_keeps_structure_and_reports_everything(self):
        self._project()
        scan = scan_external_directory(self.root, ImportOptions())
        paths = {f.path for f in scan.included}
        self.assertIn("src/main.py", paths)
        self.assertIn("src/utils/helper.py", paths)
        self.assertIn("docs/GUIDE.md", paths)
        self.assertIn("assets/logo.png", paths)
        self.assertIn(".gitignore", paths)          # 隐藏文件默认保留

    def test_scan_prunes_vcs_cache_and_build_dirs(self):
        self._project()
        scan = scan_external_directory(self.root, ImportOptions())
        paths = {f.path for f in scan.included}
        for gone in (".git/objects/ab/cdef", "__pycache__/main.pyc",
                     "node_modules/leftpad/index.js", "build/output.bin"):
            self.assertNotIn(gone, paths)
        # 被剪掉的整个目录必须**显式记录下来**, 否则用户以为文件凭空消失
        pruned = {path for path, _reason in scan.pruned_dirs}
        self.assertIn(".git", pruned)
        self.assertIn("__pycache__", pruned)
        self.assertIn("node_modules", pruned)
        self.assertIn("build", pruned)
        self.assertTrue(scan.pruned_reasons())

    def test_scan_options_can_include_everything(self):
        self._project()
        scan = scan_external_directory(
            self.root,
            ImportOptions(include_vcs=True, include_caches=True),
        )
        paths = {f.path for f in scan.included}
        self.assertIn(".git/objects/ab/cdef", paths)
        self.assertIn("node_modules/leftpad/index.js", paths)
        self.assertEqual(scan.pruned_dirs, [])

    def test_compiled_artifacts_are_always_skipped(self):
        """``.pyc`` / ``.o`` 这类编译产物没有保留价值, 即使要求"含缓存目录"也不收。"""
        self._project()
        scan = scan_external_directory(
            self.root,
            ImportOptions(include_vcs=True, include_caches=True),
        )
        skipped = {f.path: f.reason for f in scan.skipped}
        self.assertIn("__pycache__/main.pyc", skipped)
        self.assertIn("缓存/临时文件", skipped["__pycache__/main.pyc"])

    def test_scan_hidden_can_be_excluded(self):
        self._project()
        scan = scan_external_directory(self.root, ImportOptions(include_hidden=False))
        paths = {f.path for f in scan.included}
        self.assertNotIn(".gitignore", paths)
        # 隐藏**文件**在文件级被跳过, 隐藏**目录**在目录级被剪掉
        hidden_files = [f for f in scan.skipped if f.reason == "隐藏文件"]
        self.assertTrue(hidden_files, scan.reasons())

    def test_scan_without_recursion_can_flatten(self):
        self._project()
        scan = scan_external_directory(
            self.root, ImportOptions(recursive=False, keep_structure=False)
        )
        self.assertEqual({f.path for f in scan.included}, {"README.md", ".gitignore"})

    def test_big_text_is_embedded_as_bytes_instead_of_skipped(self):
        """3 MB 的纯文本放不进编辑器, 但"完全打包"不该把它丢掉。"""
        self._write("big.log", b"x" * (MAX_FILE_BYTES + 1024))
        scan = scan_external_directory(self.root, ImportOptions())
        item = next(f for f in scan.included if f.path == "big.log")
        self.assertTrue(item.binary)

    def test_file_over_binary_limit_is_reported(self):
        self._write("huge.bin", b"\x00" * (MAX_BINARY_BYTES + 16))
        scan = scan_external_directory(self.root, ImportOptions())
        skipped = [f for f in scan.skipped if f.path == "huge.bin"]
        self.assertEqual(len(skipped), 1)
        self.assertIn("上限", skipped[0].reason)

    def test_scan_missing_directory_reports_error(self):
        scan = scan_external_directory(os.path.join(self.root, "nope"), ImportOptions())
        self.assertTrue(scan.error)
        self.assertEqual(scan.included, [])

    def test_scan_target_dir_prefixes_everything(self):
        self._project()
        scan = scan_external_directory(
            self.root, ImportOptions(target_dir="vendor/lib")
        )
        self.assertTrue(all(f.path.startswith("vendor/lib/") for f in scan.included))

    def test_scan_does_not_read_whole_big_file(self):
        """扫描只读每个文件的前 8 KB, 所以几千个文件也很快。"""
        self._write("big.bin", b"\x00" * (2 * 1024 * 1024))
        scan = scan_external_directory(self.root, ImportOptions())
        self.assertEqual(scan.included[0].size, 2 * 1024 * 1024)

    # ---- 执行 ----
    def test_import_scan_matches_the_plan(self):
        self._project()
        scan = scan_external_directory(self.root, ImportOptions())
        report = self.repo.import_scan(self.space.id, scan)
        files = {f.path: f for f in self.repo.require_space(self.space.id).files}
        self.assertEqual(set(files), {f.path for f in scan.included})
        self.assertEqual(report.added, len(scan.included))
        self.assertEqual(files["assets/logo.png"].raw_bytes(), FAKE_PNG)

    def test_import_scan_is_one_revision(self):
        self._project()
        scan = scan_external_directory(self.root, ImportOptions())
        before = self.repo.history.count(self.space.id)
        self.repo.import_scan(self.space.id, scan)
        self.assertEqual(self.repo.history.count(self.space.id), before + 1)
        self.assertIn("打包导入", self.repo.revisions(self.space.id)[0].summary)

    def test_import_scan_records_skipped_files(self):
        self._write("ok.txt", b"1")
        self._write("huge.bin", b"\x00" * (MAX_BINARY_BYTES + 16))
        scan = scan_external_directory(self.root, ImportOptions())
        report = self.repo.import_scan(self.space.id, scan)
        self.assertEqual(report.added, 1)
        self.assertEqual(len(report.skipped), 1)
        self.assertIn("huge.bin", report.skipped[0][0])

    def test_import_scan_refuses_to_blow_the_binary_budget(self):
        from unittest import mock

        import codemethod.core.spaces as spaces_mod

        self._project()
        scan = scan_external_directory(self.root, ImportOptions())
        with mock.patch.object(spaces_mod, "MAX_SPACE_BINARY_BYTES", 10):
            with self.assertRaises(RepositoryError) as ctx:
                self.repo.import_scan(self.space.id, scan)
            self.assertIn("只剩", str(ctx.exception))
            report = self.repo.import_scan(self.space.id, scan, force=True)
        self.assertGreater(report.added, 0)

    def test_import_external_directory_uses_the_same_planner(self):
        self._project()
        report = self.repo.import_external_directory(self.space.id, self.root)
        paths = {f.path for f in self.repo.require_space(self.space.id).files}
        self.assertIn("src/main.py", paths)
        self.assertNotIn("__pycache__/main.pyc", paths)
        self.assertGreater(report.binary, 0)

    def test_container_roundtrip_after_packaging(self):
        self._project()
        self.repo.import_external_directory(self.space.id, self.root)
        restored = Repository.from_dict(self.repo.to_dict())
        files = {f.path: f for f in restored.require_space(self.space.id).files}
        self.assertEqual(files["assets/logo.png"].raw_bytes(), FAKE_PNG)
        self.assertEqual(files["src/main.py"].content, "import os\n")

    def test_undo_removes_the_whole_packaged_tree(self):
        self._project()
        self.repo.import_external_directory(self.space.id, self.root)
        self.assertTrue(self.repo.can_undo(self.space.id))
        self.repo.undo(self.space.id)
        self.assertEqual(self.repo.require_space(self.space.id).files, [])
        self.repo.redo(self.space.id)
        self.assertGreater(len(self.repo.require_space(self.space.id).files), 1)


class TestTagUsageAcrossKinds(unittest.TestCase):
    """标签计数必须覆盖模块 / 空间 / 函数体三类实体。

    早先只统计 ``self.entries``, 于是只给空间或函数体打过的标签永远显示 "0 次",
    标签面板还会把它标成斜体 (表示"从未使用") —— 用户看起来就是统计坏了。
    """

    def setUp(self):
        self.repo = Repository(name="tags")
        self.module = self.repo.create_entry("模块", "d", "p", ["shared", "only-module"])
        self.space = self.repo.create_space("空间", "d", "p", ["shared", "only-space"])
        self.function = self.repo.create_function(
            "fn", "python", "def fn():\n    return 1\n", tags=["shared", "only-fn"]
        )

    def test_usage_counts_all_three_kinds(self):
        usage = self.repo.tag_usage()
        self.assertEqual(usage["shared"], 3)
        self.assertEqual(usage["only-module"], 1)
        self.assertEqual(usage["only-space"], 1)
        self.assertEqual(usage["only-fn"], 1)

    def test_usage_ignores_deleted(self):
        self.repo.delete_space(self.space.id)
        self.assertEqual(self.repo.tag_usage()["shared"], 2)
        self.assertNotIn("only-space", self.repo.tag_usage())

    def test_usage_by_kind_breakdown(self):
        detail = self.repo.tag_usage_by_kind()["shared"]
        self.assertEqual(detail, {"module": 1, "space": 1, "function": 1})

    def test_all_tags_sorted_by_real_usage(self):
        self.assertEqual(self.repo.all_tags()[0], "shared")

    def test_space_only_tag_is_not_reported_as_unused(self):
        """这正是用户看到的 bug: 只给空间打过的标签显示 0 次 + 斜体。"""
        usage = self.repo.tag_usage()
        self.assertGreater(usage.get("only-space", 0), 0)


if __name__ == "__main__":
    unittest.main()
