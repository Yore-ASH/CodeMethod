"""变量/符号声明探测: 正则 + 启发式的轻量识别器.

设计目标
--------
这里**不是**完整语法分析器, 只求"足够好到可以提示用户": 从一段代码里猜出参数、
局部变量、字段、全局量、常量与返回值, 让界面能拿这些结果去追问用户
「这个变量的含义是什么?」。

因此本模块刻意做到:
- 只依赖标准库与语言注册表 (:mod:`codemethod.core.languages`), 不引入 Qt;
- 任何输入都不抛异常 (畸形代码、二进制片段一律退化为空结果);
- 结果顺序稳定, 便于界面做增量化合并 (见 :func:`merge_symbols`)。

流水线
------
1. :func:`mask_code` 先按语言规格把注释与字符串替换成等长空格 —— 偏移量与原文完全
   一致, 因此后续正则可以照常跑, 行号也能直接从掩码后的偏移换算;
2. 各语言的 :func:`_detect_xxx` 在掩码文本上跑规则, 产出候选符号;
3. :func:`_organize` 去重并按固定类别顺序排列。
"""

from __future__ import annotations

import bisect
import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .languages import get_language, normalize_language

# --------------------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------------------

# 类别顺序: 结果排序、界面分组都用它
KIND_ORDER: Sequence[str] = ("parameter", "return", "field", "global", "constant", "local")

KIND_LABELS: Dict[str, str] = {
    "parameter": "参数",
    "return": "返回值",
    "field": "字段",
    "global": "全局变量",
    "constant": "常量",
    "local": "局部变量",
}


@dataclass
class DetectedSymbol:
    """一个被启发式识别出来的符号.

    ``meaning`` 不由探测产生, 而是留给用户或上层界面填写;
    :func:`merge_symbols` 在重新探测时会把它原样保留下来。
    """

    name: str
    kind: str
    type: str = ""
    detail: str = ""
    default: str = ""
    meaning: str = ""


# 支持的语言 (其余语言退化为通用规则, 仍然可用)
_LANGUAGE_IDS: Tuple[str, ...] = (
    "python",
    "c",
    "cpp",
    "go",
    "java",
    "php",
    "rust",
    "javascript",
    "typescript",
    "csharp",
    "kotlin",
    "swift",
    "ruby",
    "lua",
    "bash",
    "sql",
)

# 这些词出现在变量名位置时几乎一定是关键字/控制流, 直接丢弃。
# 注意不要包含 return —— 返回值符号本身就叫这个名字。
_NOISE_WORDS = frozenset(
    """
    and as assert async await break case catch class const continue def default del delete
    do done elif else end enum except export extends false final finally fn for foreach
    from func function global goto if implements import in instanceof interface is lambda
    let match mod module new nil none not null or package pass print private protected
    public raise readonly repeat require self static struct super switch then this
    throw throws trait true try type typedef typeof unless until use var void when where
    while with yield
    """.split()
)

# 默认值截断长度
_DEFAULT_LIMIT = 60

# 行号换算: 第 N 行
_LINE_TEMPLATE = "第 {} 行"


# --------------------------------------------------------------------------------------
# 注释/字符串掩码
# --------------------------------------------------------------------------------------


def mask_code(language: str, code: str) -> str:
    """把注释与字符串内容替换为空格, 长度与原文逐字符一致.

    所有正则都在掩码文本上运行, 这样"字符串里出现的变量名"或"注释掉的声明"
    就不会被误报, 同时偏移量不变 —— 行号可以直接换算。
    """
    if not code:
        return ""
    if not isinstance(code, str):  # 上层可能从 JSON 里拿到非字符串
        code = str(code)

    spec = get_language(language)
    line_comments = tuple(spec.all_line_comments or ())
    block = spec.block_comment
    block_open, block_close = block if block else ("", "")
    block_is_word = bool(block_open) and block_open.isalpha()
    strings = tuple(spec.string_delimiters or ())
    triples = tuple(spec.triple_string_delimiters or ())
    char_delim = spec.char_delimiter or ""
    raw_prefixes = tuple(p for p in (spec.raw_string_prefixes or ()) if p)
    nested = bool(spec.nested_block_comment)

    chars = list(code)
    # 掩码初值是原文的副本: blank() 负责把注释/字符串那一段"擦"成空格
    mask = list(chars)
    total = len(chars)
    index = 0

    def starts_at(token: str, position: int) -> bool:
        """判断 ``token`` 是否正好出现在 ``position`` 处 (列表没有 startswith)。"""
        if not token or position < 0 or position + len(token) > total:
            return False
        return chars[position : position + len(token)] == list(token)

    def blank(start: int, stop: int) -> None:
        """把 ``[start, stop)`` 区间的内容置空, 但保留换行符本身。"""
        for pos in range(max(start, 0), min(stop, total)):
            if chars[pos] != "\n":
                mask[pos] = " "

    def scan_quoted(start: int, marker: str, stop_at_newline: bool = True) -> Tuple[int, bool]:
        """从 ``start`` (引号起始处) 出发找结束引号.

        返回 ``(结束位置, 是否闭合)``; 未闭合时结束位置停在换行之前,
        避免把后面整段代码都当成字符串吞掉。多行字符串 (三引号) 传
        ``stop_at_newline=False`` 允许跨行。
        """
        length = len(marker)
        pos = start + length
        while pos < total:
            char = chars[pos]
            if char == "\\" and length == 1:
                pos += 2
                continue
            if char == "\n" and stop_at_newline:
                return pos, False
            if starts_at(marker, pos):
                return pos + length, True
            pos += 1
        return total, True

    while index < total:
        char = chars[index]

        # 反斜杠转义: 跳过下一个字符, 防止把 \" 当成字符串起点
        if char == "\\":
            index += 2
            continue

        # 行注释
        matched_line = False
        for marker in line_comments:
            if starts_at(marker, index):
                stop = code.find("\n", index)
                blank(index, total if stop < 0 else stop)
                index = total if stop < 0 else stop + 1
                matched_line = True
                break
        if matched_line:
            continue

        # 块注释 (Rust/Swift 允许嵌套)
        if block_open and starts_at(block_open, index):
            # 只有由字母组成的标记 (例如 Ruby 的 =begin) 才要求行首, 其余位置均视为注释
            at_line_start = index == 0 or chars[index - 1] == "\n"
            if not block_is_word or at_line_start:
                depth = 1
                cursor = index + len(block_open)
                while cursor < total and depth > 0:
                    if nested and starts_at(block_open, cursor):
                        depth += 1
                        cursor += len(block_open)
                    elif starts_at(block_close, cursor):
                        depth -= 1
                        cursor += len(block_close)
                    else:
                        cursor += 1
                blank(index, cursor)
                index = max(cursor, index + len(block_open))
                continue

        # 原始字符串前缀 (f"..."、r'...'、b"..."、@$"..." 之类)
        # 必须真的跟着引号才算前缀, 否则 `def f(x)`、`return x` 里的标识符 f / r
        # 会被当成 f-string / raw string 的起点, 把后面整段代码吞掉
        handled = False
        for prefix in raw_prefixes:
            quote_at = index + len(prefix)
            if not starts_at(prefix, index) or quote_at >= total:
                continue
            following = chars[quote_at]
            plausible = following in strings or any(
                starts_at(triple, quote_at) for triple in triples
            )
            if not plausible:
                continue
            stop, closed = scan_quoted(quote_at, following)
            blank(index, stop)
            index = stop if closed else index + 1
            handled = True
            break
        if handled:
            continue

        # 三引号字符串 = 一次出现连续三个及以上引号 (Python/Swift/Kotlin 的多行字符串)
        tripled = next((q for q in triples if starts_at(q, index)), "")
        if tripled:
            marker = tripled[0] * 3
            # 三引号允许跨行, 所以结束标记也必须整三个引号一起匹配
            stop, closed = scan_quoted(index, marker, stop_at_newline=False)
            blank(index, stop)
            index = stop if closed else index + 1
            continue

        # 普通字符串
        if char in strings:
            stop, closed = scan_quoted(index, char)
            blank(index, stop)
            index = stop if closed else index + 1
            continue

        # 字符字面量 (C 系列的 'x' '\n') —— 单引号是字符串定界符时已被上面处理。
        # 必须有闭合引号才认, 否则 Rust 的生命周期标注 `&'a str` 会被当成未闭合字符串
        if char_delim and char == char_delim:
            stop, closed = scan_quoted(index, char)
            if closed:
                blank(index, stop)
                index = stop
                continue

        index += 1

    return "".join(mask)


# --------------------------------------------------------------------------------------
# 通用小工具
# --------------------------------------------------------------------------------------


class _Ctx:
    """一次探测过程中的共享上下文 (原文 + 掩码 + 行索引)。"""

    def __init__(self, language: str, code: str) -> None:
        self.language = language
        self.code = code
        self.masked = mask_code(language, code)
        self._starts = [0]
        for position, char in enumerate(self.masked):
            if char == "\n":
                self._starts.append(position + 1)

    def line_of(self, offset: int) -> int:
        """偏移量 -> 1 起算的行号。"""
        return bisect.bisect_right(self._starts, max(offset, 0))

    def line_text(self, line: int) -> str:
        """取掩码后的整行文本 (行号 1 起算)。"""
        if line < 1 or line > len(self._starts):
            return ""
        start = self._starts[line - 1]
        stop = self.masked.find("\n", start)
        if stop < 0:
            stop = len(self.masked)
        return self.masked[start:stop]

    def raw_line_text(self, line: int) -> str:
        """取原文的整行文本 (用于保留原始默认值写法)。"""
        if line < 1 or line > len(self._starts):
            return ""
        start = self._starts[line - 1]
        stop = self.code.find("\n", start)
        if stop < 0:
            stop = len(self.code)
        return self.code[start:stop]

    def indent_of(self, line: int) -> int:
        """该行的缩进宽度。"""
        text = self.line_text(line)
        return len(text) - len(text.lstrip())

    @property
    def line_count(self) -> int:
        return len(self._starts)


def _clip(text: str, limit: int = _DEFAULT_LIMIT) -> str:
    """默认值/初始值截断, 去掉换行便于单行展示。"""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _clean_name(name: str) -> str:
    """清理标识符: 去掉修饰符与前缀, 例如 PHP 的 ``$``、Python 的 ``*``。"""
    return str(name or "").strip().lstrip("*&$@:").strip()


