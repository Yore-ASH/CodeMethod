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


def take_snapshot(entity: Any) -> Dict[str, Any]:
    """取得实体的全量快照 (不含历史本身)。

    模块 / 空间 / 函数体都实现了 ``to_dict()``, 因此这里用鸭子类型。
    """
    return entity.to_dict()


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


def kind_of_snapshot(snapshot: Optional[Dict[str, Any]]) -> str:
    """按快照里出现的关键字段判断它属于哪类实体。

    刻意用"看字段"而不是"看标记": 这样旧文件 (没有 kind 字段) 也能正确判断。

    注意函数体与模块现在**都**有 ``implementations``; 区别在于函数体的实现里带
    ``symbols`` (变量含义表), 模块的实现里没有。
    """
    if not isinstance(snapshot, dict):
        return "module"
    if "files" in snapshot:
        return "space"
    if "implementations" in snapshot:
        for item in snapshot.get("implementations") or []:
            if isinstance(item, dict) and "symbols" in item:
                return "function"
        # 实现列表为空时按顶层字段名兜底: 函数体用 name, 模块用 title
        if "title" not in snapshot and "name" in snapshot:
            return "function"
        return "module"
    if "symbols" in snapshot:
        return "function"
    return "module"


def _common_fields_diff(
    chunks: List[str],
    before: Dict[str, Any],
    after: Dict[str, Any],
    *,
    title_field: str,
    title_label: str,
    context: int,
) -> None:
    """标题/名称、描述、前置要求、状态、标签、收藏 这些三类实体共有的字段。"""
    fields = (
        (title_field, title_label),
        ("description", "描述"),
        ("prerequisites", "前置要求"),
        ("status", "状态"),
    )
    for field_name, label in fields:
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


def _binary_label(item: Dict[str, Any]) -> str:
    """二进制文件在差异视图里的一句话描述 (绝不输出 base64 正文)。"""
    size = int(item.get("size") or 0)
    if item.get("data"):
        return f"（二进制, {size} 字节, 内容已嵌入）"
    return f"（二进制, {size} 字节, 仅记录大小）"


def _space_diff(
    before: Dict[str, Any], after: Dict[str, Any], context: int
) -> List[str]:
    """空间的差异: 共有字段 + 文件的增删改。"""
    chunks: List[str] = []
    _common_fields_diff(
        chunks, before, after, title_field="name", title_label="空间名称", context=context
    )

    old_files = {f.get("path"): f for f in (before.get("files") or []) if isinstance(f, dict)}
    new_files = {f.get("path"): f for f in (after.get("files") or []) if isinstance(f, dict)}

    for path in sorted(set(old_files) - set(new_files)):
        item = old_files[path]
        chunks.append(f"### 删除文件 · {path}")
        if not item.get("binary"):
            body = (item.get("content") or "").splitlines() or ["<空文件>"]
            chunks.extend(f"-{line}" for line in body[:80])
        else:
            chunks.append(f"-（二进制, {item.get('size', 0)} 字节）")
        chunks.append("")

    for path in sorted(set(new_files) - set(old_files)):
        item = new_files[path]
        chunks.append(f"### 新增文件 · {path} ({item.get('language')})")
        if not item.get("binary"):
            body = (item.get("content") or "").splitlines() or ["<空文件>"]
            chunks.extend(f"+{line}" for line in body[:80])
        else:
            chunks.append(f"+（二进制, {item.get('size', 0)} 字节）")
        chunks.append("")

    for path in sorted(set(old_files) & set(new_files)):
        old_item, new_item = old_files[path], new_files[path]
        if str(old_item.get("note") or "") != str(new_item.get("note") or ""):
            chunks.append(f"### {path} · 说明")
            chunks.append(f"-{old_item.get('note') or '<空>'}")
            chunks.append(f"+{new_item.get('note') or '<空>'}")
            chunks.append("")
        if bool(old_item.get("binary")) != bool(new_item.get("binary")):
            chunks.append(f"### {path} · 二进制标记变更")
            chunks.append(f"-binary={bool(old_item.get('binary'))}")
            chunks.append(f"+binary={bool(new_item.get('binary'))}")
            chunks.append("")
            continue
        old_language, new_language = old_item.get("language"), new_item.get("language")
        if old_language != new_language:
            chunks.append(f"### {path} · 语言")
            chunks.append(f"-{old_language}")
            chunks.append(f"+{new_language}")
            chunks.append("")
        if old_item.get("binary"):
            # 二进制内容绝不 dump 出来 (base64 会把差异视图淹掉), 只报"变了多少"
            old_data = str(old_item.get("data") or "")
            new_data = str(new_item.get("data") or "")
            if old_data != new_data:
                chunks.append(f"### {path} · 二进制内容")
                chunks.append(
                    "-"
                    + _binary_label(old_item)
                )
                chunks.append(
                    "+"
                    + _binary_label(new_item)
                )
                chunks.append("")
            continue
        content_diff = _unified_diff(
            str(old_item.get("content") or ""), str(new_item.get("content") or ""), path, context
        )
        if content_diff:
            chunks.append(f"### 文件内容 · {path}")
            chunks.extend(content_diff)
            chunks.append("")

    if len(old_files) != len(new_files):
        chunks.append(f"（文件数量: {len(old_files)} → {len(new_files)}）")
    return chunks


