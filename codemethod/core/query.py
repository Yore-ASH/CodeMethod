"""多标签查询: 检索条件、匹配与排序.

检索支持一套轻量查询语法 (与全文搜索框共用)::

    socket server            # 同时包含两个词
    "tcp server"             # 短语
    -deprecated              # 排除
    tag:network              # 只匹配标签
    lang:python              # 限定语言
    status:done              # 限定状态
    is:favorite / is:deleted # 特殊标记
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .languages import normalize_language
from .models import STATUS_ORDER, Entry, tag_key

_TOKEN_RE = re.compile(r'(-)?(?:([A-Za-z_]+):)?(?:"([^"]*)"|(\S+))')


class TagMatch(str, Enum):
    """多标签组合方式。"""

    ALL = "all"      # 必须包含全部标签 (AND)
    ANY = "any"      # 包含任意一个标签 (OR)
    NONE = "none"    # 排除所有列出的标签 (NOT)
    EXACT = "exact"  # 标签集合完全相等


SORT_OPTIONS: Sequence[tuple] = (
    ("updated_desc", "最近修改"),
    ("updated_asc", "最早修改"),
    ("created_desc", "最近创建"),
    ("created_asc", "最早创建"),
    ("title_asc", "标题 A→Z"),
    ("title_desc", "标题 Z→A"),
    ("status", "按规划状态"),
    ("lines_desc", "代码行数"),
    ("revisions_desc", "修订次数"),
    ("tags_desc", "标签数量"),
)

SORT_LABELS: Dict[str, str] = {key: label for key, label in SORT_OPTIONS}


@dataclass
class QuerySpec:
    """一次多标签检索的完整条件。"""

    text: str = ""
    tags: List[str] = field(default_factory=list)
    tag_mode: TagMatch = TagMatch.ALL
    languages: List[str] = field(default_factory=list)
    statuses: List[str] = field(default_factory=list)
    favorites_only: bool = False
    include_deleted: bool = False
    only_deleted: bool = False
    search_code: bool = True
    search_description: bool = True
    sort_key: str = "updated_desc"

    # ---- 便捷构造 ----
    def clone(self, **changes: Any) -> "QuerySpec":
        data = {
            "text": self.text,
            "tags": list(self.tags),
            "tag_mode": self.tag_mode,
            "languages": list(self.languages),
            "statuses": list(self.statuses),
            "favorites_only": self.favorites_only,
            "include_deleted": self.include_deleted,
            "only_deleted": self.only_deleted,
            "search_code": self.search_code,
            "search_description": self.search_description,
            "sort_key": self.sort_key,
        }
        data.update(changes)
        return QuerySpec(**data)

    @property
    def is_empty(self) -> bool:
        return not (
            self.text.strip()
            or self.tags
            or self.languages
            or self.statuses
            or self.favorites_only
            or self.only_deleted
        )

    def describe(self) -> str:
        bits: List[str] = []
        if self.text.strip():
            bits.append(f'文本 "{self.text.strip()}"')
        if self.tags:
            connector = {"all": " 且 ", "any": " 或 ", "none": " 排除 ", "exact": " 恰为 "}[
                self.tag_mode.value
            ]
            bits.append("标签 " + connector.join(self.tags))
        if self.languages:
            bits.append("语言 " + "/".join(self.languages))
        if self.statuses:
            bits.append("状态 " + "/".join(self.statuses))
        if self.favorites_only:
            bits.append("仅收藏")
        if self.only_deleted:
            bits.append("回收站")
        return "; ".join(bits) if bits else "全部条目"


# --------------------------------------------------------------------------------------
# 文本查询解析
# --------------------------------------------------------------------------------------


@dataclass
class TextTerm:
    value: str
    negate: bool = False
    field: Optional[str] = None  # None / tag / lang / status / is

    def __str__(self) -> str:
        prefix = "-" if self.negate else ""
        head = f"{self.field}:" if self.field else ""
        return f"{prefix}{head}{self.value}"


def parse_query_text(text: str) -> List[TextTerm]:
    """把查询串解析为词项列表。"""
    terms: List[TextTerm] = []
    for match in _TOKEN_RE.finditer(text or ""):
        negate, field_name, quoted, bare = match.groups()
        value = quoted if quoted is not None else bare
        if value is None or value == "":
            continue
        terms.append(
            TextTerm(
                value=value,
                negate=bool(negate),
                field=field_name.lower() if field_name else None,
            )
        )
    return terms


def full_text_blob(entry: Entry, *, search_code: bool = True, search_description: bool = True) -> str:
    """按范围拼出用于子串匹配的文本 (全部小写)。

    标题与标签始终参与匹配; 描述/前置要求与源代码是否参与由调用方决定。
    """
    parts: List[str] = [entry.title, " ".join(entry.tags)]
    if search_description:
        parts.append(entry.description)
        parts.append(entry.prerequisites)
    for impl in entry.active_implementations:
        parts.append(impl.title)
        parts.append(impl.filename)
        parts.append(impl.notes)
        if search_code:
            parts.append(impl.code)
    return "\n".join(parts).casefold()


def _entry_matches_term(
    repo,
    entry: Entry,
    term: TextTerm,
    *,
    search_code: bool = True,
    search_description: bool = True,
    blob: Optional[str] = None,
) -> bool:
    """判断单个词项是否命中。``field_name`` 决定作用域, 否则做全文匹配。"""
    value = term.value
    field_name = term.field
    low = value.casefold()
    hit: bool

    if field_name in ("tag", "tags", "label"):
        hit = any(tag_key(t) == tag_key(value) or low in t.casefold() for t in entry.tags)

    elif field_name in ("lang", "language"):
        want = normalize_language(value)
        hit = any(impl.language == want for impl in entry.active_implementations)
        if not hit:  # 别名/文本兜底, 例如 lang:py3
            hit = low in " ".join(entry.languages).casefold()

    elif field_name in ("status", "state"):
        hit = entry.status == low or entry.status_label.casefold() == low

    elif field_name in ("is", "flag"):
        if low in ("favorite", "fav", "star", "收藏"):
            hit = entry.favorite
        elif low in ("deleted", "trash", "回收站"):
            hit = entry.deleted
        elif low in ("has_code", "code", "有代码"):
            hit = bool(entry.active_implementations)
        elif low in ("multi", "multilang", "多语言"):
            hit = len(entry.languages) > 1
        elif low in ("history", "有历史"):
            hit = repo.history.count(entry.id) > 1 if repo is not None else entry.version > 1
        else:
            hit = False

    else:
        # 未知字段名或裸词: 全文匹配 (未知字段时把 "x:y" 当作普通文本)
        needle = low if field_name is None else f"{field_name}:{low}"
        haystack = blob
        if haystack is None:
            haystack = full_text_blob(
                entry, search_code=search_code, search_description=search_description
            )
        hit = needle in haystack

    return not hit if term.negate else hit


def _entry_matches_text(
    repo,
    entry: Entry,
    text: str,
    *,
    search_code: bool = True,
    search_description: bool = True,
) -> bool:
    terms = parse_query_text(text)
    if not terms:
        return True
    blob: Optional[str] = None
    for term in terms:
        needs_blob = term.field not in ("tag", "tags", "label", "lang", "language", "status", "state", "is", "flag")
        if needs_blob and blob is None:
            blob = full_text_blob(
                entry, search_code=search_code, search_description=search_description
            )
        if not _entry_matches_term(
            repo,
            entry,
            term,
            search_code=search_code,
            search_description=search_description,
            blob=blob,
        ):
            return False
    return True


# --------------------------------------------------------------------------------------
# 匹配与排序
# --------------------------------------------------------------------------------------


def entry_matches(repo, entry: Entry, spec: QuerySpec) -> bool:
    """判断条目是否满足查询条件。"""
    if spec.only_deleted:
        if not entry.deleted:
            return False
    elif not spec.include_deleted and entry.deleted:
        return False

    if spec.favorites_only and not entry.favorite:
        return False

    if spec.statuses and entry.status not in spec.statuses:
        return False

    if spec.languages:
        wanted = {normalize_language(lang) for lang in spec.languages}
        if not wanted & set(entry.languages):
            return False

    if spec.tags:
        entry_keys = {tag_key(t) for t in entry.tags}
        wanted_keys = {tag_key(t) for t in spec.tags}
        if spec.tag_mode is TagMatch.ALL:
            if not wanted_keys.issubset(entry_keys):
                return False
        elif spec.tag_mode is TagMatch.ANY:
            if not (wanted_keys & entry_keys):
                return False
        elif spec.tag_mode is TagMatch.NONE:
            if wanted_keys & entry_keys:
                return False
        elif spec.tag_mode is TagMatch.EXACT:
            if entry_keys != wanted_keys:
                return False

    if spec.text.strip():
        if not _entry_matches_text(
            repo,
            entry,
            spec.text,
            search_code=spec.search_code,
            search_description=spec.search_description,
        ):
            return False

    return True


def query_entries(repo, spec: QuerySpec) -> List[Entry]:
    """按条件过滤并排序。"""
    found = [e for e in repo.entries.values() if entry_matches(repo, e, spec)]
    return sort_entries(found, spec.sort_key, repo=repo)


_STATUS_RANK = {name: i for i, name in enumerate(STATUS_ORDER)}


def sort_entries(entries: Iterable[Entry], sort_key: str = "updated_desc", *, repo=None) -> List[Entry]:
    """排序。``repo`` 用于需要历史信息的排序键。"""
    items = list(entries)
    key = sort_key or "updated_desc"

    if key == "updated_desc":
        items.sort(key=lambda e: e.updated_at, reverse=True)
    elif key == "updated_asc":
        items.sort(key=lambda e: e.updated_at)
    elif key == "created_desc":
        items.sort(key=lambda e: e.created_at, reverse=True)
    elif key == "created_asc":
        items.sort(key=lambda e: e.created_at)
    elif key == "title_asc":
        items.sort(key=lambda e: e.display_title.casefold())
    elif key == "title_desc":
        items.sort(key=lambda e: e.display_title.casefold(), reverse=True)
    elif key == "status":
        items.sort(key=lambda e: (_STATUS_RANK.get(e.status, 99), -e.updated_at))
    elif key == "lines_desc":
        items.sort(key=lambda e: (-e.total_lines, e.display_title.casefold()))
    elif key == "tags_desc":
        items.sort(key=lambda e: (-len(e.tags), e.display_title.casefold()))
    elif key == "revisions_desc":
        if repo is not None:
            items.sort(key=lambda e: (-repo.history.count(e.id), e.display_title.casefold()))
        else:
            items.sort(key=lambda e: -e.version)
    else:
        items.sort(key=lambda e: e.updated_at, reverse=True)
    return items


def tag_cooccurrence(repo, *, include_deleted: bool = False) -> Dict[tuple, int]:
    """统计标签共现次数, 供"相关标签"推荐使用。"""
    pairs: Dict[tuple, int] = {}
    for entry in repo.entries.values():
        if entry.deleted and not include_deleted:
            continue
        keys = sorted({tag_key(t) for t in entry.tags})
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                pairs[(a, b)] = pairs.get((a, b), 0) + 1
    return pairs


def related_tags(repo, tag: str, *, limit: int = 8, include_deleted: bool = False) -> List[str]:
    """与给定标签最常一起出现的标签。"""
    key = tag_key(tag)
    scores: Dict[str, int] = {}
    display: Dict[str, str] = {}
    for entry in repo.entries.values():
        if entry.deleted and not include_deleted:
            continue
        keys = {tag_key(t): t for t in entry.tags}
        if key not in keys:
            continue
        for other_key, other_name in keys.items():
            if other_key == key:
                continue
            scores[other_key] = scores.get(other_key, 0) + 1
            display[other_key] = other_name
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [display[k] for k, _ in ranked[:limit]]


__all__ = [
    "QuerySpec",
    "TagMatch",
    "SORT_OPTIONS",
    "SORT_LABELS",
    "TextTerm",
    "parse_query_text",
    "full_text_blob",
    "entry_matches",
    "query_entries",
    "sort_entries",
    "tag_cooccurrence",
    "related_tags",
]