def _is_meaningful(name: str) -> bool:
    """过滤掉无意义的占位名。"""
    return bool(name) and name != "_" and not name.isspace()


def _looks_type_like(token: str) -> bool:
    """粗略判断一个词像不像类型名 (用于把 ``a: int`` 里的类型挑出来)。"""
    token = token.strip()
    if not token:
        return False
    if token[0].isupper():
        return True
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\[\])?[?*&!]?", token)) and token[0].islower()


def _skip_type_token(token: str) -> bool:
    """模块名/包名一类的单词不应算作类型。"""
    return token.lower() in {"std", "io", "fmt", "os", "sys", "net", "http", "re", "utils", "collections"}


def _find_matching(text: str, start: int, open_char: str, close_char: str) -> int:
    """返回与 ``text[start]`` 配对的右括号下标, 找不到返回 -1。"""
    depth = 0
    for position in range(start, len(text)):
        char = text[position]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return position
            if depth < 0:
                return -1
    return -1


def _split_top(text: str, delimiter: str = ",") -> List[str]:
    """按顶层分隔符切分 (括号/引号内的分隔符不算)。

    掩码已经把字符串变成空格, 但仍要处理 ``[int, str]``、``dict[str, int]``
    这类容器类型里的逗号。
    """
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    closers = set(pairs.values())
    for char in text:
        if char in pairs:
            depth += 1
        elif char in closers:
            depth = max(depth - 1, 0)
        if char == delimiter and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts]


def _refine_type(candidate: str) -> str:
    """把候选类型文本压缩成可展示的类型名。"""
    text = " ".join(str(candidate or "").split())
    for marker in ("=", ";", ")"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip().strip(",").rstrip(":").strip()


def _looks_upper(name: str) -> bool:
    """全大写 (允许下划线/数字) 即视为常量命名。"""
    return bool(name) and name.upper() == name and any(ch.isalpha() for ch in name)


def _make(
    ctx: _Ctx,
    name: str,
    kind: str,
    *,
    type: str = "",
    line: int = 0,
    offset: int = 0,
    detail: str = "",
    default: str = "",
    destructured: bool = False,
    uppercase_constant: bool = False,
) -> Optional[DetectedSymbol]:
    """构造一个符号, 顺手完成名称清洗、无意义名过滤与大写常量归类。"""
    raw = str(name or "").strip()
    # bash 的位置参数就叫 $1, 前面的 $ 是名字的一部分而不是前缀
    keep_prefix = raw.startswith("$") and raw[1:].isdigit()
    cleaned = raw if keep_prefix else _clean_name(raw)
    if not _is_meaningful(cleaned) or (not keep_prefix and cleaned.lower() in _NOISE_WORDS):
        return None
    # 参数名是 self/cls/this 的一律跳过: 它们不是"需要用户解释含义"的变量
    if kind == "parameter" and cleaned.lower() in {"self", "cls", "this", "super"}:
        return None
    if uppercase_constant and kind in {"local", "global"} and _looks_upper(cleaned):
        kind = "constant"

    if not detail:
        resolved = line or (ctx.line_of(offset) if offset else 0)
        detail = _LINE_TEMPLATE.format(resolved) if resolved else ""
    if destructured:
        detail = f"{detail} (解构)" if detail else "(解构)"

    return DetectedSymbol(
        name=cleaned,
        kind=kind,
        type=_refine_type(type),
        detail=detail,
        default=_clip(default),
    )


def _add(
    bucket: List[DetectedSymbol],
    ctx: _Ctx,
    name: str,
    kind: str,
    **kwargs: object,
) -> None:
    """``_make`` 的空安全包装: 过滤掉 None 后追加到桶里。"""
    symbol = _make(ctx, name, kind, **kwargs)  # type: ignore[arg-type]
    if symbol is not None:
        bucket.append(symbol)


def _organize(symbols: Sequence[DetectedSymbol]) -> List[DetectedSymbol]:
    """按 symbol_key 去重并排序.

    去重保留首次出现; 后出现的同键符号只用来补齐首个缺失的类型。
    排序: 参数 -> 返回值 -> 字段 -> 全局 -> 常量 -> 局部 (同类保持出现顺序)。
    """
    order = {kind: index for index, kind in enumerate(KIND_ORDER)}
    seen: Dict[str, DetectedSymbol] = {}
    result: List[DetectedSymbol] = []
    for symbol in symbols:
        key = symbol_key("", symbol.name, symbol.kind)
        previous = seen.get(key)
        if previous is None:
            seen[key] = symbol
            result.append(symbol)
            continue
        if symbol.type and not previous.type:
            previous.type = symbol.type
        if symbol.default and not previous.default:
            previous.default = symbol.default
    result.sort(key=lambda item: order.get(item.kind, len(KIND_ORDER)))
    return result


def _line_span(lines: Sequence[int]) -> Tuple[int, int]:
    """取一组行号的包围区间。"""
    if not lines:
        return 0, 0
    return min(lines), max(lines)


def _python_function_span(ctx: _Ctx, def_line: int) -> Tuple[int, int]:
    """粗略估算 Python 函数体范围: 直到缩进退回到函数定义那一行为止。"""
    base = ctx.indent_of(def_line)
    last = def_line
    for line in range(def_line + 1, ctx.line_count + 1):
        text = ctx.line_text(line)
        if not text.strip():
            continue
        if ctx.indent_of(line) <= base:
            break
        last = line
    return def_line, max(last, def_line)


# --------------------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------------------

_PY_DEF_RE = re.compile(r"^(?P<indent>[ \t]*)def\s+(?P<name>[A-Za-z_]\w*)\s*\(", re.MULTILINE)
_PY_ASSIGN_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<target>[^=\n]+?)[ \t]*(?P<ann>:[ \t]*[^=\n]+?)?[ \t]*=(?!=)[ \t]*(?P<value>[^\n]+)$",
    re.MULTILINE,
)
# 注解里的类型只允许类型表达式字符, 否则 "x: int" 会把下一行的 def 也吞进来
_PY_TYPE = r"[A-Za-z_][\w\.\[\], \|<>]*"
_PY_ANN_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<name>[A-Za-z_]\w*)[ \t]*:[ \t]*(?P<type>" + _PY_TYPE + r"?)[ \t]*=(?!=)[ \t]*(?P<value>[^\n]+)$",
    re.MULTILINE,
)
_PY_SELF_RE = re.compile(r"^(?P<indent>[ \t]*)(?:self|cls)\.(?P<name>[A-Za-z_]\w*)[ \t]*=(?!=)")
_PY_GLOBAL_RE = re.compile(r"^(?P<indent>[ \t]*)global[ \t]+(?P<names>[A-Za-z_][\w,\s]*)$", re.MULTILINE)
_PY_ANN_ONLY_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<name>[A-Za-z_]\w*)[ \t]*:[ \t]*(?P<type>" + _PY_TYPE + r")[ \t]*(?:#.*)?$",
    re.MULTILINE,
)


def _python_parameters(params: str, raw_params: str = "") -> List[Tuple[str, str, str]]:
    """解析 Python 形参串, 返回 ``[(名字, 类型, 默认值), ...]``.

    ``raw_params`` 是同一段形参的未掩码文本, 用来还原字符串默认值 (``b = "x"``)。
    """
    found: List[Tuple[str, str, str]] = []
    masked_items = _split_top(params)
    raw_items = _split_top(raw_params) if raw_params else []
    for index, raw in enumerate(masked_items):
        raw_item = raw_items[index] if index < len(raw_items) else raw
        text = raw.strip()
        if not text or text in {"/", "*"}:
            continue
        default = ""
        head = text
        # 顶层 '=' (== 与 >= 之类不算) 拆出默认值; 默认值取原文以便保留引号内容
        depth = 0
        raw_text = raw_item.strip()
        for position, char in enumerate(text):
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth = max(depth - 1, 0)
            elif char == "=" and depth == 0 and text[position - 1 : position] not in "=!<>" and text[position + 1 : position + 2] != "=":
                head, default = text[:position], text[position + 1 :]
                if position < len(raw_text):
                    default = raw_text[position + 1 :]
                break
        annotation = ""
        if ":" in head:
            # 只认第一个顶层冒号 (dict[str, int] 里的冒号在括号内)
            depth = 0
            for position, char in enumerate(head):
                if char in "[({":
                    depth += 1
                elif char in "])}":
                    depth = max(depth - 1, 0)
                elif char == ":" and depth == 0:
                    head, annotation = head[:position], head[position + 1 :]
                    break
        name = _clean_name(head)
        if not name:
            continue
        found.append((name, _refine_type(annotation), _clip(default)))
    return found