def _implementation_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """按实现 id 索引 ``implementations`` (取不到 id 时退回语言 + 序号)。"""
    out: Dict[str, Dict[str, Any]] = {}
    for position, impl in enumerate(snapshot.get("implementations") or []):
        if not isinstance(impl, dict):
            continue
        key = str(impl.get("id") or f"{impl.get('language')}#{position}")
        out[key] = impl
    return out


def _symbol_index(implementation: Dict[str, Any]) -> Dict[tuple, Dict[str, Any]]:
    return {
        (s.get("kind"), s.get("name")): s
        for s in (implementation.get("symbols") or [])
        if isinstance(s, dict)
    }


def _symbol_diff_chunks(
    chunks: List[str], prefix: str, before_impl: Dict[str, Any], after_impl: Dict[str, Any]
) -> None:
    """变量表的变化: 新增 / 删除 / 含义被填写或修改。"""
    old_symbols = _symbol_index(before_impl)
    new_symbols = _symbol_index(after_impl)
    key_order = lambda k: (str(k[0]), str(k[1]))  # noqa: E731

    for key in sorted(set(new_symbols) - set(old_symbols), key=key_order):
        symbol = new_symbols[key]
        chunks.append(f"### 新增变量 · {prefix}{symbol.get('name')} ({symbol.get('kind')})")
        if symbol.get("type"):
            chunks.append(f"+类型: {symbol.get('type')}")
        if symbol.get("meaning"):
            chunks.append(f"+含义: {symbol.get('meaning')}")
        chunks.append("")

    for key in sorted(set(old_symbols) - set(new_symbols), key=key_order):
        symbol = old_symbols[key]
        chunks.append(f"### 删除变量 · {prefix}{symbol.get('name')} ({symbol.get('kind')})")
        if symbol.get("meaning"):
            chunks.append(f"-含义: {symbol.get('meaning')}")
        chunks.append("")

    for key in sorted(set(old_symbols) & set(new_symbols), key=key_order):
        old_symbol, new_symbol = old_symbols[key], new_symbols[key]
        for field_name, label in (("type", "类型"), ("meaning", "含义")):
            old_val = str(old_symbol.get(field_name) or "")
            new_val = str(new_symbol.get(field_name) or "")
            if old_val == new_val:
                continue
            chunks.append(f"### 变量 {prefix}{new_symbol.get('name')} · {label}")
            chunks.append(f"-{old_val or '<空>'}")
            chunks.append(f"+{new_val or '<空>'}")
            chunks.append("")


