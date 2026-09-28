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


if __name__ == "__main__":
    unittest.main()