def _detect_python(ctx: _Ctx, function_name: str) -> List[DetectedSymbol]:
    """Python: def 形参 / 注解 / 解构赋值 / self 字段 / global / 常量 / return。"""
    symbols: List[DetectedSymbol] = []
    matched_lines: set = set()
    definitions: List[Tuple[str, int, str, int, int]] = []  # 名字, 行, 返回注解, 起, 止

    for match in _PY_DEF_RE.finditer(ctx.masked):
        name = match.group("name")
        open_index = match.end() - 1
        close_index = _find_matching(ctx.masked, open_index, "(", ")")
        if close_index < 0:
            continue
        tail_end = ctx.masked.find("\n", close_index)
        if tail_end < 0:
            tail_end = len(ctx.masked)
        tail = ctx.masked[close_index + 1 : tail_end]
        return_annotation = ""
        if "->" in tail:
            return_annotation = _refine_type(tail.split("->", 1)[1])
        def_line = ctx.line_of(match.start())
        start_line, end_line = _python_function_span(ctx, def_line)
        definitions.append((name, def_line, return_annotation, start_line, end_line))
        matched_lines.add(def_line)

        keep = not function_name or function_name == name
        if not keep:
            continue

        for param_name, param_type, param_default in _python_parameters(
            ctx.masked[open_index + 1 : close_index],
            _raw_span(ctx.masked, ctx.code, open_index + 1, close_index),
        ):
            _add(
                symbols,
                ctx,
                param_name,
                "parameter",
                type=param_type,
                detail="函数签名",
                default=param_default,
            )

        has_return = bool(return_annotation)
        for line in range(start_line, end_line + 1):
            text = ctx.line_text(line)
            stripped = text.strip()
            if not stripped:
                continue
            matched_lines.add(line)
            if stripped.startswith("return"):
                # 返回符号只在函数有返回注解时给出类型; 无注解时仍然列出, 便于用户补充
                _add(
                    symbols,
                    ctx,
                    "return",
                    "return",
                    type=return_annotation,
                    line=line,
                )
                continue
            if stripped.startswith(("def ", "class ", "elif ", "else:", "try:", "except ", "finally:", "with ")):
                continue
            self_match = _PY_SELF_RE.match(text)
            if self_match:
                value = ""
                if "=" in text:
                    value = text.split("=", 1)[1]
                _add(
                    symbols,
                    ctx,
                    self_match.group("name"),
                    "field",
                    line=line,
                    default=value,
                )
                continue
            if stripped.startswith("global ") or stripped.startswith("nonlocal "):
                rest = stripped.split(None, 1)[1] if " " in stripped else ""
                for name in rest.replace(",", " ").split():
                    _add(symbols, ctx, name, "global", line=line)
                continue
            if stripped.startswith(
                ("if ", "for ", "while ", "return", "import ", "from ", "assert ", "raise ", "match ", "case ")
            ):
                continue
            assign = _PY_ASSIGN_RE.match(text)
            if not assign:
                continue
            target = assign.group("target").strip().rstrip(",").strip()
            value = assign.group("value").strip()
            annotation = _refine_type(assign.group("ann") or "")
            if target.startswith("self.") or target.startswith("cls.") or target.startswith("self["):
                continue
            if "(" in target or "." in target or "[" in target:
                continue
            # 类型注解写成独立一行时 (PEP 526 的裸注解), 类型从注解行取
            destructured = "," in target
            names = [item for item in re.findall(r"[A-Za-z_]\w*", target)]
            if not names:
                continue
            value_type = ""
            if value.endswith(")") and "(" in value:
                value_type = _refine_type(value.split("(", 1)[0])
            for name in names:
                _add(
                    symbols,
                    ctx,
                    name,
                    "local",
                    type=annotation or value_type,
                    line=line,
                    default=value,
                    destructured=destructured,
                    uppercase_constant=True,
                )
        continue

    # 注解赋值: x: int = 1 (可能缩进在函数内, 也可能在模块级)
    for match in _PY_ANN_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        name = match.group("name")
        if line in matched_lines or name in _NOISE_WORDS:
            continue
        _add(
            symbols,
            ctx,
            name,
            "local",
            type=match.group("type"),
            line=line,
            default=match.group("value"),
            uppercase_constant=True,
        )

    # 类体里的裸注解: count: int (字段, 无初始值)
    class_lines = {
        ctx.line_of(match.start())
        for match in re.finditer(r"^[ \t]*class\s+[A-Za-z_]\w*", ctx.masked, re.MULTILINE)
    }
    for match in _PY_ANN_ONLY_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        name = match.group("name")
        if not class_lines or name in _NOISE_WORDS:
            continue
        if not any(line > start for start in class_lines) or ctx.indent_of(line) == 0:
            continue
        if line in matched_lines:
            continue
        _add(symbols, ctx, name, "field", type=match.group("type"), line=line)

    # 模块级常量: UPPER = ...
    for match in _PY_ASSIGN_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        if ctx.indent_of(line) != 0 or line in matched_lines:
            continue
        target = match.group("target").strip()
        if not target or any(ch in target for ch in "()[]."):
            continue
        names = re.findall(r"[A-Za-z_]\w*", target)
        if not names or not all(_looks_upper(name) for name in names):
            continue
        _add(symbols, ctx, names[0], "constant", line=line, default=match.group("value"), type=match.group("ann") or "")
        matched_lines.add(line)

    # global 声明可能出现在任何作用域
    for match in _PY_GLOBAL_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        for name in match.group("names").replace(",", " ").split():
            _add(symbols, ctx, name, "global", line=line)

    return symbols


# --------------------------------------------------------------------------------------
# C / C++
# --------------------------------------------------------------------------------------

_C_FUNC_RE = re.compile(
    r"^[ \t]*(?P<type>[A-Za-z_][\w:<>,\s\*&\[\]]*?)[ \t]+(?P<name>[A-Za-z_]\w*)[ \t]*\((?P<rawparams>[^;{}]*)\)"
    r"[ \t]*(?:const)?[ \t]*[{;]",
    re.MULTILINE,
)
_C_DECL_RE = re.compile(
    r"^[ \t]*(?:(?:static|const|constexpr|volatile|register|extern|unsigned|signed|inline|"
    r"mutable|public|private|protected)\s+)*(?:(?P<type>[A-Za-z_][\w:]*?)"
    r"(?P<pointers>[ \t]+[*&]*[ \t]*|[*&]+[ \t]*))(?P<name>[A-Za-z_]\w*)\s*(?P<rest>[=;:,(\[].*)$",
    re.MULTILINE,
)
_C_STRUCT_RE = re.compile(r"^[ \t]*(?:class|struct|union)\s+(?P<name>[A-Za-z_]\w*)", re.MULTILINE)
_C_DEFINE_RE = re.compile(r"^[ \t]*#\s*define\s+(?P<name>[A-Za-z_]\w*)\s*(?P<value>.*)$", re.MULTILINE)
# C++11 的 auto/const auto 推导声明: 单独列一条, 免得上面那条把 auto 当成类型名
_C_AUTO_RE = re.compile(
    r"^[ \t]*(?:(?:const|static|register|volatile|auto)\s+)*auto\s+(?P<pointers>[*&]+\s*)?"
    r"(?P<name>[A-Za-z_]\w*)\s*(?P<rest>[=;:(].*)$",
    re.MULTILINE,
)


def _raw_span(masked: str, raw: str, start: int, stop: int) -> str:
    """把掩码文本里的区间映射回原文.

    默认值 (``b = "x"``) 的字符会被掩码抹掉, 但掩码与原文逐字符对齐,
    所以同样的下标可以直接从原文里取出原始写法。
    """
    if not raw or len(raw) != len(masked):
        return masked[start:stop]
    if start < 0 or stop > len(raw) or stop <= start:
        return masked[start:stop]
    return raw[start:stop]


def _parse_param_text(text: str, raw_text: str = "") -> Tuple[str, str, str]:
    """把单个形参文本拆成 ``(名字, 类型, 默认值)``.

    按语言习惯分别处理: 冒号在前的 ``name: Type`` 与类型在前的 ``Type name``。
    两者都取不到类型时返回空类型, 由调用方决定是否丢弃。
    ``raw_text`` 传入未掩码的同一段文本时, 默认值能保留引号里的原始内容。
    """
    if raw_text:
        text = raw_text
    text = text.strip()
    if not text:
        return "", "", ""
    default = ""
    if "=" in text:
        text, default = text.split("=", 1)
        default = _clip(default)

    # 函数指针形参 int (*cb)(int) 的名字被括号包着, 优先处理
    pointer = re.search(r"\(\s*[*&]+\s*(?P<name>[A-Za-z_]\w*)\s*\)", text)
    if pointer:
        type_text = re.sub(r"\(\s*[*&]+\s*[A-Za-z_]\w*\s*\)", " ", text)
        return pointer.group("name"), _refine_type(type_text), default

    # 只有括号/方括号外的冒号才是 "name: Type", 免得 C++ 的 std::string 被切开
    depth = 0
    for position, char in enumerate(text):
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(depth - 1, 0)
        elif char == ":" and depth == 0:
            head_names = re.findall(r"[A-Za-z_]\w*", text[:position])
            if not head_names:
                return "", "", ""
            return head_names[-1], _refine_type(text[position + 1 :]), default

    names = re.findall(r"[A-Za-z_]\w*", text)
    if text.lstrip().startswith(("...", "*", "&")):
        # Go 的变参 ...int / Rust 的 &str 一类没有名字, 交给调用方忽略
        return "", _refine_type(text), default
    if len(names) < 2:
        return (names[0] if names else ""), "", default
    # 类型可能带 :: 或 <> (const std::string &name), 因此按名字在原文中的位置切分,
    # 而不是简单地丢掉最后一个词
    last = None
    for found_name in re.finditer(r"[A-Za-z_]\w*", text):
        last = found_name
    if last is None:
        return "", "", ""
    return last.group(0), _refine_type(text[: last.start()]), default


def _c_parameters(params: str, raw_params: str = "") -> List[Tuple[str, str, str]]:
    """C/C++/Rust 形参串 -> ``[(名字, 类型, 默认值), ...]`` (``void`` 直接跳过)。

    这几种语言的形参都是 ``类型 名字``, 名字只会紧跟空白或 ``*``/``&``, 因此不能套用
    ``name: Type`` 的冒号规则 (否则 ``std::string`` 会被从中间切开)。
    名字与类型从掩码文本判断 (避开注释), 默认值从 ``raw_params`` 取原文。
    """
    found: List[Tuple[str, str, str]] = []
    masked_items = _split_top(params)
    raw_items = _split_top(raw_params) if raw_params else []
    for index, raw in enumerate(masked_items):
        raw_item = raw_items[index] if index < len(raw_items) else ""
        text = raw.strip()
        if not text or text == "void":
            continue
        default = ""
        if "=" in text:
            _, masked_default = text.split("=", 1)
            default = _clip(masked_default)
            if raw_item and "=" in raw_item:
                default = _clip(raw_item.split("=", 1)[1])
            text = text.split("=", 1)[0]
        # 函数指针形参 int (*cb)(int) 的名字被括号包着, 优先处理
        pointer = re.search(r"\(\s*[*&]+\s*(?P<name>[A-Za-z_]\w*)\s*\)", text)
        if pointer:
            type_text = re.sub(r"\(\s*[*&]+\s*[A-Za-z_]\w*\s*\)", " ", text)
            found.append((pointer.group("name"), _refine_type(type_text), default))
            continue
        name_match = re.search(r"(?:^|[\s*&])([A-Za-z_]\w*)\s*(?:\[\s*\])?\s*$", text)
        if not name_match:
            continue
        type_text = _refine_type(text[: name_match.start(1)])
        if not type_text:
            # 只有名字没有类型 (Rust 的 &str / Go 的 int) 时跳过
            continue
        found.append((name_match.group(1), type_text, default))
    return found