def _function_diff(
    before: Dict[str, Any], after: Dict[str, Any], context: int
) -> List[str]:
    """函数体的差异: 共有字段 + 每种语言实现各自的代码与**变量含义表**变化。"""
    chunks: List[str] = []
    _common_fields_diff(
        chunks, before, after, title_field="name", title_label="函数名", context=context
    )

    old_impls = _implementation_index(before)
    new_impls = _implementation_index(after)

    for impl_id in new_impls.keys() - old_impls.keys():
        impl = new_impls[impl_id]
        language = str(impl.get("language") or "?")
        chunks.append(f"### +++ 新增语言实现 · {language} +++")
        if impl.get("prerequisites"):
            chunks.append(f"+前置要求: {impl.get('prerequisites')}")
        code = str(impl.get("code") or "")
        if code:
            chunks.append(f"+代码 {len(code.splitlines())} 行")
        chunks.append("")

    for impl_id in old_impls.keys() - new_impls.keys():
        impl = old_impls[impl_id]
        language = str(impl.get("language") or "?")
        code = str(impl.get("code") or "")
        chunks.append(f"### --- 删除语言实现 · {language} ---")
        if code:
            chunks.append(f"-代码 {len(code.splitlines())} 行")
        chunks.append("")

    for impl_id in old_impls.keys() & new_impls.keys():
        old_impl, new_impl = old_impls[impl_id], new_impls[impl_id]
        language = str(new_impl.get("language") or old_impl.get("language") or "?")
        prefix = f"[{language}] "

        for field_name, label in (
            ("language", "语言"),
            ("signature", "函数签名"),
            ("prerequisites", "前置要求"),
            ("notes", "实现说明"),
        ):
            old_val = str(old_impl.get(field_name) or "")
            new_val = str(new_impl.get(field_name) or "")
            if old_val == new_val:
                continue
            chunks.append(f"### {prefix}{label}")
            chunks.append(f"-{old_val or '<空>'}")
            chunks.append(f"+{new_val or '<空>'}")
            chunks.append("")

        _symbol_diff_chunks(chunks, prefix, old_impl, new_impl)

        code_diff = _unified_diff(
            str(old_impl.get("code") or ""),
            str(new_impl.get("code") or ""),
            f"{prefix}代码",
            context,
        )
        if code_diff:
            chunks.append(f"### {prefix}代码")
            chunks.extend(code_diff)
            chunks.append("")

    return chunks


def build_revision_diff(
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    *,
    context: int = 3,
) -> str:
    """生成可直接显示的统一差异文本。

    ``before`` 为 ``None`` 表示"新增"; ``after`` 为 ``None`` 表示"删除"。
    自动识别模块 / 空间 / 函数体三种快照形态。
    """
    chunks: List[str] = []
    if before is None and after is None:
        return ""

    kind = kind_of_snapshot(after if after is not None else before)
    entity = {"space": "空间", "function": "函数体"}.get(kind, "条目")

    if before is None:
        chunks.append(f"+++ 新增{entity} +++")
        before = {}
    if after is None:
        chunks.append(f"--- 删除{entity} ---")
        after = {}

    if kind == "space":
        chunks.extend(_space_diff(before, after, context))
        return "\n".join(chunks).strip()
    if kind == "function":
        chunks.extend(_function_diff(before, after, context))
        return "\n".join(chunks).strip()

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


def _count_line_changes(old_code: str, new_code: str) -> Tuple[int, int]:
    """返回 ``(+新增行, -删除行)``。"""
    added_n = removed_n = 0
    for line in difflib.unified_diff(
        old_code.splitlines(), new_code.splitlines(), lineterm="", n=0
    ):
        if line.startswith("+") and not line.startswith("+++"):
            added_n += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed_n += 1
    return added_n, removed_n


