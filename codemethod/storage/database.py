"""数据库门面: 打开 / 保存 / 另存 / 备份 / 格式转换.

:class:`Database` 把"内存中的 :class:`~codemethod.core.repository.Repository`"与
"磁盘上的可移植容器文件"连接起来, 并负责:

* 原子写入 (先写临时文件, 再 ``os.replace``), 断电不会留下半个文件;
* 保存前自动滚动备份 (``xxx.cmdb.1`` ... ``xxx.cmdb.5``);
* 保存后可选的完整校验 (读取回来比对 SHA-256);
* 外部修改检测 (文件被别的程序改动时给出提示)。
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..core.repository import Repository
from . import container as fmt
from .container import CodeMethodFormatError, ContainerInfo, FormatKind


class DatabaseError(Exception):
    """数据库层的可预期错误。"""


@dataclass
class SaveResult:
    """一次保存的结果。"""

    path: str
    kind: FormatKind
    bytes_written: int
    raw_bytes: int
    compressed: bool
    verified: bool
    backup: Optional[str] = None
    elapsed: float = 0.0

    @property
    def ratio(self) -> float:
        if not self.raw_bytes:
            return 1.0
        return self.bytes_written / self.raw_bytes

    def describe(self) -> str:
        parts = [
            f"已保存到 {self.path}",
            f"格式 {self.kind.label}",
            f"大小 {fmt.human_size(self.bytes_written)}",
        ]
        if self.compressed:
            parts.append(f"压缩后/原始 {self.ratio:.1%}")
        if self.backup:
            parts.append(f"备份 {os.path.basename(self.backup)}")
        if self.verified:
            parts.append("校验通过")
        parts.append(f"耗时 {self.elapsed * 1000:.0f} ms")
        return " · ".join(parts)


class Database:
    """一个代码库文件的读写会话。"""

    DEFAULT_BACKUPS = 5

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.repository = repository or Repository()
        self.path: Optional[str] = None
        self.info: Optional[ContainerInfo] = None
        self.backup_count: int = self.DEFAULT_BACKUPS
        self.compress: bool = True
        self.prefer_binary: bool = True
        self.last_saved_at: float = 0.0
        self._saved_mtime: float = 0.0
        self._saved_size: int = 0

    # ==================================================================================
    # 打开 / 新建
    # ==================================================================================
    @classmethod
    def create(cls, name: str = "未命名代码库", description: str = "", author: str = "local") -> "Database":
        """新建一个内存库。"""
        return cls(Repository(name=name, description=description, author=author))

    @classmethod
    def open(cls, path: str, *, verify: bool = True, backup_count: int = DEFAULT_BACKUPS) -> "Database":
        """打开磁盘上的容器文件 (自动识别 ``.cmdb`` / ``.cmj``)。"""
        if not os.path.exists(path):
            raise DatabaseError(f"文件不存在: {path}")
        try:
            data, info = fmt.read_any(path, verify=verify)
        except CodeMethodFormatError as exc:
            raise DatabaseError(str(exc)) from exc
        except OSError as exc:
            raise DatabaseError(f"读取失败: {exc}") from exc

        repository = Repository.from_dict(data)
        db = cls(repository)
        db.path = os.path.abspath(path)
        db.info = info
        db.backup_count = backup_count
        db.compress = info.compressed or info.kind is FormatKind.TEXT
        db.prefer_binary = info.kind is FormatKind.BINARY
        db._remember_stat()
        db.repository.path = db.path
        db.repository.mark_clean()
        return db

    def close(self) -> None:
        """结束当前会话 (不落盘, 由调用方决定是否保存)。"""
        self.path = None
        self.info = None
        self._saved_mtime = 0.0
        self._saved_size = 0

    # ==================================================================================
    # 保存
    # ==================================================================================
    def _target_path(self, path: Optional[str] = None) -> str:
        target = path or self.path
        if not target:
            raise DatabaseError("尚未指定保存路径")
        return os.path.abspath(target)

    def _remember_stat(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        stat = os.stat(self.path)
        self._saved_mtime = stat.st_mtime
        self._saved_size = stat.st_size

    def is_externally_modified(self) -> bool:
        """磁盘文件是否被本程序之外的操作改动过。"""
        if not self.path or not os.path.exists(self.path):
            return False
        stat = os.stat(self.path)
        if not self._saved_mtime:
            return False
        return (
            abs(stat.st_mtime - self._saved_mtime) > 1e-6 or stat.st_size != self._saved_size
        )

    def _make_backup(self, path: str) -> Optional[str]:
        """滚动备份: ``x.cmdb`` -> ``x.cmdb.1`` ... 超出数量则丢弃最旧的。"""
        if not self.backup_count or not os.path.exists(path):
            return None
        try:
            for index in range(self.backup_count, 1, -1):
                older = f"{path}.{index - 1}"
                newer = f"{path}.{index}"
                if os.path.exists(older):
                    if index == self.backup_count and os.path.exists(newer):
                        os.remove(newer)
                    os.replace(older, newer)
            first = f"{path}.1"
            shutil.copy2(path, first)
            return first
        except OSError:
            return None

    def save(
        self,
        path: Optional[str] = None,
        *,
        binary: Optional[bool] = None,
        compress: Optional[bool] = None,
        backup: bool = True,
        verify: bool = True,
    ) -> SaveResult:
        """保存到磁盘。

        ``binary`` 为 None 时沿用当前文件格式 (新文件默认二进制)。
        """
        started = time.perf_counter()
        target = self._target_path(path)
        want_binary = self.prefer_binary if binary is None else bool(binary)
        if os.path.splitext(target)[1].lower() in fmt.TEXT_EXTENSIONS:
            want_binary = False
        elif os.path.splitext(target)[1].lower() in fmt.BINARY_EXTENSIONS:
            want_binary = True
        use_compress = self.compress if compress is None else bool(compress)

        self.repository.modified_at = time.time()
        data = self.repository.to_dict(include_history=True)

        backup_path = self._make_backup(target) if backup else None
        tmp_path = f"{target}.tmp-{os.getpid()}"
        try:
            if want_binary:
                info = fmt.write_container(tmp_path, data, compress=use_compress)
            else:
                info = fmt.write_text(tmp_path, data)
            os.replace(tmp_path, target)
        except (OSError, ValueError) as exc:
            # OSError: 磁盘/权限问题; ValueError: 非法路径 (例如包含 NUL 字符)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise DatabaseError(f"写入失败: {exc}") from exc

        verified = False
        if verify:
            try:
                _, check_info = fmt.read_any(target, verify=True)
                problems = list(check_info.warnings)
                verified = not problems
                info = check_info
            except (CodeMethodFormatError, OSError):
                verified = False

        self.path = target
        self.info = info
        self.prefer_binary = want_binary
        self.compress = use_compress
        self.last_saved_at = time.time()
        self._remember_stat()
        self.repository.path = target
        self.repository.mark_clean()

        return SaveResult(
            path=target,
            kind=info.kind,
            bytes_written=info.file_size,
            raw_bytes=info.payload_raw,
            compressed=info.compressed,
            verified=verified,
            backup=backup_path,
            elapsed=time.perf_counter() - started,
        )

    def save_as(self, path: str, **kwargs: Any) -> SaveResult:
        """另存为新文件 (不写入当前路径)。"""
        return self.save(path, **kwargs)

    def convert(self, path: str, *, binary: bool, compress: bool = True) -> SaveResult:
        """把当前库导出为另一种容器格式 (不改动当前绑定路径)。"""
        original_path = self.path
        original_prefer = self.prefer_binary
        try:
            self.path = os.path.abspath(path)
            self.prefer_binary = binary
            return self.save(path, binary=binary, compress=compress, backup=False)
        finally:
            self.path = original_path
            self.prefer_binary = original_prefer

    # ==================================================================================
    # 导入 / 导出
    # ==================================================================================
    def merge_from(self, other_data: Dict[str, Any], *, prefix_conflicts: bool = True) -> Dict[str, int]:
        """把另一个仓储 dict 的条目并入当前库。

        条目 id / 实现 id / 修订 id 冲突时都会重新编号, 并把对方的完整历史一并并入,
        因此导入的条目同样保留可回滚的修订时间线。

        返回统计: ``{"entries": 新增条目数, "renamed": 重命名数, "revisions": 新增修订数}``。
        """
        from ..core.models import Entry, new_id

        incoming = Repository.from_dict(other_data)
        added = 0
        renamed = 0
        revisions_added = 0
        impl_renamed = 0

        for entry in incoming.entries.values():
            source_id = entry.id
            clone = Entry.from_dict(entry.to_dict())
            if clone.id in self.repository.entries:
                clone.id = new_id("ent_")
                renamed += 1

            # 实现 id 也去冲突, 避免同一实现被两个条目共享
            taken_impl_ids = {
                impl.id
                for existing in self.repository.entries.values()
                for impl in existing.implementations
            }
            for impl in clone.implementations:
                if impl.id in taken_impl_ids:
                    impl.id = new_id("impl_")
                    impl_renamed += 1

            timeline = incoming.history.revisions(source_id)
            revisions_added += self.repository.history.import_timeline(clone.id, timeline)

            self.repository.entries[clone.id] = clone
            self.repository._register_tags(clone.tags)
            added += 1

        if added:
            self.repository._touch()
        return {
            "entries": added,
            "renamed": renamed,
            "revisions": revisions_added,
            "implementations_renamed": impl_renamed,
        }

    # ==================================================================================
    # 信息
    # ==================================================================================
    def probe(self, path: Optional[str] = None) -> ContainerInfo:
        """读取容器元信息 (不加载到内存库)。"""
        return fmt.probe_file(self._target_path(path))

    @staticmethod
    def verify(path: str) -> Tuple[bool, List[str], Optional[ContainerInfo]]:
        """校验任意容器文件。"""
        return fmt.verify_file(path)

    @staticmethod
    def detect(path: str) -> FormatKind:
        return fmt.detect_format(path)

    def snapshot_to_bytes(self, *, binary: bool = True, compress: bool = True) -> bytes:
        """把当前库序列化为字节 (用于剪贴板/网络传输/测试)。"""
        data = self.repository.to_dict()
        if not binary:
            return fmt.pack_repository(data)
        blob, _info = fmt.build_container_bytes(data, compress=compress)
        return blob

    @staticmethod
    def from_bytes(blob: bytes, *, verify: bool = True) -> "Database":
        """从字节流还原一个内存库 (不绑定路径)。"""
        kind = FormatKind.BINARY if blob[:8] == fmt.CONTAINER_MAGIC else FormatKind.TEXT
        if kind is FormatKind.BINARY:
            data, info = fmt.read_container_bytes(blob, verify=verify)
        else:
            data, info = fmt.read_text_string(blob.decode("utf-8"), verify=verify)
        db = Database(Repository.from_dict(data))
        db.info = info
        db.prefer_binary = kind is FormatKind.BINARY
        return db

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return (
            f"<Database path={self.path!r} entries={len(self.repository.entries)} "
            f"dirty={self.repository.dirty}>"
        )


__all__ = ["Database", "DatabaseError", "SaveResult"]