def _go_parameters(params: str) -> List[Tuple[str, str, str]]:
    """Go 形参 ``a int, b, c string`` -> ``[(名字, 类型, 默认值), ...]``。"""
    found: List[Tuple[str, str, str]] = []
    pending: List[str] = []
    for raw in _split_top(params):
        text = raw.strip()
        if not text:
            continue
        tokens = text.split()
        # 变参 ...T 没有名字
        if len(tokens) == 1:
            if pending:
                for name in pending:
                    found.append((name, tokens[0], ""))
                pending = []
            continue
        *name_tokens, type_text = tokens
        names = [item.strip() for item in " ".join(name_tokens).split(",") if item.strip()]
        found.extend((name, type_text, "") for name in names)
    return found


def _detect_c_family(ctx: _Ctx, function_name: str, language: str) -> List[DetectedSymbol]:
    """C/C++: 函数签名形参、局部声明、类/结构体成员、#define 常量。"""
    symbols: List[DetectedSymbol] = []
    struct_ranges: List[Tuple[int, int]] = []
    for match in _C_STRUCT_RE.finditer(ctx.masked):
        open_index = ctx.masked.find("{", match.end())
        if open_index < 0:
            continue
        close_index = _find_matching(ctx.masked, open_index, "{", "}")
        if close_index < 0:
            continue
        struct_ranges.append((ctx.line_of(open_index), ctx.line_of(close_index)))

    for match in _C_FUNC_RE.finditer(ctx.masked):
        name = match.group("name")
        if name in _NOISE_WORDS or name in {"if", "for", "while", "switch", "return", "sizeof", "catch", "else"}:
            continue
        if function_name and function_name != name:
            continue
        for param_name, param_type, param_default in _c_parameters(
            match.group("rawparams"),
            _raw_span(ctx.masked, ctx.code, match.start("rawparams"), match.end("rawparams")),
        ):
            _add(
                symbols,
                ctx,
                param_name,
                "parameter",
                type=param_type,
                detail="函数签名",
                default=param_default,
            )

    for match in _C_DEFINE_RE.finditer(ctx.masked):
        _add(
            symbols,
            ctx,
            match.group("name"),
            "constant",
            line=ctx.line_of(match.start()),
            default=match.group("value"),
            detail="#define",
        )

    for match in _C_DECL_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        name = match.group("name")
        rest = match.group("rest") or ""
        type_text = match.group("type")
        if language == "cpp" and type_text in {"return", "new", "delete", "else"}:
            continue
        if name in _NOISE_WORDS or _skip_type_token(type_text):
            continue
        in_struct = any(start <= line <= end for start, end in struct_ranges)
        pointers = match.group("pointers") or " "
        if "(" in rest and not rest.startswith("["):
            # 函数声明/定义, 不是变量
            continue
        if rest.startswith("="):
            value = rest[1:].strip().rstrip(";").strip()
        elif rest.startswith("{"):
            value = rest.strip()
        else:
            value = ""
        # 常量命名优先; 再判断是否在 class/struct 花括号内; 最后按缩进区分文件作用域与函数体
        if _looks_upper(name):
            kind = "constant"
        elif in_struct:
            kind = "field"
        elif ctx.indent_of(line) > 0:
            kind = "local"
        else:
            kind = "global"
        _add(
            symbols,
            ctx,
            name,
            kind,
            type=f"{type_text}{'*' if '*' in pointers else ''}{'&' if '&' in pointers else ''}",
            line=line,
            default=value,
            uppercase_constant=kind in {"local", "global"},
        )

    # auto/const auto 推导声明 (C++11)
    for match in _C_AUTO_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        rest = match.group("rest") or ""
        if "(" in rest and not rest.startswith("["):
            continue
        _add(
            symbols,
            ctx,
            match.group("name"),
            "local" if ctx.indent_of(line) > 0 else "global",
            line=line,
            default=rest[1:].strip().rstrip(";") if rest.startswith("=") else "",
        )

    return symbols


# --------------------------------------------------------------------------------------
# Go
# --------------------------------------------------------------------------------------

_GO_FUNC_RE = re.compile(
    r"^[ \t]*func\s*(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)"
    r"\s*(?P<results>\([^)]*\)|[^;{\n]*)[ \t]*\{",
    re.MULTILINE,
)
_GO_VAR_RE = re.compile(
    r"^[ \t]*var\s+(?P<name>[A-Za-z_]\w*)(?:\s*,\s*(?P<more>[A-Za-z_]\w*))?\s*(?P<type>[^=\n]*?)\s*(?:=(?!=)\s*(?P<value>.+))?$",
    re.MULTILINE,
)
_GO_CONST_RE = re.compile(r"^[ \t]*const\s+(?P<name>[A-Za-z_]\w*)\s*(?P<type>[^=\n]*?)\s*(?:=(?!=)\s*(?P<value>.+))?$", re.MULTILINE)
_GO_BLOCK_VAR_RE = re.compile(r"^[ \t]*(?:var|const)\s*\($", re.MULTILINE)
_GO_SHORT_RE = re.compile(r"^[ \t]*(?P<targets>[^=\n]+?)\s*:=\s*(?P<value>.+)$", re.MULTILINE)
_GO_FIELD_RE = re.compile(
    r"^[ \t]*(?P<names>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+(?P<type>(?:\*|\[\])?[A-Za-z_][\w\.\[\]\*]*)\s*(?P<tag>`[^`]*`)?\s*$",
    re.MULTILINE,
)


def _go_field_names(text: str) -> List[str]:
    """Go 字段名列表 (``A, B int`` 展开成两个)。"""
    if "//" in text:
        text = text.split("//", 1)[0]
    names = [token.strip() for token in text.split(",")]
    return [name for name in names if re.fullmatch(r"[A-Za-z_]\w*", name)]


def _detect_go(ctx: _Ctx, function_name: str) -> List[DetectedSymbol]:
    """Go: 具名/匿名形参、具名返回值、var/const/short 声明、结构体字段。"""
    symbols: List[DetectedSymbol] = []
    struct_ranges: List[Tuple[int, int]] = []
    for match in re.finditer(r"^[ \t]*type\s+(?P<name>[A-Za-z_]\w*)\s+struct\s*\{", ctx.masked, re.MULTILINE):
        open_index = ctx.masked.find("{", match.end() - 1)
        close_index = _find_matching(ctx.masked, open_index, "{", "}")
        if close_index < 0:
            continue
        struct_ranges.append((ctx.line_of(open_index), ctx.line_of(close_index)))

    for match in _GO_FUNC_RE.finditer(ctx.masked):
        name = match.group("name")
        if function_name and function_name != name:
            continue
        for param_name, param_type, param_default in _go_parameters(match.group("params")):
            _add(
                symbols,
                ctx,
                param_name,
                "parameter",
                type=param_type,
                detail="函数签名",
                default=param_default,
            )
        results = (match.group("results") or "").strip()
        if results and results != "{":
            results = results.strip("()")
            for result_name, result_type, _ in _go_parameters(results):
                if not result_type:
                    continue
                _add(
                    symbols,
                    ctx,
                    result_name,
                    "return",
                    type=result_type,
                    detail="返回值",
                )

    for match in _GO_VAR_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        name = match.group("name")
        extra = match.group("more")
        value = match.group("value")
        names = [name] + ([extra] if extra else [])
        for item in names:
            _add(
                symbols,
                ctx,
                item,
                "local" if ctx.indent_of(line) > 0 else "global",
                type=_refine_type(match.group("type") or ""),
                line=line,
                default=value,
                uppercase_constant=True,
            )

    for match in _GO_CONST_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        _add(
            symbols,
            ctx,
            match.group("name"),
            "constant",
            type=_refine_type(match.group("type") or ""),
            line=line,
            default=match.group("value"),
        )

    # const ( A = iota ) 块内的裸声明: 掩码里表现为 "A = iota"
    for match in _GO_BLOCK_VAR_RE.finditer(ctx.masked):
        open_index = ctx.masked.find("(", match.start())
        close_index = _find_matching(ctx.masked, open_index, "(", ")")
        if close_index < 0:
            continue
        body = ctx.masked[open_index + 1 : close_index]
        offset = open_index + 1
        for body_match in re.finditer(r"(?P<name>[A-Za-z_]\w*)\s*(?P<type>[A-Za-z_][\w\.\[\]\*]*)?\s*(?:=(?!=)\s*(?P<value>[^\n]*))?", body):
            name = body_match.group("name")
            if name in _NOISE_WORDS:
                continue
            _add(
                symbols,
                ctx,
                name,
                "constant",
                type=_refine_type(body_match.group("type") or ""),
                offset=offset + body_match.start("name"),
                default=body_match.group("value"),
            )

    for match in _GO_SHORT_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        targets = match.group("targets")
        if targets.strip().startswith(("if ", "for ", "switch ", "case ", "select ", "return ", "else")):
            continue
        value = match.group("value").strip()
        if value.startswith("range "):
            continue
        destructured = "," in targets
        for name in re.findall(r"[A-Za-z_]\w*", targets):
            if name in _NOISE_WORDS:
                continue
            _add(
                symbols,
                ctx,
                name,
                "local",
                line=line,
                default=value,
                destructured=destructured,
            )

    # 结构体字段: 只看紧挨在结构体花括号里的那一层
    for start, end in struct_ranges:
        for line in range(start + 1, end):
            text = ctx.line_text(line)
            stripped = text.strip()
            if not stripped or stripped.startswith("//"):
                continue
            field_match = re.match(
                r"(?P<names>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+"
                r"(?P<type>(?:\*|\[\])?[A-Za-z_][\w\.\[\]\*]*)",
                stripped,
            )
            if not field_match or ctx.indent_of(line) <= ctx.indent_of(start):
                continue
            type_text = field_match.group("type")
            if type_text in _NOISE_WORDS:
                continue
            for name in _go_field_names(field_match.group("names")):
                _add(symbols, ctx, name, "field", type=type_text, line=line)

    return symbols


# --------------------------------------------------------------------------------------
# Java 系 (Java / C# / Kotlin / Swift)
# --------------------------------------------------------------------------------------

