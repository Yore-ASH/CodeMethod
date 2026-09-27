"""仓储层: 条目/实现/标签的全部可变操作, 并保证每次修改都进入修订历史.

界面层只与本模块交互, 不直接改动 :class:`~codemethod.core.models.Entry` 对象,
从而"任何修改都有历史"成为结构上的保证, 而不是靠调用方自觉。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .history import HistoryStore, build_revision_diff, checksum_of, summarize_changes, take_snapshot
from .languages import DEFAULT_LANGUAGE, get_language, normalize_language
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

SCHEMA_VERSION = 1


class RepositoryError(Exception):
    """仓储层的可预期错误 (界面直接展示给用户)。"""


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
TAG_PALETTE: Sequence[str] = (
    "#569CD6",
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
        """返回 ``{规范标签名: 使用次数}``, 只统计未删除条目。"""
        counts: Dict[str, int] = {}
        for entry in self.entries.values():
            if entry.deleted:
                continue
            for tag in entry.tags:
                key = tag_key(tag)
                counts[key] = counts.get(key, 0) + 1
        return counts

    def all_tags(self, *, include_unused: bool = True, include_deleted: bool = False) -> List[str]:
        """所有标签名, 按使用次数降序、名称升序排列。"""
        usage = self.tag_usage()
        names: Dict[str, str] = {}
        for entry in self.entries.values():
            if entry.deleted and not include_deleted:
                continue
            for tag in entry.tags:
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
    # 历史 / 回滚
    # ==================================================================================
    def revisions(self, entry_id: str, *, descending: bool = True) -> List[Revision]:
        self.require(entry_id)
        return self.history.revisions_desc(entry_id) if descending else self.history.revisions(entry_id)

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

    def _apply_snapshot(self, entry_id: str, snapshot: Dict[str, Any]) -> Entry:
        entry = Entry.from_dict(copy.deepcopy(snapshot))
        entry.id = entry_id
        self.entries[entry_id] = entry
        self._register_tags(entry.tags)
        self._invalidate(entry)
        self._touch()
        return entry

    def restore_revision(
        self, entry_id: str, revision_id: str, *, author: Optional[str] = None
    ) -> Entry:
        """把条目**全量**恢复到某个历史版本, 并追加一条 ``restore`` 修订。

        历史本身不会被截断, 因此恢复操作同样可以再被恢复。
        """
        self.require(entry_id)
        rev = self.history.revision(revision_id)
        if rev is None:
            raise RepositoryError(f"修订不存在: {revision_id}")
        if rev.entry_id != entry_id:
            raise RepositoryError("修订不属于该条目")

        current = take_snapshot(self.require(entry_id))
        entry = self._apply_snapshot(entry_id, rev.snapshot)
        self.history.record(
            entry,
            "restore",
            f"回滚到 {rev.time_label} 的版本 ({rev.action_label}); "
            + summarize_changes(current, rev.snapshot),
            author or self.author,
            force=True,
        )
        self.history.set_cursor(entry_id, len(self.history.revisions(entry_id)) - 1)
        return self.entries[entry_id]

    def undo(self, entry_id: str, *, author: Optional[str] = None) -> Optional[Entry]:
        """撤销: 游标前移一位并应用其快照 (不追加新修订)。"""
        self.require(entry_id)
        target = self.history.step(entry_id, -1)
        if target is None:
            return None
        self._apply_snapshot(entry_id, target.snapshot)
        return self.entries[entry_id]

    def redo(self, entry_id: str, *, author: Optional[str] = None) -> Optional[Entry]:
        """重做: 游标后移一位并应用其快照 (不追加新修订)。"""
        self.require(entry_id)
        target = self.history.step(entry_id, 1)
        if target is None:
            return None
        self._apply_snapshot(entry_id, target.snapshot)
        return self.entries[entry_id]

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

    def search_text(self, entry: Entry) -> str:
        key = (entry.id, entry.version, entry.deleted)
        cached = self._search_cache.get(key)
        if cached is None:
            cached = entry.search_blob
            self._search_cache = {k: v for k, v in self._search_cache.items() if k[0] != entry.id}
            self._search_cache[key] = cached
        return cached

    def language_usage(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for _entry, impl in self.all_implementations():
            counts[impl.language] = counts.get(impl.language, 0) + 1
        return counts

    def statistics(self) -> Dict[str, Any]:
        entries = self.entries_list()
        deleted = [e for e in self.entries.values() if e.deleted]
        impls = [impl for e in entries for impl in e.active_implementations]
        return {
            "name": self.name,
            "entries": len(entries),
            "deleted_entries": len(deleted),
            "implementations": len(impls),
            "languages": len({impl.language for impl in impls}),
            "tags": len(self.all_tags(include_unused=False)),
            "revisions": self.history.total_count(),
            "code_lines": sum(impl.line_count for impl in impls),
            "code_chars": sum(len(impl.code) for impl in impls),
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }

    # ==================================================================================
    # 内部
    # ==================================================================================
    def _invalidate(self, entry: Entry) -> None:
        self._search_cache = {k: v for k, v in self._search_cache.items() if k[0] != entry.id}

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
        for item in data.get("tags") or []:
            if not isinstance(item, dict):
                continue
            info = TagInfo.from_dict(item)
            if info.name:
                repo.tags[info.key] = info
        # 为没有元数据的标签补齐
        seen: List[str] = []
        for entry in repo.entries.values():
            seen.extend(entry.tags)
        repo._register_tags(seen)

        history_data = data.get("history")
        if isinstance(history_data, dict):
            repo.history = HistoryStore.from_dict(history_data)
        else:
            # 没有历史数据时, 至少为每个条目补一条初始快照
            for entry in repo.entries.values():
                repo.history.record(entry, "create", "导入的初始版本", force=True)
        repo.dirty = False
        return repo

    def clone(self) -> "Repository":
        return Repository.from_dict(copy.deepcopy(self.to_dict()))


__all__ = [
    "Repository",
    "RepositoryError",
    "TagInfo",
    "TAG_PALETTE",
    "SCHEMA_VERSION",
    "ACTION_LABELS",
    "DEFAULT_LANGUAGE",
    "build_revision_diff",
    "summarize_changes",
]
