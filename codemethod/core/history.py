"""修订历史: 全量快照、撤销/重做与差异计算.

设计要点
--------
* **只追加 (append-only)**: 任何一次修改都生成一条全量快照, 永不删除也不覆盖,
  因此每个历史时刻都可以被完整恢复;
* **游标 (cursor)**: 仅作为浏览位置。``undo``/``redo`` 移动游标并把对应快照
  应用回实时状态, 历史列表本身不受影响;
* **校验和**: 每条快照计算 SHA-256, 用于去重、完整性校验与"这份历史有没有变过"的判断。
"""

from __future__ import annotations

import difflib
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import Entry, Revision, diff_tag_sets, new_id, utcnow

MAX_REVISIONS_PER_ENTRY = 500


# --------------------------------------------------------------------------------------
# 校验和 / 快照工具
# --------------------------------------------------------------------------------------


def canonical_json(data: Any) -> str:
    """稳定序列化, 保证同一份内容永远得到同一个字符串。"""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checksum_of(snapshot: Dict[str, Any]) -> str:
    """快照的 SHA-256 摘要。"""
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()


def take_snapshot(entry: Entry) -> Dict[str, Any]:
    """取得条目的全量快照 (不含历史本身)。"""
    return entry.to_dict(include_implementations=True)


# --------------------------------------------------------------------------------------
# 差异计算
# --------------------------------------------------------------------------------------


def _unified_diff(before: str, after: str, label: str, context: int = 3) -> List[str]:
    before_lines = (before or "").splitlines(keepends=False)
    after_lines = (after or "").splitlines(keepends=False)
    if before_lines == after_lines:
        return []
    gen = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
        lineterm="",
        n=context,
    )
    return list(gen)


_TEXT_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("title", "标题"),
    ("description", "描述"),
    ("prerequisites", "通用前置要求"),
    ("status", "状态"),
)

# 单个语言实现的元信息字段 (用于差异与摘要)
_IMPL_META_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("language", "语言"),
    ("title", "标题"),
    ("filename", "文件名"),
    ("notes", "备注"),
    ("prerequisites", "前置要求"),
)


def build_revision_diff(
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    *,
    context: int = 3,
) -> str:
    """生成可直接显示的统一差异文本。

    ``before`` 为 ``None`` 表示"新增"; ``after`` 为 ``None`` 表示"删除"。
    """
    chunks: List[str] = []
    if before is None and after is None:
        return ""
    if before is None:
        chunks.append("+++ 新增条目 +++")
        before = {}
    if after is None:
        chunks.append("--- 删除条目 ---")
        after = {}

    for field_name, label in _TEXT_FIELDS:
        old = str(before.get(field_name) or "")
        new = str(after.get(field_name) or "")
        if old == new:
            continue
        diff = _unified_diff(old, new, label, context)
        if diff:
            chunks.append(f"### {label}")
            chunks.extend(diff)
            chunks.append("")

    added, removed = diff_tag_sets(before.get("tags") or [], after.get("tags") or [])
    if added or removed:
        chunks.append("### 标签")
        for tag in removed:
            chunks.append(f"-{tag}")
        for tag in added:
            chunks.append(f"+{tag}")
        chunks.append("")

    if bool(before.get("favorite")) != bool(after.get("favorite")):
        chunks.append("### 收藏")
        chunks.append(f"-{'收藏' if before.get('favorite') else '未收藏'}")
        chunks.append(f"+{'收藏' if after.get('favorite') else '未收藏'}")
        chunks.append("")

    old_impls = {i.get("id"): i for i in (before.get("implementations") or [])}
    new_impls = {i.get("id"): i for i in (after.get("implementations") or [])}

    for impl_id, impl in old_impls.items():
        if impl_id not in new_impls:
            label = impl.get("filename") or impl.get("language") or "实现"
            chunks.append(f"### 删除实现 · {label} ({impl.get('language')})")
            chunks.extend(f"-{line}" for line in (impl.get("code") or "").splitlines() or ["<空>"])
            chunks.append("")

    for impl_id, impl in new_impls.items():
        label = impl.get("filename") or impl.get("language") or "实现"
        if impl_id not in old_impls:
            chunks.append(f"### 新增实现 · {label} ({impl.get('language')})")
            chunks.extend(f"+{line}" for line in (impl.get("code") or "").splitlines() or ["<空>"])
            chunks.append("")
            continue

        old_impl = old_impls[impl_id]
        for field_name, field_label in _IMPL_META_FIELDS:
            old_val = str(old_impl.get(field_name) or "")
            new_val = str(impl.get(field_name) or "")
            if old_val == new_val:
                continue
            chunks.append(f"### {label} · {field_label}")
            # 前置要求可能写成多行, 用统一差异而不是单行 +/-
            chunks.extend(_unified_diff(old_val, new_val, f"{label} · {field_label}", context))
            chunks.append("")

        if bool(old_impl.get("deleted")) != bool(impl.get("deleted")):
            chunks.append(f"### {label} · 删除标记")
            chunks.append(f"-deleted={bool(old_impl.get('deleted'))}")
            chunks.append(f"+deleted={bool(impl.get('deleted'))}")
            chunks.append("")

        code_diff = _unified_diff(
            str(old_impl.get("code") or ""), str(impl.get("code") or ""), label, context
        )
        if code_diff:
            chunks.append(f"### 代码 · {label}")
            chunks.extend(code_diff)
            chunks.append("")

    if len(old_impls) != len(new_impls):
        chunks.append(f"（实现数量: {len(old_impls)} → {len(new_impls)}）")

    return "\n".join(chunks).strip()