_JAVA_METHOD_RE = re.compile(
    r"^[ \t]*(?:(?:public|private|protected|static|final|abstract|synchronized|native|virtual|"
    r"override|open|internal|sealed|suspend|async|inline|operator|partial|new|unsafe|extern|"
    r"readonly|const)\s+)*"
    r"(?P<type>[A-Za-z_][\w:<>,\[\]\.\?]*)?[ \t]*(?P<name>[A-Za-z_]\w*)(?=\s*\()"
    r"[ \t]*\((?P<rawparams>[^)]*)\)"
    r"[ \t]*(?::[ \t]*(?P<ktype>[A-Za-z_][\w<>,\[\]\.\?]*))?[ \t]*(?:\{|=>|throws|where|;)",
    re.MULTILINE,
)
# Java/C# 的 ``Type name = ...`` 局部声明 (类型必须自己占一段, 冒号式类型另走 _JAVA_SWIFT_DECL_RE)
_JAVA_LOCAL_RE = re.compile(
    r"^[ \t]*(?:(?:final|readonly|static|const|var|let|val|lateinit|dynamic)\s+)*"
    r"(?P<type>[A-Za-z_]\w*(?:[\.\:][A-Za-z_]\w*)*(?:<[^;=]*>)?(?:\[\])*\??)"
    r"[ \t]+(?P<name>[A-Za-z_]\w*)\s*(?P<rest>[=;].*)$",
    re.MULTILINE,
)
# Kotlin/Swift 的 ``val/var/let name: Type = value``
_JAVA_SWIFT_DECL_RE = re.compile(
    r"^[ \t]*(?:(?:private|public|internal|fileprivate|open|final|static|const|lazy|weak|"
    r"override|readonly)\s+)*(?P<keyword>let|var|val)\s+(?P<name>[A-Za-z_]\w*)"
    r"\s*(?::\s*(?P<type>[^={\n]+?))?\s*(?:=(?!=)\s*(?P<value>[^\n]+))?\s*$",
    re.MULTILINE,
)
# 类型级成员: 带访问修饰符的声明 (字段或常量)
_JAVA_FIELD_RE = re.compile(
    r"^[ \t]{2,}(?:(?:public|private|protected|internal|static|readonly|const|volatile|"
    r"transient|lateinit|open|override|weak|lazy)\s+)+"
    r"(?:(?P<type>[A-Za-z_]\w*(?:<[^>]*>)?)\s+)?(?P<name>[A-Za-z_]\w*)\s*"
    r"(?:=(?!=)\s*(?P<value>[^\n]+))?\s*[;{]?\s*$",
    re.MULTILINE,
)
_JAVA_CONST_RE = re.compile(
    r"^[ \t]*(?:(?:public|private|protected|internal)\s+)?(?:static\s+final|const|final)\s+"
    r"(?P<type>[A-Za-z_]\w*(?:\s*<[^>]*>)?)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<value>[^;\n]+)",
    re.MULTILINE,
)
# Kotlin 的 ``fun name(参数): 返回类型``
_KOTLIN_METHOD_RE = re.compile(
    r"^[ \t]*(?:(?:public|private|protected|internal|open|final|override|suspend|inline|"
    r"operator|infix|tailrec|external|abstract|sealed)\s+)*fun\s+"
    r"(?:\s*<[^>]*>\s*)?(?P<name>[A-Za-z_]\w*)\s*\((?P<rawparams>[^)]*)\)"
    r"[ \t]*(?::[ \t]*(?P<ktype>[A-Za-z_][\w<>,\[\]\.\?]*))?",
    re.MULTILINE,
)
# Swift 的 ``func name(参数) -> 返回类型``
_SWIFT_METHOD_RE = re.compile(
    r"^[ \t]*(?:(?:public|private|internal|fileprivate|open|final|static|class|mutating|"
    r"nonmutating|override|required|convenience|dynamic|async)\s+)*func\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\((?P<rawparams>[^)]*)\)"
    r"(?:[ \t]*->[ \t]*(?P<type>[^;{\n]+?))?[ \t]*(?:\{|$)",
    re.MULTILINE,
)


def _java_parameters(params: str, colon_style: bool, raw_params: str = "") -> List[Tuple[str, str, str]]:
    """Java 系形参解析.

    ``colon_style=True`` 用于 Kotlin/Swift (``name: Type``), 否则是 Java/C# 的 ``Type name``。
    ``raw_params`` 用于还原字符串默认值。
    """
    found: List[Tuple[str, str, str]] = []
    masked_items = _split_top(params)
    raw_items = _split_top(raw_params) if raw_params else []
    for index, raw in enumerate(masked_items):
        raw_item = raw_items[index] if index < len(raw_items) else ""
        text = raw.strip()
        if not text:
            continue
        default = ""
        if "=" in text:
            text, default = text.split("=", 1)
            default = _clip(default)
            if raw_item and "=" in raw_item:
                default = _clip(raw_item.split("=", 1)[1])
        name = ""
        type_text = ""
        if colon_style:
            if ":" in text:
                head, type_text = text.split(":", 1)
                name = _clean_name(head)
                type_text = _refine_type(type_text)
            else:
                words = re.findall(r"[A-Za-z_]\w*", text)
                if not words:
                    continue
                name = words[-1]
            if not name:
                continue
        else:
            # 支持 Java 的 @Nullable 注解与 final 修饰
            text = re.sub(r"@\w+(\([^)]*\))?", " ", text)
            text = re.sub(r"\b(?:final|params|in|out|ref|const|static|readonly)\b", " ", text)
            names = re.findall(r"[A-Za-z_]\w*", text)
            if not names:
                continue
            name = names[-1]
            type_text = _refine_type(" ".join(names[:-1]))
        if name and name not in _NOISE_WORDS:
            found.append((name, type_text, default))
    return found


def _detect_java_family(ctx: _Ctx, function_name: str, language: str) -> List[DetectedSymbol]:
    """Java/C#/Kotlin/Swift: 方法形参、局部声明、类型级字段与常量。"""
    symbols: List[DetectedSymbol] = []
    colon_style = language in {"kotlin", "swift"}
    method_body_lines: List[Tuple[int, int]] = []

    # fun/func 单列一条正则: 否则它们会被当成返回类型, 名字组会错位到下一个标识符
    method_patterns = [_JAVA_METHOD_RE]
    if language == "kotlin":
        method_patterns.insert(0, _KOTLIN_METHOD_RE)
    elif language == "swift":
        method_patterns.insert(0, _SWIFT_METHOD_RE)

    found_names: set = set()
    for pattern in method_patterns:
        for match in pattern.finditer(ctx.masked):
            name = match.group("name")
            if name in found_names:
                continue
            if name in _NOISE_WORDS or name in {"if", "for", "while", "switch", "catch", "return", "when"}:
                continue
            found_names.add(name)
            open_index = ctx.masked.find("{", match.end() - 1)
            if open_index >= 0:
                close_index = _find_matching(ctx.masked, open_index, "{", "}")
                if close_index > open_index:
                    method_body_lines.append((ctx.line_of(open_index), ctx.line_of(close_index)))
            if function_name and function_name != name:
                continue
            for param_name, param_type, param_default in _java_parameters(
                match.group("rawparams"),
                colon_style,
                _raw_span(
                    ctx.masked,
                    ctx.code,
                    match.start("rawparams"),
                    match.end("rawparams"),
                ),
            ):
                _add(
                    symbols,
                    ctx,
                    param_name,
                    "parameter",
                    type=param_type,
                    detail="函数签名",
                    default=param_default,
                )
            groups = match.groupdict()
            # 三条方法正则的返回类型组名不同 (type / ktype), 取存在的那个
            return_type = _refine_type(groups.get("type") or groups.get("ktype") or "")
            if "(" in return_type:
                # ``func add(...) -> Int`` 里返回类型可能被读成 ``add(...) -> Int``, 只取箭头右侧
                return_type = _refine_type(return_type.split("->", 1)[-1])
            if return_type and return_type not in {"void", "Unit"}:
                _add(symbols, ctx, "return", "return", type=return_type, detail="方法签名")

    def in_method_body(line: int) -> bool:
        """行号是否落在某个方法体里 (用来区分局部变量与类字段)。"""
        return any(start <= line <= end for start, end in method_body_lines)

    # Kotlin/Swift: val/var/let name[: Type] [= value]
    for match in _JAVA_SWIFT_DECL_RE.finditer(ctx.masked):
        name = match.group("name")
        if name in _NOISE_WORDS:
            continue
        line = ctx.line_of(match.start())
        value = (match.group("value") or "").strip()
        # ``var label`` 后面没写初始值时, 正则会把下一行的语句当成 value, 这类噪声直接丢
        if value.startswith(("return ", "if ", "for ", "while ", "}")):
            continue
        type_text = (match.group("type") or "").strip()
        if type_text in _NOISE_WORDS:
            type_text = ""
        if in_method_body(line):
            _add(
                symbols,
                ctx,
                name,
                "local",
                type=type_text,
                line=line,
                default=value,
                uppercase_constant=True,
            )
        else:
            _add(symbols, ctx, name, "field", type=type_text, line=line, default=value)

    # Java/C#: Type name = value
    for match in _JAVA_LOCAL_RE.finditer(ctx.masked):
        type_text = match.group("type")
        name = match.group("name")
        if type_text in _NOISE_WORDS or name in _NOISE_WORDS or _skip_type_token(type_text):
            continue
        if type_text in {"val", "var", "let"}:
            # Kotlin/Swift 的声明交给上面的关键字规则, 避免重复
            continue
        line = ctx.line_of(match.start())
        rest = match.group("rest") or ""
        if "(" in rest and not rest.startswith("["):
            continue
        value = rest[1:].rstrip(";") if rest.startswith("=") else ""
        kind = "local" if in_method_body(line) or ctx.indent_of(line) >= 8 else "field"
        _add(
            symbols,
            ctx,
            name,
            kind,
            type=type_text,
            line=line,
            default=value,
            uppercase_constant=kind == "local",
        )

    # 类型级成员: 带访问修饰符, 且不在方法体里
    for match in _JAVA_FIELD_RE.finditer(ctx.masked):
        name = match.group("name")
        if name in _NOISE_WORDS:
            continue
        line = ctx.line_of(match.start())
        if in_method_body(line):
            continue
        _add(
            symbols,
            ctx,
            name,
            "field",
            type=match.group("type") or "",
            line=line,
            default=match.group("value") or "",
        )

    for match in _JAVA_CONST_RE.finditer(ctx.masked):
        _add(
            symbols,
            ctx,
            match.group("name"),
            "constant",
            type=match.group("type"),
            line=ctx.line_of(match.start()),
            default=match.group("value"),
        )

    return symbols


