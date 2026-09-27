"""领域模型: 条目 / 语言实现 / 修订记录.

序列化约定
----------
所有模型都能转换成**纯 JSON 兼容**的 ``dict`` (``to_dict``) 并能从 ``dict`` 还原
(``from_dict``)。存储层 (:mod:`codemethod.storage`) 只认识这些 dict,
因此模型演进与文件格式演进是解耦的。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .languages import DEFAULT_LANGUAGE, detect_language_from_filename, normalize_language

# --------------------------------------------------------------------------------------
# 规划状态 (多标签规划中的"阶段"维度)
# --------------------------------------------------------------------------------------

STATUS_ORDER: Sequence[str] = ("idea", "planned", "in_progress", "done", "archived")

STATUS_LABELS: Dict[str, str] = {
    "idea": "构想",
    "planned": "已规划",
    "in_progress": "实现中",
    "done": "已完成",
    "archived": "已归档",
}

STATUS_COLORS: Dict[str, str] = {
    "idea": "#9E9E9E",
    "planned": "#569CD6",
    "in_progress": "#DCDCAA",
    "done": "#4EC9B0",
    "archived": "#6A6A6A",
}


def new_id(prefix: str = "") -> str:
    """生成短小且唯一的标识符。"""
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


def utcnow() -> float:
    """当前 UTC 时间戳 (秒, 浮点)。"""
    return time.time()


def format_ts(ts: Optional[float], fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """把时间戳格式化为本地时间字符串。"""
    if not ts:
        return "-"
    return time.strftime(fmt, time.localtime(ts))


def normalize_tag(tag: str) -> str:
    """标签规范化: 去空白、折叠内部空格、限制长度。

    标签大小写不敏感地视为同一标签 (见 :func:`tag_key`)。
    """
    cleaned = " ".join(str(tag).split())
    return cleaned[:64]


def tag_key(tag: str) -> str:
    """标签比较用的规范化键 (大小写不敏感, ``casefold`` 便于 Unicode)。"""
    return normalize_tag(tag).casefold()


# --------------------------------------------------------------------------------------
# Implementation
# --------------------------------------------------------------------------------------


@dataclass
class Implementation:
    """某个条目在**一种语言**下的具体实现。

    每种语言有自己的工具链与运行环境, 因此 **前置要求挂在实现上**:
    例如同一道题, Python 版要 3.10+、Go 版要 1.21+、Rust 版要 cargo。
    """

    language: str = DEFAULT_LANGUAGE
    code: str = ""
    title: str = ""
    filename: str = ""
    notes: str = ""
    prerequisites: str = ""
    id: str = field(default_factory=lambda: new_id("impl_"))
    created_at: float = field(default_factory=utcnow)
    updated_at: float = field(default_factory=utcnow)
    version: int = 1
    deleted: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    # ---- 生命周期 ----
    def __post_init__(self) -> None:
        self.language = normalize_language(self.language)
        if not self.filename:
            self.filename = self.suggest_filename()

    def suggest_filename(self) -> str:
        from .languages import default_filename

        return default_filename(self.language, self.title or "main")

    @property
    def display_title(self) -> str:
        from .languages import get_language

        name = get_language(self.language).name
        return self.title.strip() or name

    @property
    def line_count(self) -> int:
        return len(self.code.splitlines()) if self.code else 0

    def touch(self) -> None:
        self.updated_at = utcnow()
        self.version += 1

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "language": self.language,
            "title": self.title,
            "filename": self.filename,
            "code": self.code,
            "notes": self.notes,
            "prerequisites": self.prerequisites,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "deleted": self.deleted,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Implementation":
        filename = str(data.get("filename") or "")
        language_raw = data.get("language")
        language = (
            normalize_language(language_raw)
            if language_raw
            else detect_language_from_filename(filename)
        )
        return cls(
            id=str(data.get("id") or new_id("impl_")),
            language=language,
            title=str(data.get("title") or ""),
            filename=filename,
            code=str(data.get("code") or ""),
            notes=str(data.get("notes") or ""),
            # 旧版本没有这个字段: 缺失即为空, 向后兼容
            prerequisites=str(data.get("prerequisites") or ""),
            created_at=float(data.get("created_at") or utcnow()),
            updated_at=float(data.get("updated_at") or utcnow()),
            version=int(data.get("version") or 1),
            deleted=bool(data.get("deleted", False)),
            extra=dict(data.get("extra") or {}),
        )

    def clone(self, *, new_identity: bool = False) -> "Implementation":
        data = self.to_dict()
        if new_identity:
            data["id"] = new_id("impl_")
            data["created_at"] = utcnow()
            data["updated_at"] = data["created_at"]
            data["version"] = 1
        return Implementation.from_dict(data)


# --------------------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------------------


@dataclass
class Entry:
    """一个"功能条目": 描述 + 通用前置要求 + 标签 + 多语言实现。

    ``prerequisites`` 是**所有语言共用**的前置要求 (例如「需要理解双向链表」);
    每种语言自己的工具链/运行环境要求写在各自的
    :attr:`Implementation.prerequisites` 上 (例如「Python 3.10+」)。
    """

    title: str = ""
    description: str = ""
    prerequisites: str = ""
    tags: List[str] = field(default_factory=list)
    implementations: List[Implementation] = field(default_factory=list)
    status: str = "idea"
    favorite: bool = False
    id: str = field(default_factory=lambda: new_id("ent_"))
    created_at: float = field(default_factory=utcnow)
    updated_at: float = field(default_factory=utcnow)
    version: int = 1
    deleted: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tags = normalize_tags(self.tags)
        if self.status not in STATUS_ORDER:
            self.status = "idea"
        self.implementations = list(self.implementations)

    # ---- 便捷属性 ----
    #
    # 下面这几个属性同时存在于 Space / Function 上, 构成三类实体共用的最小接口,
    # 因此同一个列表模型与绘制委托可以原样渲染模块、空间与函数体。
    kind = "module"

    @property
    def kind_label(self) -> str:
        return "模块"

    @property
    def badge_count(self) -> int:
        return len(self.active_implementations)

    @property
    def badge_label(self) -> str:
        return f"{len(self.active_implementations)} 实现"

    @property
    def languages(self) -> List[str]:
        seen: List[str] = []
        for impl in self.active_implementations:
            if impl.language not in seen:
                seen.append(impl.language)
        return seen

    @property
    def active_implementations(self) -> List[Implementation]:
        return [impl for impl in self.implementations if not impl.deleted]

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def display_title(self) -> str:
        return self.title.strip() or "未命名条目"

    @property
    def total_lines(self) -> int:
        return sum(impl.line_count for impl in self.active_implementations)

    @property
    def search_blob(self) -> str:
        """供全文检索使用的合成文本 (小写)。"""
        parts = [self.title, self.description, self.prerequisites, " ".join(self.tags)]
        for impl in self.active_implementations:
            parts.append(impl.title)
            parts.append(impl.notes)
            # 每种语言的前置要求也要能被搜到 (例如 "需要 cargo")
            parts.append(impl.prerequisites)
            parts.append(impl.code)
        return "\n".join(parts).casefold()

    @property
    def prerequisites_by_language(self) -> List[tuple]:
        """``[(语言显示名, 该语言的前置要求), ...]``, 只列出填写过的。"""
        from .languages import get_language

        return [
            (get_language(impl.language).name, impl.prerequisites.strip())
            for impl in self.active_implementations
        ]

    # ---- 变更 ----
    def touch(self) -> None:
        self.updated_at = utcnow()
        self.version += 1

    def get_implementation(self, impl_id: str) -> Optional[Implementation]:
        for impl in self.implementations:
            if impl.id == impl_id:
                return impl
        return None

    def find_by_language(self, language: str) -> List[Implementation]:
        lang = normalize_language(language)
        return [impl for impl in self.active_implementations if impl.language == lang]

    # ---- 序列化 ----
    def to_dict(self, *, include_implementations: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "prerequisites": self.prerequisites,
            "tags": list(self.tags),
            "status": self.status,
            "favorite": self.favorite,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "deleted": self.deleted,
            "extra": dict(self.extra),
        }
        if include_implementations:
            data["implementations"] = [impl.to_dict() for impl in self.implementations]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entry":
        impls = [
            Implementation.from_dict(item)
            for item in (data.get("implementations") or [])
            if isinstance(item, dict)
        ]
        return cls(
            id=str(data.get("id") or new_id("ent_")),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            prerequisites=str(data.get("prerequisites") or ""),
            tags=list(data.get("tags") or []),
            implementations=impls,
            status=str(data.get("status") or "idea"),
            favorite=bool(data.get("favorite", False)),
            created_at=float(data.get("created_at") or utcnow()),
            updated_at=float(data.get("updated_at") or utcnow()),
            version=int(data.get("version") or 1),
            deleted=bool(data.get("deleted", False)),
            extra=dict(data.get("extra") or {}),
        )

    def clone(self) -> "Entry":
        """深拷贝 (不含修订历史, 历史由 :class:`~codemethod.core.history.HistoryStore` 管理)。"""
        copy = Entry.from_dict(self.to_dict())
        copy.id = self.id
        copy.implementations = [impl.clone() for impl in self.implementations]
        return copy


# --------------------------------------------------------------------------------------
# Revision
# --------------------------------------------------------------------------------------


# 修订动作类型 -> 中文描述
ACTION_LABELS: Dict[str, str] = {
    "create": "新建条目",
    "update": "修改条目",
    "delete": "删除条目",
    "restore_delete": "从回收站恢复",
    "purge": "彻底删除",
    "impl_add": "新增实现",
    "impl_update": "修改实现",
    "impl_delete": "删除实现",
    "impl_restore": "恢复实现",
    "tag_change": "标签变更",
    "status_change": "状态变更",
    "favorite": "收藏变更",
    "restore": "回滚到历史版本",
    "undo": "撤销",
    "redo": "重做",
    "import": "导入",
}


@dataclass
class Revision:
    """一次修改的**完整快照**。

    采用全量快照 (而非增量 diff) 存储, 因此任意一次历史都可以**无损全量恢复**;
    显示的差异文本由 :func:`codemethod.core.history.diff_snapshots` 按需计算。
    """

    entry_id: str
    action: str = "update"
    summary: str = ""
    author: str = "local"
    snapshot: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("rev_"))
    timestamp: float = field(default_factory=utcnow)
    parent_id: Optional[str] = None
    checksum: str = ""
    # 快照属于哪一类实体 (module / space / function)。旧文件没有这个字段, 按 module 处理。
    kind: str = "module"

    @property
    def action_label(self) -> str:
        return ACTION_LABELS.get(self.action, self.action)

    @property
    def entry_title(self) -> str:
        # 模块用 title, 空间/函数体用 name —— 历史面板统一显示成"标题"
        return str(self.snapshot.get("title") or self.snapshot.get("name") or "未命名")

    @property
    def kind_label(self) -> str:
        return {"module": "模块", "space": "空间", "function": "函数体"}.get(self.kind, self.kind)

    @property
    def time_label(self) -> str:
        return format_ts(self.timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "entry_id": self.entry_id,
            "action": self.action,
            "summary": self.summary,
            "author": self.author,
            "timestamp": self.timestamp,
            "parent_id": self.parent_id,
            "checksum": self.checksum,
            "kind": self.kind,
            "snapshot": self.snapshot,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Revision":
        return cls(
            id=str(data.get("id") or new_id("rev_")),
            entry_id=str(data.get("entry_id") or ""),
            action=str(data.get("action") or "update"),
            summary=str(data.get("summary") or ""),
            author=str(data.get("author") or "local"),
            timestamp=float(data.get("timestamp") or utcnow()),
            parent_id=data.get("parent_id"),
            checksum=str(data.get("checksum") or ""),
            kind=str(data.get("kind") or "module"),
            snapshot=dict(data.get("snapshot") or {}),
        )


# --------------------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------------------


def normalize_tags(tags: Iterable[str]) -> List[str]:
    """去重 (大小写不敏感) 并保持首次出现顺序。"""
    result: List[str] = []
    seen = set()
    for raw in tags or []:
        cleaned = normalize_tag(raw)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def diff_tag_sets(before: Iterable[str], after: Iterable[str]) -> tuple:
    """返回 ``(added, removed)`` 两组标签。"""
    before_map = {tag_key(t): t for t in before}
    after_map = {tag_key(t): t for t in after}
    added = [after_map[k] for k in after_map if k not in before_map]
    removed = [before_map[k] for k in before_map if k not in after_map]
    return added, removed
