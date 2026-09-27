"""函数体 (Function): 某一个语言里的一个具体函数, 外加"变量含义表"与前置要求.

与"模块"的区别
--------------
* 模块 = 一个问题 + 多语言实现 (横向对比不同语言的解法);
* 函数体 = **一个**语言里的**一个**函数, 关注点在于把它讲清楚:
  自动检测出参数/局部变量/字段/常量, 并要求用户逐个填写含义。

因此函数体有独立的 ``prerequisites`` (它自己的前置要求), 不与模块共用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .languages import detect_language_from_filename, get_language, normalize_language
from .models import (
    STATUS_LABELS,
    STATUS_ORDER,
    new_id,
    normalize_tags,
    utcnow,
)

# 符号种类 -> 中文标签
SYMBOL_KIND_LABELS: Dict[str, str] = {
    "parameter": "参数",
    "local": "局部变量",
    "field": "成员/字段",
    "global": "全局变量",
    "constant": "常量",
    "return": "返回值",
}

# 这几种是函数对外契约的一部分, 含义必须填写
REQUIRED_SYMBOL_KINDS = frozenset({"parameter", "field", "return"})

SYMBOL_KIND_ORDER: tuple = ("parameter", "return", "field", "global", "constant", "local")


def symbol_kind_label(kind: str) -> str:
    return SYMBOL_KIND_LABELS.get(kind, kind)


# --------------------------------------------------------------------------------------
# Symbol
# --------------------------------------------------------------------------------------


@dataclass
class Symbol:
    """函数里的一个变量/参数/返回值, 以及**用户填写的含义**。"""

    name: str = ""
    kind: str = "local"
    type: str = ""
    meaning: str = ""            # 用户填写的含义 —— 这是本功能的核心要求
    detail: str = ""
    default: str = ""

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        self.kind = str(self.kind or "local").strip() or "local"
        self.type = str(self.type or "").strip()
        self.meaning = str(self.meaning or "").strip()
        self.detail = str(self.detail or "").strip()
        self.default = str(self.default or "").strip()

    @property
    def kind_label(self) -> str:
        return symbol_kind_label(self.kind)

    @property
    def needs_meaning(self) -> bool:
        """含义是否必填 (参数/字段/返回值必填, 局部变量可留空)。"""
        return self.kind in REQUIRED_SYMBOL_KINDS

    @property
    def is_blank(self) -> bool:
        return not self.meaning

    @property
    def signature_hint(self) -> str:
        """``name: type = default`` 形式的简短签名。"""
        text = self.name
        if self.type:
            text += f": {self.type}"
        if self.default:
            text += f" = {self.default}"
        return text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "type": self.type,
            "meaning": self.meaning,
            "detail": self.detail,
            "default": self.default,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Symbol":
        return cls(
            name=str(data.get("name") or ""),
            kind=str(data.get("kind") or "local"),
            type=str(data.get("type") or ""),
            meaning=str(data.get("meaning") or ""),
            detail=str(data.get("detail") or ""),
            default=str(data.get("default") or ""),
        )

    def clone(self) -> "Symbol":
        return Symbol.from_dict(self.to_dict())

    @staticmethod
    def key_of(name: str, kind: str) -> str:
        return f"{kind}:{(name or '').strip()}"


# --------------------------------------------------------------------------------------
# Function
# --------------------------------------------------------------------------------------


@dataclass
class Function:
    """一个具体语言的函数体。"""

    name: str = ""
    language: str = "python"
    signature: str = ""
    code: str = ""
    description: str = ""
    prerequisites: str = ""       # 函数体**自己的**前置要求
    symbols: List[Symbol] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    status: str = "planned"
    favorite: bool = False
    id: str = field(default_factory=lambda: new_id("fn_"))
    created_at: float = field(default_factory=utcnow)
    updated_at: float = field(default_factory=utcnow)
    version: int = 1
    deleted: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.language = normalize_language(self.language)
        self.tags = normalize_tags(self.tags)
        if self.status not in STATUS_ORDER:
            self.status = "planned"
        self.symbols = list(self.symbols)

    # ---- 与模块/空间统一的接口 ----
    kind = "function"

    @property
    def kind_label(self) -> str:
        return "函数体"

    @property
    def display_title(self) -> str:
        if self.name.strip():
            return self.name.strip()
        return "未命名函数"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def badge_count(self) -> int:
        return len(self.symbols)

    @property
    def badge_label(self) -> str:
        return f"{len(self.symbols)} 变量"

    @property
    def languages(self) -> List[str]:
        return [self.language]

    @property
    def total_lines(self) -> int:
        return len(self.code.splitlines()) if self.code else 0

    @property
    def language_name(self) -> str:
        return get_language(self.language).name

    @property
    def search_blob(self) -> str:
        parts = [self.name, self.description, self.prerequisites, self.signature, " ".join(self.tags)]
        for symbol in self.symbols:
            parts.append(symbol.name)
            parts.append(symbol.type)
            parts.append(symbol.meaning)
        parts.append(self.code)
        return "\n".join(parts).casefold()

    # ---- 变量含义 ----
    @property
    def symbols_missing_meaning(self) -> List[Symbol]:
        """还没填含义的符号 (必填的排在前面)。"""
        blank = [s for s in self.symbols if s.is_blank]
        return sorted(blank, key=lambda s: (not s.needs_meaning, s.name))

    @property
    def required_symbols_missing_meaning(self) -> List[Symbol]:
        return [s for s in self.symbols if s.needs_meaning and s.is_blank]

    @property
    def has_unresolved_symbols(self) -> bool:
        return bool(self.required_symbols_missing_meaning)

    @property
    def symbols_by_kind(self) -> Dict[str, List[Symbol]]:
        grouped: Dict[str, List[Symbol]] = {kind: [] for kind in SYMBOL_KIND_ORDER}
        for symbol in self.symbols:
            grouped.setdefault(symbol.kind, []).append(symbol)
        return grouped

    def get_symbol(self, key: str) -> Optional[Symbol]:
        for symbol in self.symbols:
            if Symbol.key_of(symbol.name, symbol.kind) == key:
                return symbol
        return None

    def set_symbols(self, symbols: Iterable[Symbol]) -> None:
        """整体替换符号表, 并按 (种类, 名称) 去重。"""
        seen: set = set()
        ordered: List[Symbol] = []
        for symbol in symbols:
            key = Symbol.key_of(symbol.name, symbol.kind)
            if not symbol.name or key in seen:
                continue
            seen.add(key)
            ordered.append(symbol)
        self.symbols = ordered

    def apply_detected(self, detected: Iterable[Any]) -> int:
        """把检测结果并入符号表, **保留用户已填写的含义**。

        ``detected`` 是 :mod:`codemethod.core.symbols` 产出的对象列表 (鸭子类型:
        需要 ``name`` / ``kind`` / ``type`` / ``detail`` / ``default`` 属性)。
        返回新增的符号数量。
        """
        # 参数被重新赋值时 (Python 的 `base %= m`、Rust 的 `let mut` 之类) 检测器会
        # 同时报出同名局部量。那不是新声明, 在表里重复出现只会制造噪声, 所以过滤掉。
        parameter_names = {
            str(getattr(item, "name", "")).strip()
            for item in detected
            if getattr(item, "kind", "") == "parameter"
        }

        existing: Dict[str, Symbol] = {
            Symbol.key_of(s.name, s.kind): s for s in self.symbols
        }
        merged: List[Symbol] = []
        added = 0
        for item in detected:
            name = str(getattr(item, "name", "")).strip()
            kind = getattr(item, "kind", "local")
            if kind == "local" and name in parameter_names:
                continue
            key = Symbol.key_of(name, kind)
            if key in existing:
                symbol = existing.pop(key)
                # 刷新从代码里推断出来的信息, 但**绝不动含义**
                symbol.type = getattr(item, "type", "") or symbol.type
                symbol.detail = getattr(item, "detail", "") or symbol.detail
                symbol.default = getattr(item, "default", "") or symbol.default
                merged.append(symbol)
            else:
                merged.append(
                    Symbol(
                        name=name,
                        kind=kind,
                        type=getattr(item, "type", ""),
                        meaning=getattr(item, "meaning", ""),
                        detail=getattr(item, "detail", ""),
                        default=getattr(item, "default", ""),
                    )
                )
                added += 1
        # 代码里已经找不到的符号仍然保留 —— 用户可能刻意标注过
        merged.extend(existing.values())
        merged.sort(key=lambda s: (SYMBOL_KIND_ORDER.index(s.kind) if s.kind in SYMBOL_KIND_ORDER else 99, s.name))
        self.set_symbols(merged)
        return added

    # ---- 变更 ----
    def touch(self) -> None:
        self.updated_at = utcnow()
        self.version += 1

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "signature": self.signature,
            "code": self.code,
            "description": self.description,
            "prerequisites": self.prerequisites,
            "symbols": [s.to_dict() for s in self.symbols],
            "tags": list(self.tags),
            "status": self.status,
            "favorite": self.favorite,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "deleted": self.deleted,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Function":
        symbols = [
            Symbol.from_dict(item)
            for item in (data.get("symbols") or [])
            if isinstance(item, dict)
        ]
        language = data.get("language")
        return cls(
            id=str(data.get("id") or new_id("fn_")),
            name=str(data.get("name") or ""),
            language=normalize_language(language) if language else "plaintext",
            signature=str(data.get("signature") or ""),
            code=str(data.get("code") or ""),
            description=str(data.get("description") or ""),
            prerequisites=str(data.get("prerequisites") or ""),
            symbols=symbols,
            tags=list(data.get("tags") or []),
            status=str(data.get("status") or "planned"),
            favorite=bool(data.get("favorite", False)),
            created_at=float(data.get("created_at") or utcnow()),
            updated_at=float(data.get("updated_at") or utcnow()),
            version=int(data.get("version") or 1),
            deleted=bool(data.get("deleted", False)),
            extra=dict(data.get("extra") or {}),
        )

    def clone(self) -> "Function":
        return Function.from_dict(self.to_dict())

    def suggest_language_from_code(self) -> None:
        """没有指定语言时, 从代码内容里猜一个 (仅在语言仍是 plaintext 时生效)。"""
        if self.language != "plaintext":
            return
        signature = self.signature or self.code[:400]
        if "def " in signature and ":" in signature:
            self.language = "python"
        elif "package " in signature and "func " in signature:
            self.language = "go"
        elif "fn " in signature and "->" in signature:
            self.language = "rust"
        elif "public " in signature or "class " in signature:
            self.language = "java"
        elif signature.lstrip().startswith("<?php"):
            self.language = "php"
        elif detect_language_from_filename(self.name):
            self.language = detect_language_from_filename(self.name)


__all__ = [
    "Symbol",
    "Function",
    "SYMBOL_KIND_LABELS",
    "SYMBOL_KIND_ORDER",
    "REQUIRED_SYMBOL_KINDS",
    "symbol_kind_label",
]