# --------------------------------------------------------------------------------------
# Rust
# --------------------------------------------------------------------------------------

_RUST_FN_RE = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^>]*>)?\s*\((?P<rawparams>[^)]*)\)\s*(?:->\s*(?P<ret>[^{\n]+))?",
    re.MULTILINE,
)
_RUST_LET_RE = re.compile(
    r"^[ \t]*let\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)\s*(?::\s*(?P<type>[^=;]+?))?\s*(?:=(?!=)\s*(?P<value>[^;]+))?;",
    re.MULTILINE,
)
_RUST_CONST_RE = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)\s*"
    r"(?::\s*(?P<type>[^=;]+?))?\s*=\s*(?P<value>[^;]+);",
    re.MULTILINE,
)
_RUST_FIELD_RE = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?P<name>[A-Za-z_]\w*)\s*:\s*(?P<type>[^,}\n]+)",
    re.MULTILINE,
)
# Rust 字段类型可以是 `Result<u32, String>`, _refine_type 需要保留尖括号
_RUST_TYPE_RE = re.compile(r"^(?P<name>[A-Za-z_]\w*)\s*:\s*(?P<type>.+?)(?:=.*)?$")
# `Result<u32, String>` 里的逗号不属于字段分隔, 单独记录以防被 _refine_type 截断
_RUST_GENERIC_RE = re.compile(r"<[^<>]*>")


def _detect_rust(ctx: _Ctx, function_name: str) -> List[DetectedSymbol]:
    """Rust: fn 形参、``->`` 返回类型、let/const/static、结构体字段。"""
    symbols: List[DetectedSymbol] = []
    struct_ranges: List[Tuple[int, int]] = []
    for match in re.finditer(r"^[ \t]*(?:pub\s+)?struct\s+[A-Za-z_]\w*", ctx.masked, re.MULTILINE):
        open_index = ctx.masked.find("{", match.end())
        if open_index < 0:
            continue
        close_index = _find_matching(ctx.masked, open_index, "{", "}")
        if close_index < 0:
            continue
        struct_ranges.append((ctx.line_of(open_index), ctx.line_of(close_index)))

    for match in _RUST_FN_RE.finditer(ctx.masked):
        name = match.group("name")
        if function_name and function_name != name:
            continue
        # Rust 的形参是 ``name: Type``, 用冒号规则而不是 C 的类型在前规则;
        # 默认值走原文, 免得字符串默认值被掩码抹掉
        raw_params = match.group("rawparams")
        raw_items = _split_top(_raw_span(ctx.masked, ctx.code, match.start("rawparams"), match.end("rawparams")))
        masked_items = _split_top(raw_params)
        for index, item in enumerate(raw_items):
            raw_item = item
            masked_item = masked_items[index] if index < len(masked_items) else item
            param_name, param_type, param_default = _parse_param_text(masked_item, raw_item)
            if not param_name or param_name == "self":
                continue
            _add(
                symbols,
                ctx,
                param_name,
                "parameter",
                type=param_type,
                detail="函数签名",
                default=param_default,
            )
        return_type = _refine_type(match.group("ret") or "")
        if return_type:
            _add(symbols, ctx, "return", "return", type=return_type, detail="函数签名")

    for match in _RUST_LET_RE.finditer(ctx.masked):
        _add(
            symbols,
            ctx,
            match.group("name"),
            "local",
            type=match.group("type") or "",
            line=ctx.line_of(match.start()),
            default=match.group("value") or "",
        )

    for match in _RUST_CONST_RE.finditer(ctx.masked):
        _add(
            symbols,
            ctx,
            match.group("name"),
            "constant",
            type=match.group("type") or "",
            line=ctx.line_of(match.start()),
            default=match.group("value") or "",
        )

    for match in _RUST_FIELD_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        if not any(start <= line <= end for start, end in struct_ranges):
            continue
        _add(symbols, ctx, match.group("name"), "field", type=match.group("type"), line=line)

    return symbols


# --------------------------------------------------------------------------------------
# PHP
# --------------------------------------------------------------------------------------

_PHP_FUNC_RE = re.compile(
    r"^[ \t]*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+(?P<name>[A-Za-z_]\w*)\s*"
    r"\((?P<rawparams>[^)]*)\)\s*(?::\s*(?P<ret>[A-Za-z_\\][\w\\\|]*))?",
    re.MULTILINE,
)
_PHP_VAR_RE = re.compile(r"^[ \t]*(?P<name>\$[A-Za-z_]\w*)\s*=(?!=)\s*(?P<value>[^;]+);", re.MULTILINE)
_PHP_PROP_RE = re.compile(
    r"^[ \t]*(?:(?:public|private|protected|static|readonly|var)\s+)+(?P<name>\$[A-Za-z_]\w*)\s*(?:=\s*(?P<value>[^;]+))?;",
    re.MULTILINE,
)
_PHP_THIS_RE = re.compile(r"^[ \t]*\$this->(?P<name>[A-Za-z_]\w*)\s*=(?!=)")
_PHP_DEFINE_RE = re.compile(r"\bdefine\s*\(\s*['\"](?P<name>[A-Za-z_]\w*)['\"]\s*,\s*(?P<value>[^)]*)\)")
_PHP_CONST_RE = re.compile(r"^[ \t]*(?:(?:public|private|protected)\s+)?const\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<value>[^;]+)", re.MULTILINE)


def _php_parameters(params: str, raw_params: str = "") -> List[Tuple[str, str, str]]:
    """PHP 形参: ``Type $name = default``。"""
    found: List[Tuple[str, str, str]] = []
    masked_items = _split_top(params)
    raw_items = _split_top(raw_params) if raw_params else []
    for index, raw in enumerate(masked_items):
        raw_item = raw_items[index] if index < len(raw_items) else ""
        text = raw.strip()
        if not text:
            continue
        default = ""
        if "=" in text:
            text, default = text.split("=", 1)
            default = _clip(default)
            if raw_item and "=" in raw_item:
                default = _clip(raw_item.split("=", 1)[1])
        match = re.search(r"\$([A-Za-z_]\w*)", text)
        if not match:
            continue
        type_text = _refine_type(text[: match.start()])
        found.append((match.group(1), type_text, default))
    return found


def _detect_php(ctx: _Ctx, function_name: str) -> List[DetectedSymbol]:
    """PHP: function 形参、``$var`` 局部/全局、``$this->x`` 字段、define/const 常量。"""
    symbols: List[DetectedSymbol] = []
    for match in _PHP_FUNC_RE.finditer(ctx.masked):
        name = match.group("name")
        if function_name and function_name != name:
            continue
        for param_name, param_type, param_default in _php_parameters(
            match.group("rawparams"),
            _raw_span(ctx.masked, ctx.code, match.start("rawparams"), match.end("rawparams")),
        ):
            _add(
                symbols,
                ctx,
                param_name,
                "parameter",
                type=param_type,
                detail="函数签名",
                default=param_default,
            )
        return_type = _refine_type(match.group("ret") or "")
        if return_type:
            _add(symbols, ctx, "return", "return", type=return_type, detail="函数签名")

    for match in _PHP_THIS_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        text = ctx.raw_line_text(line)
        value = text.split("=", 1)[1] if "=" in text else ""
        _add(symbols, ctx, match.group("name"), "field", line=line, default=value.rstrip(";"))

    for match in _PHP_PROP_RE.finditer(ctx.masked):
        _add(
            symbols,
            ctx,
            match.group("name"),
            "field",
            line=ctx.line_of(match.start()),
            default=match.group("value") or "",
        )

    for match in _PHP_VAR_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        kind = "local" if ctx.indent_of(line) > 0 else "global"
        _add(
            symbols,
            ctx,
            match.group("name"),
            kind,
            line=line,
            default=match.group("value"),
            uppercase_constant=True,
        )

    for match in _PHP_CONST_RE.finditer(ctx.masked):
        _add(
            symbols,
            ctx,
            match.group("name"),
            "constant",
            line=ctx.line_of(match.start()),
            default=match.group("value"),
        )

    for match in _PHP_DEFINE_RE.finditer(ctx.masked):
        _add(
            symbols,
            ctx,
            match.group("name"),
            "constant",
            line=ctx.line_of(match.start()),
            default=match.group("value"),
            detail="define()",
        )

    return symbols


# --------------------------------------------------------------------------------------
# JavaScript / TypeScript
# --------------------------------------------------------------------------------------

_JS_FUNC_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)?\s*"
    r"\((?P<rawparams>[^)]*)\)\s*(?::\s*(?P<ret>[^;{\n]+))?",
    re.MULTILINE,
)
_JS_VAR_RE = re.compile(
    r"^[ \t]*(?P<kind>const|let|var)\s+(?P<targets>[^=\n]+?)\s*=(?!=)\s*(?P<value>[^\n;]+)",
    re.MULTILINE,
)
# 类成员: 带修饰符或在 class 花括号内、缩进 2 空格的 ``name = value`` / ``name: Type = value``
_JS_CLASS_FIELD_RE = re.compile(
    r"^[ \t]{2,}(?:(?:public|private|protected|static|readonly|declare|abstract|override)\s+)*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:\??:\s*(?P<type>[^=;\n]+))?\s*=(?!=)\s*(?P<value>[^\n;]+)",
    re.MULTILINE,
)
_JS_CLASS_RE = re.compile(r"^[ \t]*(?:export\s+)?(?:default\s+)?class\s+[A-Za-z_$][\w$]*", re.MULTILINE)
_JS_ARROW_PAREN_RE = re.compile(r"(?P<params>\([^()]*\))\s*(?::\s*(?P<ret>[^={\n]+?))?\s*=>")
# TS 内置类型出现在形参名字位置时不是变量 (``a: number`` 的第二种解析会得到 number)
_JS_TYPE_KEYWORDS = frozenset(
    {"string", "number", "boolean", "object", "symbol", "bigint", "void", "never", "any", "unknown", "undefined", "null"}
)


