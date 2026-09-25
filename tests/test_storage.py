"""存储层测试: 二进制/文本容器、完整性校验、备份、格式转换与导出。"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.models import Implementation  # noqa: E402
from codemethod.core.repository import Repository  # noqa: E402
from codemethod.storage import container as fmt  # noqa: E402
from codemethod.storage import exporter  # noqa: E402
from codemethod.storage.database import Database, DatabaseError  # noqa: E402


def make_repository() -> Repository:
    repo = Repository(name="存储测试", description="d", author="tester")
    entry = repo.create_entry(
        "TCP 服务器", "描述", "前置", ["network", "tcp"], status="done", favorite=True
    )
    repo.add_implementation(entry.id, "python", "import socket\nprint(1)", title="py")
    repo.add_implementation(entry.id, "go", "package main", title="go")
    repo.add_implementation(entry.id, "rust", "fn main(){}", title="rs")
    repo.update_entry(entry.id, description="描述 v2")
    second = repo.create_entry("LRU", "缓存", "", ["algorithm"])
    repo.add_implementation(second.id, "cpp", "int main(){}")
    return repo


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="codemethod-test-")
        self.repo = make_repository()
        self.db = Database(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name: str) -> str:
        return os.path.join(self.tmp, name)


class TestBinaryContainer(StorageTestCase):
    def test_roundtrip(self):
        target = self.path("a.cmdb")
        info = fmt.write_container(target, self.repo.to_dict())
        self.assertIs(info.kind, fmt.FormatKind.BINARY)
        self.assertTrue(info.compressed)

        data, read_info = fmt.read_container(target)
        restored = Repository.from_dict(data)
        self.assertEqual(len(restored.entries), 2)
        self.assertEqual(restored.history.total_count(), self.repo.history.total_count())
        self.assertEqual(read_info.version, "1.0")
        self.assertEqual(read_info.warnings, [])

    def test_header_layout_is_64_bytes(self):
        self.assertEqual(fmt.HEADER_STRUCT.size, 64)
        self.assertEqual(fmt.FOOTER_STRUCT.size, 32)
        blob, _ = fmt.build_container_bytes(self.repo.to_dict())
        self.assertTrue(blob.startswith(fmt.CONTAINER_MAGIC))
        self.assertTrue(blob.endswith(fmt.FOOTER_MAGIC))
        major, minor = struct.unpack_from("<HH", blob, 8)
        self.assertEqual((major, minor), (fmt.FORMAT_MAJOR, fmt.FORMAT_MINOR))

    def test_manifest_is_readable_without_payload(self):
        target = self.path("m.cmdb")
        fmt.write_container(target, self.repo.to_dict())
        _data, info = fmt.read_container(target)
        self.assertIsNotNone(info.manifest)
        self.assertEqual(info.manifest["stats"]["entries"], 2)
        self.assertEqual(info.manifest["stats"]["implementations"], 4)
        ids = {item["id"] for item in info.manifest["entries"]}
        self.assertEqual(len(ids), 2)

    def test_uncompressed_variant(self):
        target = self.path("u.cmdb")
        info = fmt.write_container(target, self.repo.to_dict(), compress=False)
        self.assertFalse(info.compressed)
        self.assertEqual(info.payload_len, info.payload_raw)
        data, _ = fmt.read_container(target)
        self.assertEqual(len(data["entries"]), 2)

    def test_detect_format_by_content_not_extension(self):
        binary = self.path("weird.txt")
        fmt.write_container(binary, self.repo.to_dict())
        self.assertIs(fmt.detect_format(binary), fmt.FormatKind.BINARY)
        text = self.path("also.txt")
        fmt.write_text(text, self.repo.to_dict())
        self.assertIs(fmt.detect_format(text), fmt.FormatKind.TEXT)

    def test_bad_magic_rejected(self):
        target = self.path("bad.cmdb")
        with open(target, "wb") as handle:
            handle.write(b"NOTCMDB!" + b"\x00" * 100)
        with self.assertRaises(fmt.CodeMethodFormatError):
            fmt.read_container(target)

    def test_truncated_file_rejected(self):
        target = self.path("t.cmdb")
        fmt.write_container(target, self.repo.to_dict())
        with open(target, "rb") as handle:
            blob = handle.read()
        with open(target, "wb") as handle:
            handle.write(blob[: len(blob) // 2])
        with self.assertRaises(fmt.CodeMethodFormatError):
            fmt.read_container(target)

    def test_payload_corruption_detected(self):
        target = self.path("c.cmdb")
        fmt.write_container(target, self.repo.to_dict(), compress=False)
        with open(target, "rb") as handle:
            blob = bytearray(handle.read())
        blob[fmt.HEADER_STRUCT.size + 40] ^= 0xFF  # 破坏载荷
        with open(target, "wb") as handle:
            handle.write(bytes(blob))
        with self.assertRaises(fmt.CodeMethodFormatError):
            fmt.read_container(target)
        ok, problems, _info = fmt.verify_file(target)
        self.assertFalse(ok)
        self.assertTrue(problems)

    def test_future_version_rejected(self):
        target = self.path("v.cmdb")
        fmt.write_container(target, self.repo.to_dict())
        with open(target, "rb") as handle:
            blob = bytearray(handle.read())
        struct.pack_into("<H", blob, 8, 99)
        with self.assertRaises(fmt.CodeMethodFormatError):
            fmt.read_container_bytes(bytes(blob))

    def test_header_tampering_is_detected(self):
        """footer_crc 覆盖头部, 因此改一个时间戳也会被发现。"""
        target = self.path("f.cmdb")
        fmt.write_container(target, self.repo.to_dict(), compress=False)
        with open(target, "rb") as handle:
            blob = bytearray(handle.read())
        struct.pack_into("<Q", blob, 48, 12345)  # 篡改 modified 时间戳
        data, info = fmt.read_container_bytes(bytes(blob))
        self.assertEqual(len(data["entries"]), 2)  # 数据本身仍可读
        self.assertTrue(any("校验和" in w for w in info.warnings), info.warnings)

        # 写回磁盘后, 完整校验应报告问题
        with open(target, "wb") as handle:
            handle.write(bytes(blob))
        ok, problems, _info = fmt.verify_file(target)
        self.assertFalse(ok)
        self.assertTrue(problems)

    def test_manifest_corruption_is_detected(self):
        target = self.path("mc.cmdb")
        fmt.write_container(target, self.repo.to_dict(), compress=False)
        with open(target, "rb") as handle:
            blob = bytearray(handle.read())
        header = fmt.HEADER_STRUCT.unpack_from(blob, 0)
        payload_len = header[5]
        manifest_start = fmt.HEADER_STRUCT.size + payload_len
        blob[manifest_start] ^= 0xFF  # 破坏清单首字节
        _data, info = fmt.read_container_bytes(bytes(blob))
        self.assertTrue(any("清单" in w for w in info.warnings), info.warnings)

    def test_verify_ok_for_valid_file(self):
        target = self.path("ok.cmdb")
        fmt.write_container(target, self.repo.to_dict())
        ok, problems, info = fmt.verify_file(target)
        self.assertTrue(ok, problems)
        self.assertIsNotNone(info)


class TestTextContainer(StorageTestCase):
    def test_roundtrip_and_is_readable_json(self):
        target = self.path("a.cmj")
        fmt.write_text(target, self.repo.to_dict())
        with open(target, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["format"], fmt.TEXT_FORMAT_TAG)
        self.assertIn("integrity", document)
        self.assertIn("entries", document["repository"])

        data, info = fmt.read_text(target)
        self.assertIs(info.kind, fmt.FormatKind.TEXT)
        self.assertEqual(len(data["entries"]), 2)

    def test_integrity_warning_on_manual_edit(self):
        target = self.path("e.cmj")
        fmt.write_text(target, self.repo.to_dict())
        with open(target, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        document["repository"]["entries"][0]["title"] = "被手工改过"
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False)
        data, info = fmt.read_text(target)
        self.assertTrue(any("integrity" in w for w in info.warnings))
        self.assertEqual(data["entries"][0]["title"], "被手工改过")

    def test_bare_repository_json_accepted(self):
        target = self.path("bare.cmj")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(self.repo.to_dict(), handle, ensure_ascii=False)
        data, info = fmt.read_text(target)
        self.assertEqual(len(data["entries"]), 2)
        self.assertTrue(info.warnings)

    def test_invalid_json_rejected(self):
        target = self.path("bad.cmj")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        with self.assertRaises(fmt.CodeMethodFormatError):
            fmt.read_text(target)


class TestDatabase(StorageTestCase):
    def test_save_creates_file_and_marks_clean(self):
        target = self.path("d.cmdb")
        result = self.db.save(target)
        self.assertTrue(os.path.exists(target))
        self.assertTrue(result.verified)
        self.assertFalse(self.repo.dirty)
        self.assertEqual(self.db.path, os.path.abspath(target))

    def test_save_is_atomic_no_temp_left(self):
        target = self.path("a.cmdb")
        self.db.save(target)
        leftovers = [n for n in os.listdir(self.tmp) if ".tmp-" in n]
        self.assertEqual(leftovers, [])

    def test_open_roundtrip(self):
        target = self.path("o.cmdb")
        self.db.save(target)
        reopened = Database.open(target)
        self.assertEqual(len(reopened.repository.entries), 2)
        self.assertEqual(
            reopened.repository.history.total_count(), self.repo.history.total_count()
        )
        self.assertFalse(reopened.repository.dirty)

    def test_open_missing_file_raises(self):
        with self.assertRaises(DatabaseError):
            Database.open(self.path("nope.cmdb"))

    def test_open_garbage_raises(self):
        target = self.path("g.cmdb")
        with open(target, "wb") as handle:
            handle.write(b"garbage data here")
        with self.assertRaises(DatabaseError):
            Database.open(target)

    def test_rolling_backups(self):
        target = self.path("b.cmdb")
        self.db.save(target)
        self.repo.update_entry(list(self.repo.entries)[0], description="v2")
        self.db.save(target)
        self.repo.update_entry(list(self.repo.entries)[0], description="v3")
        self.db.save(target)
        self.assertTrue(os.path.exists(target + ".1"))
        self.assertTrue(os.path.exists(target + ".2"))

    def test_backup_can_be_disabled(self):
        target = self.path("nb.cmdb")
        self.db.save(target)
        self.assertEqual(self.db.save(target, backup=False).backup, None)
        self.assertFalse(os.path.exists(target + ".1"))

    def test_save_as_does_not_rebind_on_failure(self):
        target = self.path("x.cmdb")
        self.db.save(target)
        # 另存到非法路径
        with self.assertRaises(DatabaseError):
            self.db.save(os.path.join(self.tmp, "no-such-dir", "\x00bad.cmdb"))
        self.assertTrue(os.path.exists(target))

    def test_convert_between_formats(self):
        binary = self.path("c.cmdb")
        text = self.path("c.cmj")
        self.db.save(binary)
        result = self.db.convert(text, binary=False)
        self.assertIs(result.kind, fmt.FormatKind.TEXT)
        self.assertIs(Database.detect(text), fmt.FormatKind.TEXT)
        self.assertEqual(len(Database.open(text).repository.entries), 2)
        # 当前绑定路径不变
        self.assertEqual(self.db.path, os.path.abspath(binary))

    def test_extension_overrides_format_hint(self):
        target = self.path("forced.cmj")
        result = self.db.save(target, binary=True)
        self.assertIs(result.kind, fmt.FormatKind.TEXT)

    def test_from_bytes_roundtrip(self):
        blob = self.db.snapshot_to_bytes()
        restored = Database.from_bytes(blob)
        self.assertEqual(len(restored.repository.entries), 2)

    def test_external_modification_detected(self):
        target = self.path("ext.cmdb")
        self.db.save(target)
        self.assertFalse(self.db.is_externally_modified())
        with open(target, "ab") as handle:
            handle.write(b"\x00")
        self.assertTrue(self.db.is_externally_modified())

    def test_merge_from_renames_conflicts_and_keeps_history(self):
        # 用同一份数据制造 id 冲突 (条目/实现/修订 id 全部重合)
        other = Repository.from_dict(self.repo.to_dict())
        before_total = self.db.repository.history.total_count()
        before_entries = len(self.db.repository.entries)

        stats = self.db.merge_from(other.to_dict())

        self.assertEqual(stats["entries"], 2)
        self.assertEqual(stats["renamed"], 2)  # id 完全相同 -> 全部重命名
        self.assertEqual(len(self.db.repository.entries), before_entries + 2)
        self.assertEqual(stats["revisions"], before_total)
        self.assertEqual(
            self.db.repository.history.total_count(), before_total * 2
        )
        # 索引与实际修订数一致 (曾经会因 id 覆盖而静默丢失)
        indexed = sum(
            len(self.db.repository.history.revisions(eid))
            for eid in self.db.repository.history.entry_ids()
        )
        self.assertEqual(indexed, self.db.repository.history.total_count())

    def test_merge_does_not_reuse_implementation_ids(self):
        other = Repository.from_dict(self.repo.to_dict())
        self.db.merge_from(other.to_dict())
        ids = [
            impl.id
            for entry in self.db.repository.entries.values()
            for impl in entry.implementations
        ]
        self.assertEqual(len(ids), len(set(ids)), "实现 id 出现重复")

    def test_merge_from_keeps_distinct_entries(self):
        other = Repository(name="other")
        entry = other.create_entry("独有条目", "", "", ["unique"])
        other.add_implementation(entry.id, "java", "class A {}")
        stats = self.db.merge_from(other.to_dict())
        self.assertEqual(stats["entries"], 1)
        self.assertEqual(stats["renamed"], 0)
        titles = {e.title for e in self.db.repository.entries.values()}
        self.assertIn("独有条目", titles)


class TestExporter(StorageTestCase):
    def test_markdown_contains_entries_and_code(self):
        text = exporter.library_to_markdown(self.repo)
        self.assertIn("# 存储测试", text)
        self.assertIn("TCP 服务器", text)
        self.assertIn("```python", text)
        self.assertIn("```go", text)
        self.assertIn("```cpp", text)
        self.assertIn("## 修订历史", text)
        self.assertIn("#network", text)

    def test_entry_markdown_single(self):
        entry = list(self.repo.entries.values())[0]
        text = exporter.entry_to_markdown(entry, revisions=self.repo.revisions(entry.id))
        self.assertIn("## 前置要求", text)
        self.assertIn("前置", text)

    def test_json_roundtrip(self):
        target = self.path("e.json")
        exporter.export_json(self.repo, target)
        with open(target, "r", encoding="utf-8") as handle:
            data = exporter.read_json_export(handle.read())
        self.assertEqual(len(data["entries"]), 2)

    def test_zip_export_contains_real_source_files(self):
        import zipfile

        target = self.path("e.zip")
        count = exporter.export_zip(self.repo, target)
        self.assertEqual(count, 2)
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
        self.assertIn("codemethod.json", names)
        self.assertIn("README.md", names)
        self.assertTrue(any(n.endswith("main.py") or n.endswith(".py") for n in names))
        self.assertTrue(any(n.endswith(".go") for n in names))
        self.assertTrue(any(n.endswith("entry.json") for n in names))

    def test_zip_roundtrip(self):
        target = self.path("r.zip")
        exporter.export_zip(self.repo, target)
        data = exporter.read_zip_export(target)
        restored = Repository.from_dict(data)
        self.assertEqual(len(restored.entries), 2)
        self.assertEqual(restored.history.total_count(), self.repo.history.total_count())

    def test_zip_without_metadata_rejected(self):
        import zipfile

        target = self.path("bad.zip")
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("hello.txt", "hi")
        with self.assertRaises(ValueError):
            exporter.read_zip_export(target)

    def test_markdown_export_to_file(self):
        target = self.path("lib.md")
        exporter.export_markdown(self.repo, target)
        self.assertGreater(os.path.getsize(target), 100)

    def test_deleted_implementations_listed(self):
        entry = list(self.repo.entries.values())[0]
        impl = entry.active_implementations[0]
        self.repo.delete_implementation(entry.id, impl.id)
        text = exporter.entry_to_markdown(self.repo.require(entry.id))
        self.assertIn("已删除的实现", text)


class TestManifest(StorageTestCase):
    def test_manifest_counts_languages_and_tags(self):
        payload = fmt.pack_repository(self.repo.to_dict())
        manifest = fmt.build_manifest(self.repo.to_dict(), payload)
        self.assertEqual(manifest["stats"]["entries"], 2)
        self.assertIn("python", manifest["languages"])
        self.assertIn("go", manifest["languages"])
        self.assertEqual(manifest["tags"]["network"], 1)
        self.assertEqual(
            manifest["integrity"]["digest"], __import__("hashlib").sha256(payload).hexdigest()
        )


if __name__ == "__main__":
    unittest.main()