def _summarize_space(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    bits: List[str] = []
    for field_name, label in (("name", "空间名称"), ("description", "描述"),
                              ("prerequisites", "前置要求"), ("status", "状态")):
        if str(before.get(field_name) or "") != str(after.get(field_name) or ""):
            bits.append(label)

    added, removed = diff_tag_sets(before.get("tags") or [], after.get("tags") or [])
    if added or removed:
        parts = [f"+{len(added)}"] if added else []
        if removed:
            parts.append(f"-{len(removed)}")
        bits.append("标签 " + "/".join(parts))

    old_files = {f.get("path"): f for f in (before.get("files") or []) if isinstance(f, dict)}
    new_files = {f.get("path"): f for f in (after.get("files") or []) if isinstance(f, dict)}
    for path in sorted(set(new_files) - set(old_files)):
        bits.append(f"新增文件 {path}")
    for path in sorted(set(old_files) - set(new_files)):
        bits.append(f"删除文件 {path}")
    for path in sorted(set(old_files) & set(new_files)):
        old_item, new_item = old_files[path], new_files[path]
        if bool(old_item.get("binary")) or bool(new_item.get("binary")):
            # 二进制文件只报"内容/大小变了", 不参与行级统计
            if str(old_item.get("data") or "") != str(new_item.get("data") or ""):
                bits.append(
                    f"{path} 二进制内容变更 "
                    f"({int(old_item.get('size') or 0)} → {int(new_item.get('size') or 0)} 字节)"
                )
            continue
        if str(old_item.get("content") or "") != str(new_item.get("content") or ""):
            plus, minus = _count_line_changes(
                str(old_item.get("content") or ""), str(new_item.get("content") or "")
            )
            bits.append(f"{path} +{plus}/-{minus}")
    return ", ".join(bits) if bits else "无实质变更"


def _summarize_function(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    bits: List[str] = []
    for field_name, label in (("name", "函数名"), ("description", "描述"),
                              ("prerequisites", "前置要求"), ("status", "状态")):
        if str(before.get(field_name) or "") != str(after.get(field_name) or ""):
            bits.append(label)

    added, removed = diff_tag_sets(before.get("tags") or [], after.get("tags") or [])
    if added or removed:
        parts = [f"+{len(added)}"] if added else []
        if removed:
            parts.append(f"-{len(removed)}")
        bits.append("标签 " + "/".join(parts))

    old_impls = _implementation_index(before)
    new_impls = _implementation_index(after)

    for impl_id in new_impls.keys() - old_impls.keys():
        bits.append(f"新增 {new_impls[impl_id].get('language')} 实现")
    for impl_id in old_impls.keys() - new_impls.keys():
        bits.append(f"删除 {old_impls[impl_id].get('language')} 实现")

    for impl_id in sorted(old_impls.keys() & new_impls.keys()):
        old_impl, new_impl = old_impls[impl_id], new_impls[impl_id]
        language = str(new_impl.get("language") or old_impl.get("language") or "?")
        for field_name, label in (("language", "语言"), ("signature", "签名"),
                                  ("prerequisites", "前置要求"), ("notes", "实现说明")):
            if str(old_impl.get(field_name) or "") != str(new_impl.get(field_name) or ""):
                bits.append(f"{language} {label}")

        old_symbols = _symbol_index(old_impl)
        new_symbols = _symbol_index(new_impl)
        if new_symbols.keys() - old_symbols.keys():
            bits.append(f"{language} 新增变量 {len(new_symbols.keys() - old_symbols.keys())}")
        if old_symbols.keys() - new_symbols.keys():
            bits.append(f"{language} 删除变量 {len(old_symbols.keys() - new_symbols.keys())}")
        filled = 0
        for key in old_symbols.keys() & new_symbols.keys():
            if str(old_symbols[key].get("meaning") or "") != str(new_symbols[key].get("meaning") or ""):
                filled += 1
        if filled:
            bits.append(f"{language} {filled} 个变量含义变更")

        old_code = str(old_impl.get("code") or "")
        new_code = str(new_impl.get("code") or "")
        if old_code != new_code:
            plus, minus = _count_line_changes(old_code, new_code)
            bits.append(f"{language} 代码 +{plus}/-{minus}")

    return ", ".join(bits) if bits else "无实质变更"


def summarize_changes(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> str:
    """生成一句话变更摘要, 形如 ``标题, 描述, Python 代码 +12/-3, 标签 +2``。"""
    if before is None:
        return "新建条目"
    if after is None:
        return "删除条目"

    kind = kind_of_snapshot(after if after else before)
    if kind == "space":
        return _summarize_space(before, after)
    if kind == "function":
        return _summarize_function(before, after)

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
        entry: Any,
        action: str = "update",
        summary: str = "",
        author: str = "local",
        *,
        snapshot: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[Revision]:
        """为 ``entry`` 追加一条修订 (模块 / 空间 / 函数体皆可)。

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
            kind=str(getattr(entry, "kind", "module")),
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