def _js_parameters(params: str, raw_params: str = "") -> List[Tuple[str, str, str]]:
    """JS/TS 形参: ``a``、``a = 1``、``a: number = 1``、``{a, b}``。"""
    found: List[Tuple[str, str, str]] = []
    masked_items = _split_top(params)
    raw_items = _split_top(raw_params) if raw_params else []
    for index, raw in enumerate(masked_items):
        raw_item = raw_items[index] if index < len(raw_items) else ""
        text = raw.strip()
        if not text:
            continue
        default = ""
        if "=" in text:
            text, default = text.split("=", 1)
            default = _clip(default)
            if raw_item and "=" in raw_item:
                default = _clip(raw_item.split("=", 1)[1])
        type_text = ""
        if ":" in text:
            text, type_text = text.split(":", 1)
        name = _clean_name(text)
        # TS 的内置类型没有引号也不是变量名, 出现在名字位置一律丢弃
        if not name or name in _NOISE_WORDS or name in _JS_TYPE_KEYWORDS:
            continue
        found.append((name, _refine_type(type_text), default))
    return found


def _detect_javascript(ctx: _Ctx, function_name: str, language: str) -> List[DetectedSymbol]:
    """JS/TS: function 与箭头函数形参、const/let/var、类字段。"""
    symbols: List[DetectedSymbol] = []
    class_ranges: List[Tuple[int, int]] = []
    for match in _JS_CLASS_RE.finditer(ctx.masked):
        open_index = ctx.masked.find("{", match.end())
        if open_index < 0:
            continue
        close_index = _find_matching(ctx.masked, open_index, "{", "}")
        if close_index < 0:
            continue
        class_ranges.append((ctx.line_of(open_index), ctx.line_of(close_index)))

    for match in _JS_FUNC_RE.finditer(ctx.masked):
        name = match.group("name") or ""
        if function_name and name and function_name != name:
            continue
        for param_name, param_type, param_default in _js_parameters(
            match.group("rawparams"),
            _raw_span(ctx.masked, ctx.code, match.start("rawparams"), match.end("rawparams")),
        ):
            _add(
                symbols,
                ctx,
                param_name,
                "parameter",
                type=param_type,
                detail="函数签名",
                default=param_default,
            )
        if language == "typescript":
            return_type = _refine_type(match.group("ret") or "")
            if return_type:
                _add(symbols, ctx, "return", "return", type=return_type, detail="函数签名")

    for match in _JS_ARROW_PAREN_RE.finditer(ctx.masked):
        # 形参串带上括号一起解析, 免得把 (x, y) 切成两段
        arrow_raw = _raw_span(
            ctx.masked,
            ctx.code,
            match.start("params") + 1,
            max(match.end("params") - 1, match.start("params") + 1),
        )
        for param_name, param_type, param_default in _js_parameters(
            match.group("params").strip("()"), arrow_raw
        ):
            _add(
                symbols,
                ctx,
                param_name,
                "parameter",
                type=param_type,
                detail="箭头函数",
                default=param_default,
            )
        if language == "typescript":
            return_type = _refine_type(match.group("ret") or "")
            if return_type:
                _add(symbols, ctx, "return", "return", type=return_type, detail="箭头函数")

    for match in _JS_VAR_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        kind = match.group("kind")
        targets = match.group("targets").strip()
        # ``const MAX: number = 100`` 里冒号后面是类型而不是变量名, 先切掉
        if ":" in targets:
            targets = targets.split(":", 1)[0].strip()
        value = match.group("value").strip()
        if "=>" in value:
            # 箭头函数已经被形参规则处理过, 这里不再把函数名当变量
            continue
        destructured = "," in targets or targets.startswith(("{", "["))
        for name in re.findall(r"[A-Za-z_$][\w$]*", targets):
            if name in _NOISE_WORDS:
                continue
            _add(
                symbols,
                ctx,
                name,
                "constant" if kind == "const" else "local",
                line=line,
                default=value,
                destructured=destructured,
            )

    for match in _JS_CLASS_FIELD_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        if not any(start <= line <= end for start, end in class_ranges):
            continue
        if "=>" in (match.group("value") or ""):
            continue
        _add(
            symbols,
            ctx,
            match.group("name"),
            "field",
            type=match.group("type") or "",
            line=line,
            default=match.group("value"),
        )

    return symbols


# --------------------------------------------------------------------------------------
# Ruby / Lua / Bash
# --------------------------------------------------------------------------------------

_RUBY_DEF_RE = re.compile(
    r"^[ \t]*def\s+(?P<name>[A-Za-z_][\w\?!=]*(?:\.[A-Za-z_]\w*)?)\s*(?:\((?P<params>[^)]*)\))?",
    re.MULTILINE,
)
_RUBY_ASSIGN_RE = re.compile(r"^[ \t]*(?P<name>@{1,2}[A-Za-z_]\w*|\$[A-Za-z_]\w*|[A-Za-z_]\w*)\s*=(?!=)\s*(?P<value>[^\n#]+)", re.MULTILINE)


def _ruby_parameters(params: str) -> List[Tuple[str, str, str]]:
    """Ruby 形参: ``a``、``b = 1``、``*rest``、``**kw``、``key:``。"""
    found: List[Tuple[str, str, str]] = []
    for raw in _split_top(params):
        text = raw.strip()
        if not text:
            continue
        default = ""
        if "=" in text:
            text, default = text.split("=", 1)
            default = _clip(default)
        text = text.rstrip(":").strip()
        name = _clean_name(text)
        if not name or name in _NOISE_WORDS:
            continue
        found.append((name, "", default))
    return found


def _detect_ruby(ctx: _Ctx, function_name: str) -> List[DetectedSymbol]:
    """Ruby: def 形参、局部变量、``@field``、``CONST``。"""
    symbols: List[DetectedSymbol] = []
    for match in _RUBY_DEF_RE.finditer(ctx.masked):
        name = match.group("name")
        if function_name and function_name != name.split(".")[-1]:
            continue
        for param_name, param_type, param_default in _ruby_parameters(match.group("params") or ""):
            _add(
                symbols,
                ctx,
                param_name,
                "parameter",
                type=param_type,
                detail="函数签名",
                default=param_default,
            )

    for match in _RUBY_ASSIGN_RE.finditer(ctx.masked):
        raw_name = match.group("name")
        line = ctx.line_of(match.start())
        value = match.group("value")
        if raw_name.startswith("@"):
            _add(symbols, ctx, raw_name.lstrip("@"), "field", line=line, default=value)
            continue
        if raw_name.startswith("$"):
            _add(symbols, ctx, raw_name, "global", line=line, default=value)
            continue
        kind = "constant" if _looks_upper(raw_name) else "local"
        if raw_name in _NOISE_WORDS:
            continue
        _add(symbols, ctx, raw_name, kind, line=line, default=value)

    return symbols


