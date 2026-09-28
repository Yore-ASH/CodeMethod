"""仓储层: 条目/实现/标签的全部可变操作, 并保证每次修改都进入修订历史.

界面层只与本模块交互, 不直接改动 :class:`~codemethod.core.models.Entry` 对象,
从而"任何修改都有历史"成为结构上的保证, 而不是靠调用方自觉。
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .history import (
    HistoryStore,
    build_revision_diff,
    checksum_of,
    kind_of_snapshot,
    summarize_changes,
    take_snapshot,
)
from .languages import DEFAULT_LANGUAGE, get_language, normalize_language
from .functions import Function, FunctionImplementation, Symbol
from .models import (
    ACTION_LABELS,
    STATUS_ORDER,
    Entry,
    Implementation,
    Revision,
    normalize_tag,
    normalize_tags,
    new_id,
    tag_key,
    utcnow,
)
from .spaces import (
    MAX_BINARY_BYTES,
    MAX_FILES_PER_SPACE,
    MAX_FILE_BYTES,
    MAX_SPACE_BINARY_BYTES,
    DirectoryScan,
    ImportOptions,
    ProjectFile,
    Space,
    decode_text,
    human_bytes,
    is_binary_path,
    looks_binary,
    normalize_project_path,
    scan_external_directory,
    search_readmes,
)

SCHEMA_VERSION = 1


class RepositoryError(Exception):
    """仓储层的可预期错误 (界面直接展示给用户)。"""


@dataclass
class ImportReport:
    """一次"导入外部文件"的结果 (供界面给出准确的反馈)。"""

    added: int = 0            # 新增的文件数
    updated: int = 0          # 覆盖已有文件的数量
    binary: int = 0           # 其中作为二进制嵌入的数量
    text: int = 0             # 其中作为文本存进库的数量
    total_bytes: int = 0      # 嵌入/写入的总字节数
    skipped: List[Tuple[str, str]] = field(default_factory=list)   # (路径, 原因)

    @property
    def changed(self) -> int:
        return self.added + self.updated

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"新增 {self.added} 个")
        if self.updated:
            parts.append(f"覆盖 {self.updated} 个")
        if self.binary:
            parts.append(f"其中二进制 {self.binary} 个 ({human_bytes(self.total_bytes)})")
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 个")
        return " · ".join(parts) if parts else "没有导入任何文件"


@dataclass
class TagInfo:
    """标签元数据 (用于标签管理面板: 颜色、说明、使用次数)。"""

    name: str
    color: str = "#569CD6"
    description: str = ""
    created_at: float = field(default_factory=utcnow)

    @property
    def key(self) -> str:
        return tag_key(self.name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "color": self.color,
            "description": self.description,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TagInfo":
        return cls(
            name=normalize_tag(str(data.get("name") or "")),
            color=str(data.get("color") or "#569CD6"),
            description=str(data.get("description") or ""),
            created_at=float(data.get("created_at") or utcnow()),
        )


# 新建标签时循环取用的调色板
TAG_PALETTE: Sequence[str] = (    "#569CD6",
    "#4EC9B0",
    "#DCDCAA",
    "#CE9178",
    "#C586C0",
    "#9CDCFE",
    "#B5CEA8",
    "#D16969",
    "#D7BA7D",
    "#608B4E",
    "#E06C75",
    "#56B6C2",
)


def detect_implementation_symbols(implementation: Any) -> int:
    """检测某个语言实现的代码里的变量声明并并入它的符号表, 返回新增数量。

    检测器在 :mod:`codemethod.core.symbols`, 是纯标准库的启发式实现
    (正则 + 注释/字符串屏蔽); 这里只负责把结果交给
    :meth:`FunctionImplementation.apply_detected`, 由后者保证
    **不覆盖用户已填写的含义**。
    """
    try:
        from .symbols import detect_symbols
    except ImportError:  # pragma: no cover - symbols 模块缺失时退化为不检测
        return 0
    try:
        detected = detect_symbols(
            implementation.language, implementation.code, getattr(implementation, "name", "")
        )
    except Exception:  # pragma: no cover - 检测器保证不抛, 这里再兜一层
        return 0
    return implementation.apply_detected(detected)


def detect_function_symbols(function: Function) -> int:
    """检测函数体**全部语言实现**的变量声明, 返回新增总数。

    兼容旧签名 (以前 Function 只有一种语言)。
    """
    return sum(detect_implementation_symbols(impl) for impl in function.active_implementations)


class Repository:
    """一个 CodeMethod 库 (对应磁盘上的一个 ``.cmdb`` / ``.cmj`` 文件)。"""
    def __init__(
        self,
        name: str = "未命名代码库",
        description: str = "",
        author: str = "local",
        *,
        max_revisions: int = 500,
    ) -> None:
        self.name = name
        self.description = description
        self.author = author
        self.schema_version = SCHEMA_VERSION
        self.created_at = utcnow()
        self.modified_at = self.created_at
        self.entries: Dict[str, Entry] = {}
        self.spaces: Dict[str, Space] = {}
        self.functions: Dict[str, Function] = {}
        self.tags: Dict[str, TagInfo] = {}
        self.history = HistoryStore(max_revisions=max_revisions)
        self.extra: Dict[str, Any] = {}
        self.path: Optional[str] = None
        self.dirty: bool = False
        self._search_cache: Dict[Tuple[str, int, bool], str] = {}
        self._tag_counter: int = 0

    # ==================================================================================
    # 条目
    # ==================================================================================
    def create_entry(
        self,
        title: str,
        description: str = "",
        prerequisites: str = "",
        tags: Iterable[str] = (),
        *,
        status: str = "idea",
        favorite: bool = False,
        implementations: Optional[List[Implementation]] = None,
        entry_id: Optional[str] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Entry:
        entry = Entry(
            id=entry_id or new_id("ent_"),
            title=title.strip() or "未命名条目",
            description=description,
            prerequisites=prerequisites,
            tags=normalize_tags(tags),
            implementations=list(implementations or []),
            status=status if status in STATUS_ORDER else "idea",
            favorite=favorite,
        )
        if entry.id in self.entries:
            raise RepositoryError(f"条目 id 冲突: {entry.id}")
        self.entries[entry.id] = entry
        self._register_tags(entry.tags)
        self.history.record(entry, "create", summary or "新建条目", author or self.author, force=True)
        self._invalidate(entry)
        self._touch()
        return entry

    def update_entry(
        self,
        entry_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        prerequisites: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
        status: Optional[str] = None,
        favorite: Optional[bool] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Entry:
        entry = self.require(entry_id)
        before = take_snapshot(entry)

        if title is not None:
            entry.title = title.strip() or entry.title
        if description is not None:
            entry.description = description
        if prerequisites is not None:
            entry.prerequisites = prerequisites
        if tags is not None:
            entry.tags = normalize_tags(tags)
            self._register_tags(entry.tags)
        if status is not None:
            if status not in STATUS_ORDER:
                raise RepositoryError(f"未知状态: {status}")
            entry.status = status
        if favorite is not None:
            entry.favorite = bool(favorite)

        after = take_snapshot(entry)
        if checksum_of(before) == checksum_of(after):
            return entry

        entry.touch()
        after = take_snapshot(entry)
        self.history.record(
            entry,
            "update",
            summary or summarize_changes(before, after),
            author or self.author,
        )
        self._invalidate(entry)
        self._touch()
        return entry

    def delete_entry(self, entry_id: str, *, hard: bool = False, author: Optional[str] = None) -> None:
        """删除条目。默认软删除 (可恢复), ``hard=True`` 时彻底移除 (历史保留在回收站之外)。"""
        entry = self.require(entry_id)
        if hard:
            snapshot = take_snapshot(entry)
            entry.deleted = True
            entry.touch()
            self.history.record(
                entry, "purge", "彻底删除条目", author or self.author, force=True
            )
            self.entries.pop(entry_id, None)
            self._invalidate(entry)
            self._touch()
            return
        if entry.deleted:
            return
        entry.deleted = True
        entry.touch()
        self.history.record(entry, "delete", "移入回收站", author or self.author, force=True)
        self._invalidate(entry)
        self._touch()

    def restore_entry(self, entry_id: str, *, author: Optional[str] = None) -> Entry:
        entry = self.require(entry_id)
        if not entry.deleted:
            return entry
        entry.deleted = False
        entry.touch()
        self.history.record(
            entry, "restore_delete", "从回收站恢复", author or self.author, force=True
        )
        self._invalidate(entry)
        self._touch()
        return entry

    def toggle_favorite(self, entry_id: str, *, author: Optional[str] = None) -> Entry:
        entry = self.require(entry_id)
        before = take_snapshot(entry)
        entry.favorite = not entry.favorite
        entry.touch()
        after = take_snapshot(entry)
        self.history.record(
            entry,
            "favorite",
            ("收藏" if entry.favorite else "取消收藏") + f" ({summarize_changes(before, after)})",
            author or self.author,
        )
        self._invalidate(entry)
        self._touch()
        return entry

    def duplicate_entry(self, entry_id: str, *, author: Optional[str] = None) -> Entry:
        source = self.require(entry_id)
        clone = Entry.from_dict(source.to_dict())
        clone.id = new_id("ent_")
        clone.title = f"{source.display_title} (副本)"
        clone.created_at = utcnow()
        clone.updated_at = clone.created_at
        clone.version = 1
        clone.deleted = False
        clone.implementations = [impl.clone(new_identity=True) for impl in source.implementations]
        self.entries[clone.id] = clone
        self._register_tags(clone.tags)
        self.history.record(clone, "create", f"复制自《{source.display_title}》", author or self.author, force=True)
        self._invalidate(clone)
        self._touch()
        return clone

    # ==================================================================================
    # 多语言实现
    # ==================================================================================
    def add_implementation(
        self,
        entry_id: str,
        language: str,
        code: str = "",
        *,
        title: str = "",
        filename: str = "",
        notes: str = "",
        prerequisites: str = "",
        impl_id: Optional[str] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Implementation:
        entry = self.require(entry_id)
        before = take_snapshot(entry)
        lang = normalize_language(language)
        impl = Implementation(
            id=impl_id or new_id("impl_"),
            language=lang,
            code=code,
            title=title,
            filename=filename,
            notes=notes,
            prerequisites=prerequisites,
        )
        if any(existing.id == impl.id for existing in entry.implementations):
            raise RepositoryError(f"实现 id 冲突: {impl.id}")
        entry.implementations.append(impl)
        entry.touch()
        after = take_snapshot(entry)
        lang_name = get_language(lang).name
        self.history.record(
            entry,
            "impl_add",
            summary or f"新增 {lang_name} 实现; " + summarize_changes(before, after),
            author or self.author,
            force=True,
        )
        self._invalidate(entry)
        self._touch()
        return impl

    def update_implementation(
        self,
        entry_id: str,
        impl_id: str,
        *,
        language: Optional[str] = None,
        code: Optional[str] = None,
        title: Optional[str] = None,
        filename: Optional[str] = None,
        notes: Optional[str] = None,
        prerequisites: Optional[str] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Implementation:
        entry = self.require(entry_id)
        impl = entry.get_implementation(impl_id)
        if impl is None:
            raise RepositoryError(f"实现不存在: {impl_id}")
        before = take_snapshot(entry)

        if language is not None:
            impl.language = normalize_language(language)
        if code is not None:
            impl.code = code
        if title is not None:
            impl.title = title
        if filename is not None:
            impl.filename = filename
        if notes is not None:
            impl.notes = notes
        if prerequisites is not None:
            impl.prerequisites = prerequisites

        impl.touch()
        entry.touch()
        after = take_snapshot(entry)
        self.history.record(
            entry,
            "impl_update",
            summary or summarize_changes(before, after),
            author or self.author,
        )
        self._invalidate(entry)
        self._touch()
        return impl

    def delete_implementation(
        self, entry_id: str, impl_id: str, *, author: Optional[str] = None
    ) -> None:
        entry = self.require(entry_id)
        impl = entry.get_implementation(impl_id)
        if impl is None:
            raise RepositoryError(f"实现不存在: {impl_id}")
        if impl.deleted:
            return
        before = take_snapshot(entry)
        impl.deleted = True
        impl.touch()
        entry.touch()
        after = take_snapshot(entry)
        lang_name = get_language(impl.language).name
        self.history.record(
            entry,
            "impl_delete",
            f"删除 {lang_name} 实现; " + summarize_changes(before, after),
            author or self.author,
            force=True,
        )
        self._invalidate(entry)
        self._touch()

    def restore_implementation(
        self, entry_id: str, impl_id: str, *, author: Optional[str] = None
    ) -> None:
        entry = self.require(entry_id)
        impl = entry.get_implementation(impl_id)
        if impl is None or not impl.deleted:
            return
        impl.deleted = False
        impl.touch()
        entry.touch()
        lang_name = get_language(impl.language).name
        self.history.record(
            entry, "impl_restore", f"恢复 {lang_name} 实现", author or self.author, force=True
        )
        self._invalidate(entry)
        self._touch()

    def set_entry_implementations(
        self,
        entry_id: str,
        implementations: List[Implementation],
        *,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Entry:
        """整体替换实现列表 (供编辑对话框一次性提交使用)。"""
        entry = self.require(entry_id)
        before = take_snapshot(entry)
        entry.implementations = list(implementations)
        entry.touch()
        after = take_snapshot(entry)
        if checksum_of(before) == checksum_of(after):
            return entry
        self.history.record(
            entry, "impl_update", summary or summarize_changes(before, after), author or self.author
        )
        self._invalidate(entry)
        self._touch()
        return entry

    def apply_entry(
        self,
        entry_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        prerequisites: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
        status: Optional[str] = None,
        favorite: Optional[bool] = None,
        implementations: Optional[List[Implementation]] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Entry:
        """一次性应用编辑对话框的全部改动, 只产生**一条**修订。

        与逐个字段调用 ``update_*`` 相比, 用户点一次"保存"就只对应一条历史记录,
        回滚时语义更清晰。
        """
        entry = self.require(entry_id)
        before = take_snapshot(entry)

        if title is not None and title.strip():
            entry.title = title.strip()
        if description is not None:
            entry.description = description
        if prerequisites is not None:
            entry.prerequisites = prerequisites
        if tags is not None:
            entry.tags = normalize_tags(tags)
            self._register_tags(entry.tags)
        if status is not None:
            if status not in STATUS_ORDER:
                raise RepositoryError(f"未知状态: {status}")
            entry.status = status
        if favorite is not None:
            entry.favorite = bool(favorite)
        if implementations is not None:
            entry.implementations = list(implementations)

        after = take_snapshot(entry)
        if checksum_of(before) == checksum_of(after):
            return entry

        entry.touch()
        after = take_snapshot(entry)

        impl_before = {i.get("id") for i in (before.get("implementations") or [])}
        impl_after = {i.get("id") for i in (after.get("implementations") or [])}
        if impl_after - impl_before:
            action = "impl_add"
        elif impl_before - impl_after:
            action = "impl_delete"
        elif any(
            o.get("code") != n.get("code")
            for o, n in zip(
                sorted(before.get("implementations") or [], key=lambda i: i.get("id") or ""),
                sorted(after.get("implementations") or [], key=lambda i: i.get("id") or ""),
            )
        ):
            action = "impl_update"
        else:
            action = "update"

        self.history.record(
            entry, action, summary or summarize_changes(before, after), author or self.author
        )
        self._invalidate(entry)
        self._touch()
        return entry

    # ==================================================================================
    # 标签
    # ==================================================================================
    def _register_tags(self, tags: Iterable[str]) -> None:
        for tag in tags or ():
            cleaned = normalize_tag(tag)
            if not cleaned:
                continue
            key = tag_key(cleaned)
            if key not in self.tags:
                color = TAG_PALETTE[self._tag_counter % len(TAG_PALETTE)]
                self._tag_counter += 1
                self.tags[key] = TagInfo(name=cleaned, color=color)

    def tag_color(self, tag: str) -> str:
        info = self.tags.get(tag_key(tag))
        return info.color if info else "#808080"

    def set_tag_color(self, tag: str, color: str) -> None:
        key = tag_key(tag)
        info = self.tags.get(key)
        if info is None:
            info = TagInfo(name=normalize_tag(tag), color=color)
            self.tags[key] = info
        else:
            info.color = color
        self._touch()

    def tag_usage(self) -> Dict[str, int]:
        """返回 ``{规范标签键: 使用次数}``, 只统计未删除的对象。

        注意: 必须覆盖**三类实体** (模块 / 空间 / 函数体)。早先这里只遍历
        ``self.entries``, 于是只给空间或函数体打过的标签永远显示"0 次",
        在标签面板里还会被标成斜体 (表示"从未使用") —— 看起来就像统计坏了。
        """
        counts: Dict[str, int] = {}
        for item in self.items(include_deleted=False):
            for tag in getattr(item, "tags", ()) or ():
                key = tag_key(tag)
                counts[key] = counts.get(key, 0) + 1
        return counts

    def tag_usage_by_kind(self) -> Dict[str, Dict[str, int]]:
        """``{标签键: {"module": n, "space": n, "function": n}}``, 供界面显示明细。"""
        out: Dict[str, Dict[str, int]] = {}
        for item in self.items(include_deleted=False):
            kind = getattr(item, "kind", "module")
            for tag in getattr(item, "tags", ()) or ():
                bucket = out.setdefault(tag_key(tag), {})
                bucket[kind] = bucket.get(kind, 0) + 1
        return out

    def all_tags(self, *, include_unused: bool = True, include_deleted: bool = False) -> List[str]:
        """所有标签名, 按使用次数降序、名称升序排列 (三类实体合并统计)。"""
        usage = self.tag_usage()
        names: Dict[str, str] = {}
        for item in self.items(include_deleted=include_deleted):
            for tag in getattr(item, "tags", ()) or ():
                names.setdefault(tag_key(tag), tag)
        if include_unused:
            for key, info in self.tags.items():
                names.setdefault(key, info.name)
        return sorted(names.values(), key=lambda n: (-usage.get(tag_key(n), 0), n.casefold()))

    def rename_tag(self, old: str, new: str, *, author: Optional[str] = None) -> int:
        """重命名标签, 返回受影响的条目数。"""
        old_key = tag_key(old)
        new_name = normalize_tag(new)
        if not new_name:
            raise RepositoryError("标签名不能为空")
        new_key = tag_key(new_name)
        if old_key == new_key:
            return 0

        affected = 0
        for entry in self.entries.values():
            keys = [tag_key(t) for t in entry.tags]
            if old_key not in keys:
                continue
            before = take_snapshot(entry)
            entry.tags = normalize_tags(
                [new_name if tag_key(t) == old_key else t for t in entry.tags]
            )
            entry.touch()
            after = take_snapshot(entry)
            self.history.record(
                entry,
                "tag_change",
                f"标签《{normalize_tag(old)}》→《{new_name}》",
                author or self.author,
                force=True,
            )
            self._invalidate(entry)
            affected += 1

        info = self.tags.pop(old_key, None)
        if info is not None:
            info.name = new_name
            self.tags.setdefault(new_key, info)
        else:
            self._register_tags([new_name])
        if affected:
            self._touch()
        return affected

    def delete_tag(self, tag: str, *, author: Optional[str] = None) -> int:
        """从所有条目移除某标签, 返回受影响的条目数。"""
        key = tag_key(tag)
        affected = 0
        for entry in self.entries.values():
            if key not in [tag_key(t) for t in entry.tags]:
                continue
            before = take_snapshot(entry)
            entry.tags = [t for t in entry.tags if tag_key(t) != key]
            entry.touch()
            after = take_snapshot(entry)
            self.history.record(
                entry,
                "tag_change",
                f"移除标签《{normalize_tag(tag)}》; " + summarize_changes(before, after),
                author or self.author,
                force=True,
            )
            self._invalidate(entry)
            affected += 1
        self.tags.pop(key, None)
        if affected:
            self._touch()
        return affected

    def merge_tags(self, sources: Iterable[str], target: str, *, author: Optional[str] = None) -> int:
        """把多个标签合并为目标标签, 返回受影响条目数。"""
        target_name = normalize_tag(target)
        if not target_name:
            raise RepositoryError("目标标签名不能为空")
        keys = {tag_key(s) for s in sources}
        keys.discard(tag_key(target_name))
        if not keys:
            return 0

        affected = 0
        for entry in self.entries.values():
            entry_keys = {tag_key(t) for t in entry.tags}
            if not (entry_keys & keys):
                continue
            before = take_snapshot(entry)
            merged = [t for t in entry.tags if tag_key(t) not in keys]
            merged.append(target_name)
            entry.tags = normalize_tags(merged)
            entry.touch()
            after = take_snapshot(entry)
            self.history.record(
                entry,
                "tag_change",
                f"标签合并 →《{target_name}》",
                author or self.author,
                force=True,
            )
            self._invalidate(entry)
            affected += 1

        for key in keys:
            self.tags.pop(key, None)
        self._register_tags([target_name])
        if affected:
            self._touch()
        return affected

    # ==================================================================================
    # 统一访问 (模块 / 空间 / 函数体 三类实体共用一套导航与历史)
    # ==================================================================================
    KINDS: Sequence[str] = ("module", "space", "function")

    def bucket(self, kind: str) -> Dict[str, Any]:
        """取得某一类实体的存储字典。"""
        if kind == "space":
            return self.spaces
        if kind == "function":
            return self.functions
        return self.entries

    def items(
        self,
        kind: Any = "all",
        *,
        include_deleted: bool = False,
    ) -> List[Any]:
        """按类别返回实体列表 (顺序: 模块 → 空间 → 函数体)。

        ``kind`` 既可以是字符串 (``"all"`` / ``"module"`` / ``"space"`` / ``"function"``),
        也可以是类别序列 —— 后者供 :class:`~codemethod.core.query.QuerySpec` 使用。
        """
        if kind in ("all", "", None):
            kinds: Sequence[str] = self.KINDS
        elif isinstance(kind, str):
            kinds = (kind,)
        else:
            selected = [k for k in kind if k in self.KINDS]
            kinds = tuple(selected) if selected else self.KINDS

        out: List[Any] = []
        for name in kinds:
            for entity in self.bucket(name).values():
                if include_deleted or not entity.deleted:
                    out.append(entity)
        return out

    def get_item(self, item_id: str) -> Optional[Any]:
        """跨三类查找 (id 前缀已经能区分, 但这里不依赖前缀)。"""
        for name in self.KINDS:
            found = self.bucket(name).get(item_id)
            if found is not None:
                return found
        return None

    def kind_of(self, item_id: str) -> str:
        """返回实体类别; 不存在时按历史记录推断, 再退化为 ``module``。"""
        for name in self.KINDS:
            if item_id in self.bucket(name):
                return name
        for rev in reversed(self.history.revisions(item_id)):
            if rev.kind:
                return rev.kind
        return "module"

    def require_any(self, item_id: str) -> Any:
        entity = self.get_item(item_id)
        if entity is None:
            raise RepositoryError(f"对象不存在: {item_id}")
        return entity

    def _register_entity_tags(self, entity: Any) -> None:
        self._register_tags(getattr(entity, "tags", ()) or ())

    def _entity_counts(self) -> Dict[str, int]:
        return {
            name: sum(1 for e in self.bucket(name).values() if not e.deleted)
            for name in self.KINDS
        }

    # ==================================================================================
    # 独立空间 (Space)
    # ==================================================================================
    def create_space(
        self,
        name: str,
        description: str = "",
        prerequisites: str = "",
        tags: Iterable[str] = (),
        *,
        status: str = "planned",
        favorite: bool = False,
        files: Optional[List[ProjectFile]] = None,
        entry_point: str = "",
        space_id: Optional[str] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Space:
        space = Space(
            id=space_id or new_id("spc_"),
            name=name.strip() or "未命名空间",
            description=description,
            prerequisites=prerequisites,
            tags=normalize_tags(tags),
            files=list(files or []),
            status=status if status in STATUS_ORDER else "planned",
            favorite=favorite,
            entry_point=entry_point,
        )
        if space.id in self.spaces:
            raise RepositoryError(f"空间 id 冲突: {space.id}")
        self.spaces[space.id] = space
        self._register_tags(space.tags)
        self.history.record(
            space, "create", summary or "新建空间", author or self.author, force=True
        )
        self._touch()
        return space

    def update_space(
        self,
        space_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        prerequisites: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
        status: Optional[str] = None,
        favorite: Optional[bool] = None,
        entry_point: Optional[str] = None,
        files: Optional[List[ProjectFile]] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Space:
        space = self.require_space(space_id)
        before = take_snapshot(space)

        if name is not None and name.strip():
            space.name = name.strip()
        if description is not None:
            space.description = description
        if prerequisites is not None:
            space.prerequisites = prerequisites
        if tags is not None:
            space.tags = normalize_tags(tags)
            self._register_tags(space.tags)
        if status is not None:
            if status not in STATUS_ORDER:
                raise RepositoryError(f"未知状态: {status}")
            space.status = status
        if favorite is not None:
            space.favorite = bool(favorite)
        if entry_point is not None:
            space.entry_point = normalize_project_path(entry_point)
        if files is not None:
            space.files = list(files)

        after = take_snapshot(space)
        if checksum_of(before) == checksum_of(after):
            return space
        space.touch()
        after = take_snapshot(space)
        self.history.record(
            space, "update", summary or summarize_changes(before, after), author or self.author
        )
        self._touch()
        return space

    def require_space(self, space_id: str) -> Space:
        space = self.spaces.get(space_id)
        if space is None:
            raise RepositoryError(f"空间不存在: {space_id}")
        return space

    def delete_space(self, space_id: str, *, hard: bool = False, author: Optional[str] = None) -> None:
        space = self.require_space(space_id)
        if hard:
            space.deleted = True
            space.touch()
            self.history.record(space, "purge", "彻底删除空间", author or self.author, force=True)
            self.spaces.pop(space_id, None)
            self._touch()
            return
        if space.deleted:
            return
        space.deleted = True
        space.touch()
        self.history.record(space, "delete", "移入回收站", author or self.author, force=True)
        self._touch()

    def restore_space(self, space_id: str, *, author: Optional[str] = None) -> Space:
        space = self.require_space(space_id)
        if not space.deleted:
            return space
        space.deleted = False
        space.touch()
        self.history.record(
            space, "restore_delete", "从回收站恢复", author or self.author, force=True
        )
        self._touch()
        return space

    # ---- 空间内的文件 ----
    def _space_file_change(
        self,
        space: Space,
        action: str,
        summary: str,
        *,
        author: Optional[str] = None,
    ) -> Space:
        space.touch()
        self.history.record(space, action, summary, author or self.author, force=True)
        self._touch()
        return space

    def put_space_file(
        self,
        space_id: str,
        path: str,
        content: str = "",
        *,
        note: str = "",
        binary: bool = False,
        size: int = 0,
        binary_data: Optional[bytes] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> ProjectFile:
        """新增或覆盖空间里的一个文件。

        * 文本内容超过 :data:`MAX_FILE_BYTES` 会被拒绝;
        * 传 ``binary_data`` 时把**原始字节直接嵌进库里** (base64 存进 ``data``),
          上限 :data:`MAX_BINARY_BYTES`, 且整个空间最多嵌入
          :data:`MAX_SPACE_BINARY_BYTES`;
        * 只传 ``binary=True`` / ``size`` 时保持旧行为: 仅记录大小, 不保存内容。
        """
        space = self.require_space(space_id)
        normalized = normalize_project_path(path)
        if not normalized:
            raise RepositoryError("文件路径不能为空")

        is_binary = binary or is_binary_path(normalized) or binary_data is not None
        if binary_data is not None:
            payload = bytes(binary_data)
            if len(payload) > MAX_BINARY_BYTES:
                raise RepositoryError(
                    f"{normalized} 有 {human_bytes(len(payload))}, 超过单个二进制文件的上限 "
                    f"{human_bytes(MAX_BINARY_BYTES)}"
                )
            if not is_binary_path(normalized):
                # 扩展名不像二进制, 但内容也不像文本 —— 仍然按二进制嵌进去
                pass
            left = space.binary_budget_left(exclude_path=normalized)
            if len(payload) > left:
                raise RepositoryError(
                    f"空间里已嵌入的二进制接近上限: 这个文件需要 "
                    f"{human_bytes(len(payload))}, 但只剩 {human_bytes(left)} "
                    f"(单空间上限 {human_bytes(MAX_SPACE_BINARY_BYTES)})"
                )
            file = ProjectFile(path=normalized, binary=True, note=note)
            file.set_bytes(payload)
        elif is_binary:
            file = ProjectFile(
                path=normalized, content="", binary=True, size=max(0, int(size)), note=note
            )
        else:
            if len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise RepositoryError(
                    f"文件过大 ({len(content.encode('utf-8'))} 字节), 上限 {MAX_FILE_BYTES} 字节"
                )
            file = ProjectFile(path=normalized, content=content, note=note)

        existing = space.get_file(normalized)
        if existing is None and len(space.files) >= MAX_FILES_PER_SPACE:
            raise RepositoryError(f"单个空间的文件数上限为 {MAX_FILES_PER_SPACE}")

        old_size = existing.computed_size if existing is not None else 0
        space.put_file(file)
        action = "file_add" if existing is None else "file_update"
        verb = "新增" if existing is None else "修改"
        detail = summary or (
            f"{verb}文件 {normalized}"
            + ("" if existing is None else f" ({old_size} → {file.computed_size} 字节)")
            + ("（二进制, 已嵌入库内）" if file.binary and file.data else "")
        )
        self._space_file_change(space, action, detail, author=author)
        return file

    # ---- 导入外部文件 (内容直接嵌入 .cmdb, 不是引用) ----
    def import_external_files(
        self,
        space_id: str,
        paths: Iterable[str],
        *,
        base_dir: str = "",
        target_dir: str = "",
        author: Optional[str] = None,
    ) -> ImportReport:
        """把一个或多个**外部文件**读进空间。

        * 二进制文件 (按扩展名或内容判断) 的原始字节会被 **base64 编码后写进容器** ——
          拷走一个 ``.cmdb`` 就能把它们完整还原, 不依赖原路径;
        * 文本文件按 UTF-8 读入 ``content``;
        * ``base_dir`` 给出时, 用相对于它的路径作为库内路径, 从而保留子目录结构;
        * ``target_dir`` 给出时, 全部文件落到这个库内目录下;
        * 整批导入只产生**一条**修订。
        """
        space = self.require_space(space_id)
        report = ImportReport()
        created: List[ProjectFile] = []
        before = take_snapshot(space)
        remaining_budget = space.binary_budget_left()
        # 已选路径先占位, 免得同一批里两个文件把预算算重
        reserved: Dict[str, int] = {}

        for raw_path in paths:
            source = os.path.abspath(str(raw_path))
            if not os.path.isfile(source):
                report.skipped.append((str(raw_path), "不是文件或不存在"))
                continue

            relative = self._relative_import_path(source, base_dir)
            if target_dir:
                relative = f"{normalize_project_path(target_dir)}/{relative}"
            normalized = normalize_project_path(relative)
            if not normalized:
                report.skipped.append((str(raw_path), "路径无效"))
                continue

            existing = space.get_file(normalized)
            if existing is None and len(space.files) + len(created) >= MAX_FILES_PER_SPACE:
                report.skipped.append((normalized, f"超过单空间 {MAX_FILES_PER_SPACE} 个文件的上限"))
                continue

            try:
                with open(source, "rb") as handle:
                    raw = handle.read()
            except OSError as exc:
                report.skipped.append((normalized, f"读取失败: {exc.strerror or exc}"))
                continue

            by_extension = is_binary_path(normalized)
            binary = by_extension or looks_binary(raw)

            if binary:
                if len(raw) > MAX_BINARY_BYTES:
                    report.skipped.append(
                        (normalized, f"{human_bytes(len(raw))} 超过单文件上限 {human_bytes(MAX_BINARY_BYTES)}")
                    )
                    continue
                budget = remaining_budget - reserved.get(normalized, 0)
                if len(raw) > budget:
                    report.skipped.append(
                        (normalized, f"空间二进制余量不足 (还需 {human_bytes(len(raw))}, 只剩 {human_bytes(budget)})")
                    )
                    continue
                reserved[normalized] = reserved.get(normalized, 0) + len(raw)
                file = ProjectFile(path=normalized, binary=True)
                file.set_bytes(raw)
            else:
                body = decode_text(raw)
                if body is None:      # pragma: no cover - looks_binary 已拦下
                    report.skipped.append((normalized, "既不是合法 UTF-8 也不是二进制"))
                    continue
                if len(raw) > MAX_FILE_BYTES:
                    report.skipped.append(
                        (normalized, f"{human_bytes(len(raw))} 超过单文件上限 {human_bytes(MAX_FILE_BYTES)}")
                    )
                    continue
                file = ProjectFile(path=normalized, content=body)

            if existing is not None:
                file.note = existing.note
                report.updated += 1
            else:
                report.added += 1
            report.total_bytes += file.computed_size
            if binary:
                report.binary += 1
            else:
                report.text += 1
            created.append(file)

        for file in created:
            space.put_file(file)
        if created:
            detail = f"导入 {len(created)} 个外部文件 ({report.summary()})"
            if report.updated:
                # 覆盖已有文件时把"哪个文件的内容变了"也带上 —— 尤其是二进制,
                # 用户不可能从 base64 里看出差别, 只能靠这句话。
                changes = summarize_changes(before, take_snapshot(space))
                if changes and changes != "无实质变更":
                    detail += f"; {changes}"
            self._space_file_change(space, "file_add", detail, author=author)
        return report

    @staticmethod
    def _relative_import_path(source: str, base_dir: str) -> str:
        """决定外部文件在空间里的相对路径。"""
        name = os.path.basename(source)
        if not base_dir:
            return name
        try:
            relative = os.path.relpath(source, os.path.abspath(base_dir))
        except ValueError:      # pragma: no cover - 跨盘符
            return name
        if relative.startswith(".."):
            return name
        return relative.replace(os.sep, "/")

    def import_external_directory(
        self,
        space_id: str,
        directory: str,
        *,
        recursive: bool = True,
        target_dir: str = "",
        options: Optional[ImportOptions] = None,
        force: bool = False,
        author: Optional[str] = None,
    ) -> ImportReport:
        """把一整个外部目录 (含子目录) 打包进空间。

        默认跳过版本控制目录 (``.git`` 等) 与缓存目录 (``__pycache__`` /
        ``node_modules`` / ``.venv`` …) —— 想连这些一起打包就传 ``options``,
        把 ``include_vcs`` / ``include_caches`` 打开。
        """
        opts = options or ImportOptions(recursive=recursive, target_dir=target_dir)
        scan = scan_external_directory(directory, opts)
        if scan.error:
            raise RepositoryError(scan.error)
        return self.import_scan(space_id, scan, force=force, author=author)

    def import_scan(
        self,
        space_id: str,
        scan: DirectoryScan,
        *,
        force: bool = False,
        author: Optional[str] = None,
    ) -> ImportReport:
        """按一份已经算好的计划 (见 :func:`scan_external_directory`) 执行导入。

        界面先用同一个扫描结果做预览, 用户确认后走这里, 因此**不会重复扫描**。
        ``force=True`` 时允许超出空间的二进制软上限 (界面会在确认框里说清代价)。
        """
        space = self.require_space(space_id)
        report = ImportReport()
        planned = scan.included

        total_binary = sum(item.size for item in planned if item.binary)
        left = space.binary_budget_left()
        if total_binary > left and not force:
            raise RepositoryError(
                f"这次要嵌入 {human_bytes(total_binary)} 的二进制内容, 但空间只剩 "
                f"{human_bytes(left)} (单空间软上限 {human_bytes(MAX_SPACE_BINARY_BYTES)})。\n"
                "可以少选一些文件, 或在导入对话框里勾上「仍然导入」。"
            )

        existing_paths = {item.path for item in space.files}
        created: List[ProjectFile] = []
        before = take_snapshot(space)

        for item in planned:
            if item.path not in existing_paths and len(space.files) + len(created) >= MAX_FILES_PER_SPACE:
                report.skipped.append(
                    (item.path, f"超过单空间 {MAX_FILES_PER_SPACE} 个文件的上限")
                )
                continue
            try:
                with open(item.source, "rb") as handle:
                    raw = handle.read()
            except OSError as exc:
                report.skipped.append((item.path, f"读取失败: {exc.strerror or exc}"))
                continue

            if item.binary:
                file = ProjectFile(path=item.path, binary=True)
                file.set_bytes(raw)
            else:
                body = decode_text(raw)
                if body is None:
                    # 扫描时按文本计划的, 真读进来却解不开 —— 退化为二进制嵌入,
                    # 而不是把文件丢掉 (宁可多存也不丢内容)
                    file = ProjectFile(path=item.path, binary=True)
                    file.set_bytes(raw)
                else:
                    file = ProjectFile(path=item.path, content=body)

            existing = space.get_file(item.path)
            if existing is not None:
                file.note = existing.note
                report.updated += 1
            else:
                existing_paths.add(item.path)
                report.added += 1
            report.total_bytes += file.computed_size
            if file.binary:
                report.binary += 1
            else:
                report.text += 1
            created.append(file)

        for item in scan.skipped:
            report.skipped.append((item.path, item.reason))

        for file in created:
            space.put_file(file)
        if created:
            detail = (
                f"从 {os.path.basename(scan.root.rstrip(os.sep))}/ 打包导入 "
                f"{len(created)} 个文件 ({report.summary()})"
            )
            if scan.skipped:
                detail += f"; 跳过 {len(scan.skipped)} 个"
            if force and total_binary > left:
                detail += "（已超出空间软上限, 手动确认）"
            self._space_file_change(space, "file_add", detail, author=author)
        return report

    def scan_directory(
        self, directory: str, options: Optional[ImportOptions] = None
    ) -> DirectoryScan:
        """扫描一个待导入目录 (给界面做预览用, 不碰仓库)。"""
        return scan_external_directory(directory, options)

    def export_space_file(
        self, space_id: str, path: str, target: str
    ) -> int:
        """把空间里的一个文件写到磁盘, 返回字节数。"""
        space = self.require_space(space_id)
        file = space.get_file(path)
        if file is None:
            raise RepositoryError(f"文件不存在: {path}")
        if file.binary and not file.data:
            raise RepositoryError(
                f"{path} 只有大小记录、没有内容 (旧版本保存的二进制文件), 无法导出"
            )
        try:
            return file.write_to(target)
        except OSError as exc:
            raise RepositoryError(f"写入失败: {exc.strerror or exc}") from exc

    def rename_space_file(
        self,
        space_id: str,
        old_path: str,
        new_path: str,
        *,
        author: Optional[str] = None,
    ) -> ProjectFile:
        space = self.require_space(space_id)
        source = normalize_project_path(old_path)
        target = normalize_project_path(new_path)
        if not target:
            raise RepositoryError("新路径不能为空")
        file = space.get_file(source)
        if file is None:
            raise RepositoryError(f"文件不存在: {source}")
        if space.has_path(target):
            raise RepositoryError(f"目标路径已存在: {target}")

        space.remove_file(source)
        file.path = target
        if not file.binary:
            file.language = ProjectFile(path=target).language
        file.touch()
        space.put_file(file)
        self._space_file_change(
            space, "file_rename", f"重命名 {source} → {target}", author=author
        )
        return file

    def delete_space_file(
        self, space_id: str, path: str, *, author: Optional[str] = None
    ) -> bool:
        space = self.require_space(space_id)
        normalized = normalize_project_path(path)
        if not space.remove_file(normalized):
            return False
        self._space_file_change(space, "file_delete", f"删除文件 {normalized}", author=author)
        return True

    def delete_space_directory(
        self, space_id: str, path: str, *, author: Optional[str] = None
    ) -> int:
        space = self.require_space(space_id)
        normalized = normalize_project_path(path)
        removed = space.remove_directory(normalized)
        if removed:
            self._space_file_change(
                space, "file_delete", f"删除目录 {normalized}/ (共 {removed} 个文件)", author=author
            )
        return removed

    def import_space_files(
        self,
        space_id: str,
        files: Iterable[Tuple[str, Any]],
        *,
        author: Optional[str] = None,
        summary: str = "",
    ) -> int:
        """批量导入 ``(相对路径, 内容)``, 只产生**一条**修订。

        内容可以是 ``str`` (文本) 或 ``bytes`` (二进制, 会被嵌入库内)。
        """
        space = self.require_space(space_id)
        count = 0
        for path, content in files:
            normalized = normalize_project_path(path)
            if not normalized:
                continue
            try:
                if isinstance(content, (bytes, bytearray)):
                    payload = bytes(content)
                    if len(payload) > MAX_BINARY_BYTES:
                        continue
                    if len(payload) > space.binary_budget_left(exclude_path=normalized):
                        continue
                    file = ProjectFile(path=normalized, binary=True)
                    file.set_bytes(payload)
                else:
                    if is_binary_path(normalized):
                        continue
                    file = ProjectFile(path=normalized, content=str(content))
                space.put_file(file)
                count += 1
            except Exception:  # pragma: no cover - 单条失败不影响其余
                continue
        if count:
            self._space_file_change(
                space, "file_add", summary or f"批量导入 {count} 个文件", author=author
            )
        return count

    def space_readme_hits(self, query: str, *, context_lines: int = 2) -> List[Any]:
        """在全部空间的 README 里检索。"""
        return search_readmes(self.spaces.values(), query, context_lines=context_lines)

    # ==================================================================================
    # 函数体 (Function)
    # ==================================================================================
    def create_function(
        self,
        name: str,
        language: str = DEFAULT_LANGUAGE,
        code: str = "",
        *,
        signature: str = "",
        description: str = "",
        prerequisites: str = "",
        implementation_prerequisites: str = "",
        symbols: Optional[List[Symbol]] = None,
        notes: str = "",
        implementations: Optional[List[FunctionImplementation]] = None,
        tags: Iterable[str] = (),
        status: str = "planned",
        favorite: bool = False,
        function_id: Optional[str] = None,
        detect: bool = True,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Function:
        """新建函数体。

        ``prerequisites`` 是所有语言共用的前置要求;
        ``implementation_prerequisites`` 是**这一种语言**自己的 (旧接口沿用)。
        也可以直接传 ``implementations`` 一次性给出多语言实现。
        """
        function = Function(
            id=function_id or new_id("fn_"),
            name=name.strip() or "未命名函数",
            description=description,
            prerequisites=prerequisites,
            implementations=list(implementations or []),
            tags=normalize_tags(tags),
            status=status if status in STATUS_ORDER else "planned",
            favorite=favorite,
        )
        if not function.implementations:
            function.implementations.append(
                FunctionImplementation(
                    language=normalize_language(language),
                    signature=signature,
                    code=code,
                    notes=notes,
                    prerequisites=implementation_prerequisites,
                    symbols=list(symbols or []),
                )
            )
        if function.id in self.functions:
            raise RepositoryError(f"函数体 id 冲突: {function.id}")

        if detect:
            for impl in function.active_implementations:
                if not impl.symbols:
                    detect_implementation_symbols(impl)

        self.functions[function.id] = function
        self._register_tags(function.tags)
        detail = summary or (
            f"新建函数体; {len(function.active_implementations)} 种语言, "
            f"{function.meanings_summary}"
        )
        self.history.record(function, "create", detail, author or self.author, force=True)
        self._touch()
        return function

    def update_function(
        self,
        function_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        prerequisites: Optional[str] = None,
        implementations: Optional[List[FunctionImplementation]] = None,
        tags: Optional[Iterable[str]] = None,
        status: Optional[str] = None,
        favorite: Optional[bool] = None,
        # ---- 旧接口: 直接改主实现的字段, 仍然支持 ----
        language: Optional[str] = None,
        code: Optional[str] = None,
        signature: Optional[str] = None,
        symbols: Optional[List[Symbol]] = None,
        notes: Optional[str] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> Function:
        """一次性应用函数体的全部改动, 只产生一条修订。"""
        function = self.require_function(function_id)
        before = take_snapshot(function)

        if name is not None and name.strip():
            function.name = name.strip()
        if description is not None:
            function.description = description
        if prerequisites is not None:
            function.prerequisites = prerequisites
        if implementations is not None:
            function.implementations = list(implementations)

        primary = function.active_implementations[0] if function.active_implementations else None
        if (language is not None or code is not None or signature is not None
                or symbols is not None or notes is not None):
            if primary is None:
                primary = FunctionImplementation()
                function.implementations.append(primary)
            if language is not None:
                primary.language = normalize_language(language)
            if code is not None:
                primary.code = code
            if signature is not None:
                primary.signature = signature
            if notes is not None:
                primary.notes = notes
            if symbols is not None:
                primary.set_symbols(symbols)

        if tags is not None:
            function.tags = normalize_tags(tags)
            self._register_tags(function.tags)
        if status is not None:
            if status not in STATUS_ORDER:
                raise RepositoryError(f"未知状态: {status}")
            function.status = status
        if favorite is not None:
            function.favorite = bool(favorite)

        after = take_snapshot(function)
        if checksum_of(before) == checksum_of(after):
            return function
        function.touch()
        after = take_snapshot(function)
        self.history.record(
            function,
            "update",
            summary or summarize_changes(before, after),
            author or self.author,
        )
        self._touch()
        return function

    # ---- 多语言实现 ----
    def add_function_implementation(
        self,
        function_id: str,
        language: str,
        code: str = "",
        *,
        signature: str = "",
        notes: str = "",
        prerequisites: str = "",
        symbols: Optional[List[Symbol]] = None,
        implementation_id: Optional[str] = None,
        detect: bool = True,
        author: Optional[str] = None,
        summary: str = "",
    ) -> FunctionImplementation:
        function = self.require_function(function_id)
        before = take_snapshot(function)
        impl = FunctionImplementation(
            id=implementation_id or new_id("fimpl_"),
            language=normalize_language(language),
            signature=signature,
            code=code,
            notes=notes,
            prerequisites=prerequisites,
            symbols=list(symbols or []),
        )
        if any(existing.id == impl.id for existing in function.implementations):
            raise RepositoryError(f"实现 id 冲突: {impl.id}")
        if detect and not impl.symbols:
            detect_implementation_symbols(impl)
        function.implementations.append(impl)
        function.touch()
        after = take_snapshot(function)
        self.history.record(
            function,
            "impl_add",
            summary or f"新增 {impl.language_name} 实现; " + summarize_changes(before, after),
            author or self.author,
            force=True,
        )
        self._touch()
        return impl

    def update_function_implementation(
        self,
        function_id: str,
        implementation_id: str,
        *,
        language: Optional[str] = None,
        code: Optional[str] = None,
        signature: Optional[str] = None,
        notes: Optional[str] = None,
        prerequisites: Optional[str] = None,
        symbols: Optional[List[Symbol]] = None,
        author: Optional[str] = None,
        summary: str = "",
    ) -> FunctionImplementation:
        function = self.require_function(function_id)
        impl = function.get_implementation(implementation_id)
        if impl is None:
            raise RepositoryError(f"实现不存在: {implementation_id}")
        before = take_snapshot(function)

        if language is not None:
            impl.language = normalize_language(language)
        if code is not None:
            impl.code = code
        if signature is not None:
            impl.signature = signature
        if notes is not None:
            impl.notes = notes
        if prerequisites is not None:
            impl.prerequisites = prerequisites
        if symbols is not None:
            impl.set_symbols(symbols)

        impl.touch()
        function.touch()
        after = take_snapshot(function)
        if checksum_of(before) == checksum_of(after):
            return impl
        self.history.record(
            function,
            "impl_update",
            summary or summarize_changes(before, after),
            author or self.author,
        )
        self._touch()
        return impl

    def delete_function_implementation(
        self, function_id: str, implementation_id: str, *, author: Optional[str] = None
    ) -> None:
        function = self.require_function(function_id)
        impl = function.get_implementation(implementation_id)
        if impl is None:
            raise RepositoryError(f"实现不存在: {implementation_id}")
        if impl.deleted:
            return
        before = take_snapshot(function)
        impl.deleted = True
        impl.touch()
        function.touch()
        after = take_snapshot(function)
        self.history.record(
            function,
            "impl_delete",
            f"删除 {impl.language_name} 实现; " + summarize_changes(before, after),
            author or self.author,
            force=True,
        )
        self._touch()

    def restore_function_implementation(
        self, function_id: str, implementation_id: str, *, author: Optional[str] = None
    ) -> None:
        function = self.require_function(function_id)
        impl = function.get_implementation(implementation_id)
        if impl is None or not impl.deleted:
            return
        impl.deleted = False
        impl.touch()
        function.touch()
        self.history.record(
            function, "impl_restore", f"恢复 {impl.language_name} 实现",
            author or self.author, force=True,
        )
        self._touch()

    def require_function(self, function_id: str) -> Function:
        function = self.functions.get(function_id)
        if function is None:
            raise RepositoryError(f"函数体不存在: {function_id}")
        return function

    def detect_function_symbols(
        self,
        function_id: str,
        implementation_id: str = "",
        *,
        author: Optional[str] = None,
        record: bool = True,
    ) -> Tuple[int, Function]:
        """重新检测变量并并入符号表, 返回 ``(新增数量, 函数体)``。

        只传 ``function_id`` 时检测**全部语言实现**; 指定 ``implementation_id`` 则只检测那一个。
        **用户已经填写的含义永远不会被覆盖** —— 见 :meth:`FunctionImplementation.apply_detected`。
        """
        function = self.require_function(function_id)
        targets = (
            [function.get_implementation(implementation_id)]
            if implementation_id
            else function.active_implementations
        )
        targets = [impl for impl in targets if impl is not None]
        if not targets:
            return 0, function

        before = take_snapshot(function)
        added = sum(detect_implementation_symbols(impl) for impl in targets)
        after = take_snapshot(function)
        if record and checksum_of(before) != checksum_of(after):
            function.touch()
            after = take_snapshot(function)
            scope = targets[0].language_name if len(targets) == 1 else f"{len(targets)} 种语言"
            self.history.record(
                function,
                "symbols_detect",
                f"自动检测变量 ({scope}): 新增 {added} 个; " + summarize_changes(before, after),
                author or self.author,
                force=True,
            )
            self._touch()
        return added, function

    def set_function_symbols(
        self,
        function_id: str,
        symbols: List[Symbol],
        *,
        implementation_id: str = "",
        author: Optional[str] = None,
        summary: str = "",
    ) -> Function:
        """整体替换某个语言实现的变量含义表 (不传实现 id 则改主实现)。"""
        function = self.require_function(function_id)
        target = (
            function.get_implementation(implementation_id)
            if implementation_id
            else (function.active_implementations[0] if function.active_implementations else None)
        )
        if target is None:
            return function
        self.update_function_implementation(
            function_id, target.id, symbols=symbols, author=author, summary=summary
        )
        return function

    def delete_function(
        self, function_id: str, *, hard: bool = False, author: Optional[str] = None
    ) -> None:
        function = self.require_function(function_id)
        if hard:
            function.deleted = True
            function.touch()
            self.history.record(
                function, "purge", "彻底删除函数体", author or self.author, force=True
            )
            self.functions.pop(function_id, None)
            self._touch()
            return
        if function.deleted:
            return
        function.deleted = True
        function.touch()
        self.history.record(function, "delete", "移入回收站", author or self.author, force=True)
        self._touch()

    def restore_function(self, function_id: str, *, author: Optional[str] = None) -> Function:
        function = self.require_function(function_id)
        if not function.deleted:
            return function
        function.deleted = False
        function.touch()
        self.history.record(
            function, "restore_delete", "从回收站恢复", author or self.author, force=True
        )
        self._touch()
        return function

    # ==================================================================================
    # 通用操作 (按 id 自动分派到模块 / 空间 / 函数体)
    # ==================================================================================
    #
    # 界面只跟这组方法打交道。以前界面直接调 delete_entry/update_entry, 于是对空间与
    # 函数体沉默失效 —— 这类"忘了分派"的 bug 现在由这一层兜住。
    def set_item_status(self, item_id: str, status: str, *, author: Optional[str] = None) -> Any:
        if status not in STATUS_ORDER:
            raise RepositoryError(f"未知状态: {status}")
        kind = self.kind_of(item_id)
        if kind == "space":
            return self.update_space(item_id, status=status, author=author)
        if kind == "function":
            return self.update_function(item_id, status=status, author=author)
        return self.update_entry(item_id, status=status, author=author)

    def set_item_tags(
        self, item_id: str, tags: Iterable[str], *, author: Optional[str] = None
    ) -> Any:
        kind = self.kind_of(item_id)
        if kind == "space":
            return self.update_space(item_id, tags=tags, author=author)
        if kind == "function":
            return self.update_function(item_id, tags=tags, author=author)
        return self.update_entry(item_id, tags=tags, author=author)

    def toggle_favorite_item(self, item_id: str, *, author: Optional[str] = None) -> Any:
        """切换收藏并写入历史。空间与函数体也走这里。"""
        entity = self.require_any(item_id)
        before = take_snapshot(entity)
        entity.favorite = not entity.favorite
        entity.touch()
        after = take_snapshot(entity)
        self.history.record(
            entity,
            "favorite",
            ("收藏" if entity.favorite else "取消收藏") + f" ({summarize_changes(before, after)})",
            author or self.author,
            force=True,
        )
        self._invalidate(entity)
        self._touch()
        return entity

    def duplicate_item(self, item_id: str, *, author: Optional[str] = None) -> Any:
        kind = self.kind_of(item_id)
        if kind == "space":
            return self.duplicate_space(item_id, author=author)
        if kind == "function":
            return self.duplicate_function(item_id, author=author)
        return self.duplicate_entry(item_id, author=author)

    def duplicate_space(self, space_id: str, *, author: Optional[str] = None) -> Space:
        source = self.require_space(space_id)
        clone = Space.from_dict(source.to_dict())
        clone.id = new_id("spc_")
        clone.name = f"{source.display_title} (副本)"
        clone.created_at = utcnow()
        clone.updated_at = clone.created_at
        clone.version = 1
        clone.deleted = False
        self.spaces[clone.id] = clone
        self._register_tags(clone.tags)
        self.history.record(
            clone, "create", f"复制自空间《{source.display_title}》", author or self.author,
            force=True,
        )
        self._touch()
        return clone

    def duplicate_function(self, function_id: str, *, author: Optional[str] = None) -> Function:
        source = self.require_function(function_id)
        clone = Function.from_dict(source.to_dict())
        clone.id = new_id("fn_")
        clone.name = f"{source.display_title} (副本)"
        clone.created_at = utcnow()
        clone.updated_at = clone.created_at
        clone.version = 1
        clone.deleted = False
        clone.implementations = [
            impl.clone(new_identity=True) for impl in source.implementations
        ]
        self.functions[clone.id] = clone
        self._register_tags(clone.tags)
        self.history.record(
            clone, "create", f"复制自函数体《{source.display_title}》", author or self.author,
            force=True,
        )
        self._touch()
        return clone

    def delete_item(self, item_id: str, *, hard: bool = False, author: Optional[str] = None) -> None:
        """删除任意一类实体 (软删除进回收站, ``hard=True`` 时彻底移除)。"""
        kind = self.kind_of(item_id)
        if kind == "space":
            self.delete_space(item_id, hard=hard, author=author)
        elif kind == "function":
            self.delete_function(item_id, hard=hard, author=author)
        else:
            self.delete_entry(item_id, hard=hard, author=author)

    def restore_item(self, item_id: str, *, author: Optional[str] = None) -> Any:
        kind = self.kind_of(item_id)
        if kind == "space":
            return self.restore_space(item_id, author=author)
        if kind == "function":
            return self.restore_function(item_id, author=author)
        return self.restore_entry(item_id, author=author)

    def item_is_deleted(self, item_id: str) -> bool:
        entity = self.get_item(item_id)
        return bool(entity is None or entity.deleted)

    # ==================================================================================
    # 历史 / 回滚
    # ==================================================================================
    def revisions(self, item_id: str, *, descending: bool = True) -> List[Revision]:
        """返回某个对象的修订列表。

        刻意**不校验对象是否存在**: 彻底删除后历史仍然要能查看 (回收站/审计)。
        """
        if descending:
            return self.history.revisions_desc(item_id)
        return self.history.revisions(item_id)

    def revision_diff(self, revision_id: str, *, context: int = 3) -> str:
        rev = self.history.revision(revision_id)
        if rev is None:
            raise RepositoryError(f"修订不存在: {revision_id}")
        timeline = self.history.revisions(rev.entry_id)
        index = self.history.index_of(rev.entry_id, rev.id)
        before = timeline[index - 1].snapshot if index > 0 else None
        return build_revision_diff(before, rev.snapshot, context=context)

    def snapshot_diff(
        self, before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]], *, context: int = 3
    ) -> str:
        return build_revision_diff(before, after, context=context)

    def _apply_snapshot(
        self, item_id: str, snapshot: Dict[str, Any], kind: Optional[str] = None
    ) -> Any:
        """按快照重建实体并放回对应的集合。"""
        resolved = kind or kind_of_snapshot(snapshot)
        data = copy.deepcopy(snapshot)
        data["id"] = item_id
        if resolved == "space":
            entity: Any = Space.from_dict(data)
        elif resolved == "function":
            entity = Function.from_dict(data)
        else:
            entity = Entry.from_dict(data)
        self.bucket(resolved)[item_id] = entity
        self._register_entity_tags(entity)
        self._invalidate(entity)
        self._touch()
        return entity

    def restore_revision(
        self, item_id: str, revision_id: str, *, author: Optional[str] = None
    ) -> Any:
        """把实体**全量**恢复到某个历史版本, 并追加一条 ``restore`` 修订。

        历史本身不会被截断, 因此恢复操作同样可以再被恢复。
        """
        entity = self.require_any(item_id)
        rev = self.history.revision(revision_id)
        if rev is None:
            raise RepositoryError(f"修订不存在: {revision_id}")
        if rev.entry_id != item_id:
            raise RepositoryError("修订不属于该对象")

        kind = rev.kind or self.kind_of(item_id)
        current = take_snapshot(entity)
        restored = self._apply_snapshot(item_id, rev.snapshot, kind)
        self.history.record(
            restored,
            "restore",
            f"回滚到 {rev.time_label} 的版本 ({rev.action_label}); "
            + summarize_changes(current, rev.snapshot),
            author or self.author,
            force=True,
        )
        self.history.set_cursor(item_id, len(self.history.revisions(item_id)) - 1)
        return restored

    def undo(self, item_id: str, *, author: Optional[str] = None) -> Optional[Any]:
        """撤销: 游标前移一位并应用其快照 (不追加新修订)。"""
        self.require_any(item_id)
        target = self.history.step(item_id, -1)
        if target is None:
            return None
        return self._apply_snapshot(item_id, target.snapshot, target.kind)

    def redo(self, item_id: str, *, author: Optional[str] = None) -> Optional[Any]:
        """重做: 游标后移一位并应用其快照 (不追加新修订)。"""
        self.require_any(item_id)
        target = self.history.step(item_id, 1)
        if target is None:
            return None
        return self._apply_snapshot(item_id, target.snapshot, target.kind)

    def can_undo(self, entry_id: str) -> bool:
        return self.history.can_undo(entry_id)

    def can_redo(self, entry_id: str) -> bool:
        return self.history.can_redo(entry_id)

    # ==================================================================================
    # 查询辅助
    # ==================================================================================
    def require(self, entry_id: str) -> Entry:
        entry = self.entries.get(entry_id)
        if entry is None:
            raise RepositoryError(f"条目不存在: {entry_id}")
        return entry

    def get(self, entry_id: str) -> Optional[Entry]:
        return self.entries.get(entry_id)

    def entries_list(self, *, include_deleted: bool = False) -> List[Entry]:
        return [e for e in self.entries.values() if include_deleted or not e.deleted]

    def all_implementations(self) -> List[Tuple[Entry, Implementation]]:
        out: List[Tuple[Entry, Implementation]] = []
        for entry in self.entries_list():
            for impl in entry.active_implementations:
                out.append((entry, impl))
        return out

    def search_text(self, entity: Any) -> str:
        key = (entity.id, entity.version, entity.deleted)
        cached = self._search_cache.get(key)
        if cached is None:
            cached = entity.search_blob
            self._search_cache = {k: v for k, v in self._search_cache.items() if k[0] != entity.id}
            self._search_cache[key] = cached
        return cached

    def language_usage(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for _entry, impl in self.all_implementations():
            counts[impl.language] = counts.get(impl.language, 0) + 1
        return counts

    def space_language_bytes(self) -> Dict[str, int]:
        """汇总全部空间的代码字节数 (GitHub 风格占比的数据来源)。"""
        totals: Dict[str, int] = {}
        for space in self.spaces.values():
            if space.deleted:
                continue
            for share in space.language_shares():
                totals[share.language] = totals.get(share.language, 0) + share.bytes
        return totals

    def overall_language_shares(self) -> List[Any]:
        """整个代码库的语言占比 (按空间里的代码字节数统计)。"""
        from .spaces import LanguageShare

        totals = self.space_language_bytes()
        grand = sum(totals.values())
        shares = []
        for language, size in totals.items():
            share = LanguageShare(language=language, bytes=size, files=0, lines=0)
            share.percent = (size / grand * 100.0) if grand else 0.0
            shares.append(share)
        shares.sort(key=lambda s: (-s.bytes, s.name))
        return shares

    def statistics(self) -> Dict[str, Any]:
        entries = self.entries_list()
        deleted = [e for e in self.entries.values() if e.deleted]
        impls = [impl for e in entries for impl in e.active_implementations]
        spaces = [s for s in self.spaces.values() if not s.deleted]
        functions = [f for f in self.functions.values() if not f.deleted]
        function_impls = [impl for f in functions for impl in f.active_implementations]
        all_impls = impls + function_impls
        counts = self._entity_counts()
        return {
            "name": self.name,
            "entries": len(entries),
            "deleted_entries": len(deleted),
            "implementations": len(all_impls),
            "module_implementations": len(impls),
            "function_implementations": len(function_impls),
            "languages": len({impl.language for impl in all_impls}),
            "tags": len(self.all_tags(include_unused=False)),
            "revisions": self.history.total_count(),
            "code_lines": sum(impl.line_count for impl in all_impls),
            "code_chars": sum(len(impl.code) for impl in all_impls),
            # 三类实体
            "modules": counts.get("module", 0),
            "spaces": counts.get("space", 0),
            "functions": counts.get("function", 0),
            "space_files": sum(len(s.files) for s in spaces),
            "space_bytes": sum(s.total_size for s in spaces),
            "space_readmes": sum(len(s.readme_files) for s in spaces),
            "function_symbols": sum(f.total_symbols for f in functions),
            "symbols_missing_meaning": sum(
                len(f.symbols_missing_meaning) for f in functions
            ),
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }

    # ==================================================================================
    # 内部
    # ==================================================================================
    def _invalidate(self, entity: Any) -> None:
        self._search_cache = {k: v for k, v in self._search_cache.items() if k[0] != entity.id}

    def _touch(self) -> None:
        self.modified_at = utcnow()
        self.dirty = True

    def mark_clean(self) -> None:
        self.dirty = False

    # ==================================================================================
    # 序列化
    # ==================================================================================
    def to_dict(self, *, include_history: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "tags": [info.to_dict() for info in self.tags.values()],
            "entries": [entry.to_dict() for entry in self.entries.values()],
            # 三类实体同处一个容器: 旧版本读到多余键会忽略, 新版本缺键则当作空
            "spaces": [space.to_dict() for space in self.spaces.values()],
            "functions": [func.to_dict() for func in self.functions.values()],
            "extra": dict(self.extra),
        }
        if include_history:
            data["history"] = self.history.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Repository":
        repo = cls(
            name=str(data.get("name") or "未命名代码库"),
            description=str(data.get("description") or ""),
            author=str(data.get("author") or "local"),
        )
        repo.schema_version = int(data.get("schema_version") or SCHEMA_VERSION)
        repo.created_at = float(data.get("created_at") or utcnow())
        repo.modified_at = float(data.get("modified_at") or repo.created_at)
        repo.extra = dict(data.get("extra") or {})

        for item in data.get("entries") or []:
            if not isinstance(item, dict):
                continue
            entry = Entry.from_dict(item)
            repo.entries[entry.id] = entry
        for item in data.get("spaces") or []:
            if not isinstance(item, dict):
                continue
            space = Space.from_dict(item)
            repo.spaces[space.id] = space
        for item in data.get("functions") or []:
            if not isinstance(item, dict):
                continue
            function = Function.from_dict(item)
            repo.functions[function.id] = function
        for item in data.get("tags") or []:
            if not isinstance(item, dict):
                continue
            info = TagInfo.from_dict(item)
            if info.name:
                repo.tags[info.key] = info
        # 为没有元数据的标签补齐 (三类实体的标签都要收)
        seen: List[str] = []
        for entity in repo.items(include_deleted=True):
            seen.extend(getattr(entity, "tags", ()) or ())
        repo._register_tags(seen)

        history_data = data.get("history")
        if isinstance(history_data, dict):
            repo.history = HistoryStore.from_dict(history_data)
        else:
            # 没有历史数据时, 至少为每个对象补一条初始快照
            for entity in repo.items(include_deleted=True):
                repo.history.record(entity, "create", "导入的初始版本", force=True)
        repo.dirty = False
        return repo

    def clone(self) -> "Repository":
        return Repository.from_dict(copy.deepcopy(self.to_dict()))


__all__ = [
    "Repository",
    "RepositoryError",
    "ImportReport",
    "TagInfo",
    "TAG_PALETTE",
    "SCHEMA_VERSION",
    "ACTION_LABELS",
    "DEFAULT_LANGUAGE",
    "build_revision_diff",
    "summarize_changes",
    "detect_function_symbols",
    "detect_implementation_symbols",
]