def summarize_changes(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> str:
    """生成一句话变更摘要, 形如 ``标题, 描述, Python 代码 +12/-3, 标签 +2``。"""
    if before is None:
        return "新建条目"
    if after is None:
        return "删除条目"

    bits: List[str] = []
    for field_name, label in _TEXT_FIELDS:
        if str(before.get(field_name) or "") != str(after.get(field_name) or ""):
            bits.append(label)

    added, removed = diff_tag_sets(before.get("tags") or [], after.get("tags") or [])
    if added or removed:
        parts = []
        if added:
            parts.append(f"+{len(added)}")
        if removed:
            parts.append(f"-{len(removed)}")
        bits.append(f"标签 {'/'.join(parts)}")

    if bool(before.get("favorite")) != bool(after.get("favorite")):
        bits.append("收藏")

    old_impls = {i.get("id"): i for i in (before.get("implementations") or [])}
    new_impls = {i.get("id"): i for i in (after.get("implementations") or [])}

    for impl_id in new_impls.keys() - old_impls.keys():
        bits.append(f"新增实现 ({new_impls[impl_id].get('language')})")
    for impl_id in old_impls.keys() - new_impls.keys():
        bits.append(f"删除实现 ({old_impls[impl_id].get('language')})")

    for impl_id in old_impls.keys() & new_impls.keys():
        old_impl, new_impl = old_impls[impl_id], new_impls[impl_id]
        lang = new_impl.get("language")
        old_code = str(old_impl.get("code") or "")
        new_code = str(new_impl.get("code") or "")
        if old_code != new_code:
            added_n = removed_n = 0
            for line in difflib.unified_diff(
                old_code.splitlines(), new_code.splitlines(), lineterm="", n=0
            ):
                if line.startswith("+") and not line.startswith("+++"):
                    added_n += 1
                elif line.startswith("-") and not line.startswith("---"):
                    removed_n += 1
            bits.append(f"{lang} 代码 +{added_n}/-{removed_n}")
        elif bool(old_impl.get("deleted")) != bool(new_impl.get("deleted")):
            bits.append("实现删除标记")

        # 语言 / 标题 / 文件名 / 备注 / 前置要求 的元信息改动
        changed_fields = [
            field_label
            for field_name, field_label in _IMPL_META_FIELDS
            if str(old_impl.get(field_name) or "") != str(new_impl.get(field_name) or "")
        ]
        if changed_fields:
            bits.append(f"{lang} 实现{'/'.join(changed_fields)}")

    return ", ".join(bits) if bits else "无实质变更"


# --------------------------------------------------------------------------------------
# HistoryStore
# --------------------------------------------------------------------------------------


class HistoryStore:
    """按条目维护的修订时间线。"""

    def __init__(self, max_revisions: int = MAX_REVISIONS_PER_ENTRY) -> None:
        self.max_revisions = max(1, int(max_revisions))
        self._revisions: Dict[str, List[Revision]] = {}
        self._index: Dict[str, Revision] = {}
        self._cursor: Dict[str, int] = {}

    # ---- 记录 ----
    def record(
        self,
        entry: Entry,
        action: str = "update",
        summary: str = "",
        author: str = "local",
        *,
        snapshot: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[Revision]:
        """为 ``entry`` 追加一条修订。

        若新快照与当前最后一条完全相同且 ``force=False``, 则跳过, 返回 ``None``。
        """
        snap = snapshot if snapshot is not None else take_snapshot(entry)
        digest = checksum_of(snap)
        timeline = self._revisions.setdefault(entry.id, [])

        current_index = self._cursor.get(entry.id, len(timeline) - 1)
        parent: Optional[str] = None
        if timeline:
            if 0 <= current_index < len(timeline):
                parent = timeline[current_index].id
            else:
                parent = timeline[-1].id
            if timeline[-1].checksum == digest and not force:
                self._cursor[entry.id] = len(timeline) - 1
                return None

        rev = Revision(
            entry_id=entry.id,
            action=action,
            summary=summary or summarize_changes(
                timeline[-1].snapshot if timeline else None, snap
            ),
            author=author,
            snapshot=snap,
            timestamp=utcnow(),
            parent_id=parent,
            checksum=digest,
        )
        timeline.append(rev)
        self._index[rev.id] = rev
        self._cursor[entry.id] = len(timeline) - 1
        self._prune(entry.id)
        return rev

    def _prune(self, entry_id: str) -> None:
        timeline = self._revisions.get(entry_id)
        if not timeline or len(timeline) <= self.max_revisions:
            return
        overflow = len(timeline) - self.max_revisions
        removed = timeline[:overflow]
        del timeline[:overflow]
        for rev in removed:
            self._index.pop(rev.id, None)
        cursor = self._cursor.get(entry_id, 0) - overflow
        self._cursor[entry_id] = max(0, min(cursor, len(timeline) - 1))

    # ---- 查询 ----
    def revisions(self, entry_id: str) -> List[Revision]:
        """按时间正序返回某条目的全部修订。"""
        return list(self._revisions.get(entry_id, ()))

    def revisions_desc(self, entry_id: str) -> List[Revision]:
        return list(reversed(self.revisions(entry_id)))

    def revision(self, revision_id: str) -> Optional[Revision]:
        return self._index.get(revision_id)

    def latest(self, entry_id: str) -> Optional[Revision]:
        timeline = self._revisions.get(entry_id)
        return timeline[-1] if timeline else None

    def first(self, entry_id: str) -> Optional[Revision]:
        timeline = self._revisions.get(entry_id)
        return timeline[0] if timeline else None

    def count(self, entry_id: str) -> int:
        return len(self._revisions.get(entry_id, ()))

    def total_count(self) -> int:
        return len(self._index)

    def entry_ids(self) -> Iterable[str]:
        return tuple(self._revisions.keys())

    def all_revisions(self, *, descending: bool = True) -> List[Revision]:
        """全部条目的修订, 按时间排序。"""
        items: List[Revision] = []
        for timeline in self._revisions.values():
            items.extend(timeline)
        items.sort(key=lambda r: r.timestamp, reverse=descending)
        return items

    def index_of(self, entry_id: str, revision_id: str) -> int:
        for i, rev in enumerate(self._revisions.get(entry_id, ())):
            if rev.id == revision_id:
                return i
        return -1

    # ---- 游标 / 撤销 / 重做 ----
    def cursor(self, entry_id: str) -> int:
        timeline = self._revisions.get(entry_id)
        if not timeline:
            return -1
        return max(0, min(self._cursor.get(entry_id, len(timeline) - 1), len(timeline) - 1))

    def current_revision(self, entry_id: str) -> Optional[Revision]:
        idx = self.cursor(entry_id)
        timeline = self._revisions.get(entry_id, ())
        if idx < 0 or idx >= len(timeline):
            return None
        return timeline[idx]

    def is_at_head(self, entry_id: str) -> bool:
        timeline = self._revisions.get(entry_id)
        if not timeline:
            return True
        return self.cursor(entry_id) >= len(timeline) - 1

    def can_undo(self, entry_id: str) -> bool:
        return self.cursor(entry_id) > 0

    def can_redo(self, entry_id: str) -> bool:
        timeline = self._revisions.get(entry_id)
        if not timeline:
            return False
        return self.cursor(entry_id) < len(timeline) - 1

    def step(self, entry_id: str, delta: int) -> Optional[Revision]:
        """移动游标并返回目标修订 (``delta`` 为 -1 撤销 / +1 重做)。"""
        timeline = self._revisions.get(entry_id)
        if not timeline:
            return None
        target = self.cursor(entry_id) + delta
        if target < 0 or target >= len(timeline):
            return None
        self._cursor[entry_id] = target
        return timeline[target]

    def set_cursor(self, entry_id: str, index: int) -> Optional[Revision]:
        timeline = self._revisions.get(entry_id)
        if not timeline:
            return None
        index = max(0, min(index, len(timeline) - 1))
        self._cursor[entry_id] = index
        return timeline[index]

    # ---- 维护 ----
    def import_timeline(self, entry_id: str, revisions: Iterable[Revision]) -> int:
        """把外部条目的修订并入 ``entry_id`` 的时间线, 返回并入的条数。

        合并两个曾经互相复制过的库时, 修订 id 会完全重合; 这里对冲突的 id 重新编号,
        避免 :attr:`_index` 被静默覆盖 (那会导致 ``total_count`` 与实际修订数不符)。
        """
        timeline = self._revisions.setdefault(entry_id, [])
        count = 0
        for rev in revisions:
            if rev.id in self._index:
                rev.id = new_id("rev_")
            rev.entry_id = entry_id
            timeline.append(rev)
            self._index[rev.id] = rev
            count += 1
        if timeline:
            timeline.sort(key=lambda r: r.timestamp)
            self._cursor[entry_id] = len(timeline) - 1
        return count

    def drop_entry(self, entry_id: str) -> None:
        for rev in self._revisions.pop(entry_id, []):
            self._index.pop(rev.id, None)
        self._cursor.pop(entry_id, None)

    def clear(self) -> None:
        self._revisions.clear()
        self._index.clear()
        self._cursor.clear()

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_revisions": self.max_revisions,
            "timelines": {
                entry_id: [rev.to_dict() for rev in timeline]
                for entry_id, timeline in self._revisions.items()
            },
            "cursors": dict(self._cursor),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HistoryStore":
        store = cls(max_revisions=int(data.get("max_revisions") or MAX_REVISIONS_PER_ENTRY))
        for entry_id, items in (data.get("timelines") or {}).items():
            timeline: List[Revision] = []
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                rev = Revision.from_dict(item)
                if not rev.entry_id:
                    rev.entry_id = entry_id
                if not rev.checksum:
                    rev.checksum = checksum_of(rev.snapshot)
                timeline.append(rev)
            timeline.sort(key=lambda r: r.timestamp)
            store._revisions[entry_id] = timeline
            for rev in timeline:
                store._index[rev.id] = rev
        for entry_id, cursor in (data.get("cursors") or {}).items():
            store._cursor[entry_id] = int(cursor)
        for entry_id in store._revisions:
            store._cursor.setdefault(entry_id, len(store._revisions[entry_id]) - 1)
        return store

    def stats(self) -> Dict[str, int]:
        return {
            "entries_with_history": len(self._revisions),
            "total_revisions": self.total_count(),
            "max_per_entry": self.max_revisions,
        }