_LUA_FUNC_RE = re.compile(
    r"^[ \t]*(?:local\s+)?function\s+(?P<name>[A-Za-z_][\w\.:]*)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
_LUA_LOCAL_RE = re.compile(
    r"^[ \t]*local\s+(?P<name>[A-Za-z_]\w*)(?:\s*,\s*(?P<more>[A-Za-z_]\w*))*\s*(?:=(?!=)\s*(?P<value>[^\n]+))?",
    re.MULTILINE,
)
_LUA_GLOBAL_RE = re.compile(r"^[ \t]*(?P<name>[A-Za-z_]\w*)\s*=(?!=)\s*(?P<value>[^\n]+)", re.MULTILINE)


def _detect_lua(ctx: _Ctx, function_name: str) -> List[DetectedSymbol]:
    """Lua: function 形参、``local`` 局部、裸赋值全局。"""
    symbols: List[DetectedSymbol] = []
    for match in _LUA_FUNC_RE.finditer(ctx.masked):
        name = match.group("name")
        if function_name and function_name != name:
            continue
        for raw in _split_top(match.group("params")):
            text = raw.strip()
            if not text or text == "...":
                continue
            _add(symbols, ctx, text, "parameter", detail="函数签名")

    for match in _LUA_LOCAL_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        names = [match.group("name")]
        names.extend(re.findall(r",\s*([A-Za-z_]\w*)", ctx.line_text(line)))
        for name in names:
            _add(symbols, ctx, name, "local", line=line, default=match.group("value") or "")

    for match in _LUA_GLOBAL_RE.finditer(ctx.masked):
        name = match.group("name")
        if name in _NOISE_WORDS or name in {"local", "function", "end", "return"}:
            continue
        line = ctx.line_of(match.start())
        stripped = ctx.line_text(line).strip()
        if stripped.startswith(("if ", "elseif ", "while ", "for ", "until ")) or stripped == "end":
            continue
        _add(symbols, ctx, name, "global", line=line, default=match.group("value"))

    return symbols


_BASH_FUNC_RE = re.compile(
    r"^[ \t]*(?:function\s+)?(?P<name>[A-Za-z_][\w\-]*)\s*\(\s*\)\s*\{?", re.MULTILINE
)
_BASH_LOCAL_RE = re.compile(r"^[ \t]*(?P<kw>local|declare|typeset|readonly)\s+(?:-\w+\s+)*(?P<name>[A-Za-z_]\w*)", re.MULTILINE)
_BASH_ASSIGN_RE = re.compile(r"^[ \t]*(?P<name>[A-Za-z_]\w*)=(?P<value>[^\n]*)$", re.MULTILINE)
_BASH_POSITIONAL_RE = re.compile(r"\$\{?(?P<index>\d+)\}?")


def _detect_bash(ctx: _Ctx, function_name: str) -> List[DetectedSymbol]:
    """Bash: 位置参数 ``$1``、``local`` 变量、顶层 ``NAME=value``。"""
    symbols: List[DetectedSymbol] = []
    for match in _BASH_FUNC_RE.finditer(ctx.masked):
        name = match.group("name")
        if name in _NOISE_WORDS or name in {"if", "then", "else", "fi", "do", "done", "case", "esac"}:
            continue
        start = ctx.line_of(match.start())
        end = start
        # 函数体范围: 用闭合花括号粗略界定, 找不到就一路扫到文件末尾
        for line in range(start + 1, ctx.line_count + 1):
            text = ctx.line_text(line)
            if text.strip() == "}":
                end = line
                break
            end = line
        if function_name and function_name != name:
            continue
        used: List[int] = []
        for line in range(start, min(end, ctx.line_count) + 1):
            # 位置参数常写在双引号里 ("$1"), 掩码会把它抹掉, 所以这里用原文扫描
            for positional in _BASH_POSITIONAL_RE.finditer(ctx.raw_line_text(line)):
                index = int(positional.group("index"))
                if index > 0 and index not in used:
                    used.append(index)
        for index in sorted(used):
            _add(
                symbols,
                ctx,
                f"${index}",
                "parameter",
                detail="位置参数",
            )

    for match in _BASH_LOCAL_RE.finditer(ctx.masked):
        _add(
            symbols,
            ctx,
            match.group("name"),
            "local",
            line=ctx.line_of(match.start()),
        )

    for match in _BASH_ASSIGN_RE.finditer(ctx.masked):
        line = ctx.line_of(match.start())
        if ctx.indent_of(line) != 0:
            continue
        name = match.group("name")
        if name in _NOISE_WORDS:
            continue
        value = match.group("value")
        _add(
            symbols,
            ctx,
            name,
            "constant" if _looks_upper(name) else "global",
            line=line,
            default=value,
        )

    return symbols


# --------------------------------------------------------------------------------------
# SQL / 通用退化规则
# --------------------------------------------------------------------------------------

_SQL_DECLARE_RE = re.compile(
    r"\bdeclare\s+(?P<name>[A-Za-z_]\w*)\s+(?P<type>[A-Za-z_]\w*(?:\s*\([^)]*\))?)"
    r"(?:\s*(?::=|=)\s*(?P<value>[^;]+))?",
    re.IGNORECASE,
)
_SQL_AS_RE = re.compile(r"\bas\s+(?P<name>[A-Za-z_]\w*)", re.IGNORECASE)
# SQL 里 as 后面跟这些词时是语法关键字而不是别名
_SQL_AS_NOISE = frozenset(
    {"select", "from", "where", "join", "inner", "outer", "left", "right", "full", "cross", "on", "group", "order", "by", "having", "union", "values", "set", "with"}
)


def _detect_sql(ctx: _Ctx, function_name: str) -> List[DetectedSymbol]:
    """SQL 只做保守识别: ``DECLARE x INT`` 与 ``AS 别名``。

    这是刻意的浅实现 —— SQL 的"变量"大多数是列名/别名, 贸然识别反而会刷屏。
    """
    symbols: List[DetectedSymbol] = []
    for match in _SQL_DECLARE_RE.finditer(ctx.masked):
        _add(
            symbols,
            ctx,
            match.group("name"),
            "local",
            type=match.group("type"),
            line=ctx.line_of(match.start()),
            default=match.group("value") or "",
        )
    for match in _SQL_AS_RE.finditer(ctx.masked):
        name = match.group("name")
        if name.lower() in _SQL_AS_NOISE:
            continue
        line = ctx.line_of(match.start())
        _add(
            symbols,
            ctx,
            name,
            "field",
            line=line,
            detail=f"{_LINE_TEMPLATE.format(line)} (别名)",
        )
    return symbols


_GENERIC_ASSIGN_RE = re.compile(
    r"(?:(?:^|[;({])\s*(?P<keyword>let|var|const|final|val|readonly|declare|dim)\s+)?"
    r"(?P<name>[A-Za-z_]\w*)\s*:?[ \t]*(?:=[^=]|:=)(?!=)",
    re.MULTILINE,
)


def _detect_generic(ctx: _Ctx, function_name: str) -> List[DetectedSymbol]:
    """兜底规则: 只认出最常见的 ``name = value`` / ``let name = value``。"""
    symbols: List[DetectedSymbol] = []
    for match in _GENERIC_ASSIGN_RE.finditer(ctx.masked):
        name = match.group("name")
        keyword = match.group("keyword") or ""
        if name in _NOISE_WORDS:
            continue
        line = ctx.line_of(match.start())
        line_text = ctx.line_text(line)
        if "(" in line_text.split("=", 1)[0]:
            continue
        kind = "constant" if keyword == "const" or _looks_upper(name) else "local"
        _add(symbols, ctx, name, kind, line=line)
    return symbols


# --------------------------------------------------------------------------------------
# 分派
# --------------------------------------------------------------------------------------

_PYTHON_LIKE = frozenset({"python"})
_C_LIKE = frozenset({"c", "cpp"})
_JAVA_LIKE = frozenset({"java", "csharp", "kotlin", "swift"})
_JS_LIKE = frozenset({"javascript", "typescript"})


def detect_symbols(language: str, code: str, function_name: str = "") -> list[DetectedSymbol]:
    """从 ``code`` 中探测符号声明.

    :param language: 语言标识 (别名/扩展名均可, 内部会规范化)
    :param code: 源码文本
    :param function_name: 只关心某个函数时传入其名字; 空串表示全文件扫描
    :returns: 顺序稳定的 :class:`DetectedSymbol` 列表; 任何异常都退化为空列表
    """
    try:
        if code is None:
            return []
        if not isinstance(code, str):
            code = str(code)
        if not code.strip():
            return []

        lang = normalize_language(language)
        ctx = _Ctx(lang, code)
        name = str(function_name or "").strip()

        def run(target: str) -> list[DetectedSymbol]:
            if lang in _PYTHON_LIKE:
                return _detect_python(ctx, target)
            if lang in _C_LIKE:
                return _detect_c_family(ctx, target, lang)
            if lang == "go":
                return _detect_go(ctx, target)
            if lang in _JAVA_LIKE:
                return _detect_java_family(ctx, target, lang)
            if lang == "rust":
                return _detect_rust(ctx, target)
            if lang == "php":
                return _detect_php(ctx, target)
            if lang in _JS_LIKE:
                return _detect_javascript(ctx, target, lang)
            if lang == "ruby":
                return _detect_ruby(ctx, target)
            if lang == "lua":
                return _detect_lua(ctx, target)
            if lang == "bash":
                return _detect_bash(ctx, target)
            if lang == "sql":
                return _detect_sql(ctx, target)
            return _detect_generic(ctx, target)

        found = run(name)

        # 按名字没找到 (例如模型里的函数名和代码里的不一致, 或代码里改了函数名),
        # 就退化为"扫全文件并取第一个函数" —— 否则用户会看到"检测不到任何变量"。
        if not found and name:
            found = run("")

        # 兜底补充: 专用规则一个都没命中时, 至少给出通用赋值结果
        if not found:
            found = _detect_generic(ctx, name)
        return _organize(found)
    except Exception:  # noqa: BLE001 - 探测失败绝不能影响编辑器
        return []


def symbol_key(language: str, name: str, kind: str) -> str:
    """稳定去重键: 语言 + 名字 + 类别.

    语言为 ``plaintext`` (即调用方没给语言) 时退化为 ``*``, 这样同一份注解在
    重新探测时可以跨语言命中, 不会凭空产生重复项。
    """
    try:
        lang = normalize_language(language)
    except Exception:  # noqa: BLE001
        lang = "plaintext"
    if lang == "plaintext":
        lang = "*"
    cleaned = _clean_name(name).casefold()
    return f"{lang}:{kind}:{cleaned}"


def merge_symbols(
    existing: Sequence[DetectedSymbol], detected: Sequence[DetectedSymbol]
) -> list[DetectedSymbol]:
    """保序合并旧注解与新探测结果.

    规则:
    - ``existing`` 中的符号全部保留且顺序不变; 重新探测到的同一符号会刷新
      ``type`` / ``detail`` / ``default``, 但 ``meaning`` 始终沿用用户填写的旧值;
    - ``detected`` 中多出来的新符号按探测顺序追加;
    - 同一个 ``symbol_key`` 绝不重复。
    """
    try:
        old_items = list(existing or [])
        fresh_items = list(detected or [])
    except TypeError:
        return []

    fresh: Dict[str, DetectedSymbol] = {}
    for symbol in fresh_items:
        if not isinstance(symbol, DetectedSymbol):
            continue
        fresh.setdefault(symbol_key("", symbol.name, symbol.kind), symbol)

    merged: List[DetectedSymbol] = []
    used: set = set()
    for old in old_items:
        if not isinstance(old, DetectedSymbol):
            continue
        key = symbol_key("", old.name, old.kind)
        if key in used:
            continue
        used.add(key)
        current = fresh.get(key)
        if current is None:
            # 代码里已找不到: 保留用户此前的注解, 但不再声称知道它的位置
            merged.append(
                DetectedSymbol(
                    name=old.name,
                    kind=old.kind,
                    type=old.type,
                    detail="",
                    default=old.default,
                    meaning=old.meaning,
                )
            )
            continue
        merged.append(
            DetectedSymbol(
                name=current.name or old.name,
                kind=old.kind,
                type=current.type or old.type,
                detail=current.detail,
                default=current.default or old.default,
                meaning=old.meaning,
            )
        )

    for symbol in fresh_items:
        if not isinstance(symbol, DetectedSymbol):
            continue
        key = symbol_key("", symbol.name, symbol.kind)
        if key in used:
            continue
        used.add(key)
        merged.append(symbol)

    return merged


def supported_languages() -> list[str]:
    """返回实现了专门规则的语言 id (其余语言走通用兜底规则)。"""
    return list(_LANGUAGE_IDS)


def describe_symbols(language: str, code: str, function_name: str = "") -> dict:
    """``detect_symbols`` 的结构化摘要, 方便测试与自检输出。"""
    symbols = detect_symbols(language, code, function_name)
    counts: Dict[str, int] = {kind: 0 for kind in KIND_ORDER}
    for symbol in symbols:
        counts[symbol.kind] = counts.get(symbol.kind, 0) + 1
    return {
        "language": normalize_language(language),
        "function": str(function_name or ""),
        "counts": counts,
        "symbols": [asdict(symbol) for symbol in symbols],
    }


__all__ = [
    "DetectedSymbol",
    "KIND_LABELS",
    "KIND_ORDER",
    "describe_symbols",
    "detect_symbols",
    "mask_code",
    "merge_symbols",
    "supported_languages",
    "symbol_key",
]
