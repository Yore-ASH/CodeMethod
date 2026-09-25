"""基于语言注册表的通用语法高亮器.

同一个 :class:`CodeHighlighter` 通过 :class:`~codemethod.core.languages.LanguageSpec`
驱动, 因此支持语言只取决于注册表内容。当前内置支持 C / C++ / Go / Java / Python /
PHP / Rust 以及 JavaScript、TypeScript、C#、Kotlin、Swift、Ruby、Lua、SQL、Shell、
JSON、YAML、HTML、CSS、Markdown 等 20+ 种语言。

实现分两步:

1. **跨行扫描器** (纯 Python 字符扫描) 负责块注释、三引号字符串等跨行结构,
   它理解字符串与转义, 因此 ``// 这不是注释里的 /*`` 不会被误判;
2. **正则规则** (QRegularExpression) 负责单行 token。

应用顺序遵循"后写覆盖先写": 关键字 → 函数名 → 注解/预处理 → 字符串 →
跨行结构 → 行注释。这样注释与字符串总能压住关键字, 视觉上才正确。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from ..core.languages import LANGUAGES, LanguageSpec, get_language, normalize_language
from .theme import DEFAULT_THEME, Theme

# --------------------------------------------------------------------------------------
# 跨行扫描状态
# --------------------------------------------------------------------------------------

STATE_NORMAL = 0
STATE_BLOCK_COMMENT = 1
STATE_TRIPLE_BASE = 2  # 2 + 三引号分隔符下标

# 扫描产出的 span 种类 -> token 名
SPAN_COMMENT = "comment"
SPAN_STRING = "string"


@dataclass(frozen=True)
class _Rule:
    pattern: QRegularExpression
    token: str


# --------------------------------------------------------------------------------------
# 规则构造
# --------------------------------------------------------------------------------------


def _rx(pattern: str, *, case_insensitive: bool = False) -> QRegularExpression:
    options = QRegularExpression.PatternOption.NoPatternOption
    if case_insensitive:
        options |= QRegularExpression.PatternOption.CaseInsensitiveOption
    regex = QRegularExpression(pattern)
    regex.setPatternOptions(options)
    return regex


def _alt(words: Sequence[str]) -> str:
    """把词表拼成正则或分支 (按长度降序, 避免短词抢先匹配)。"""
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


def _number_pattern() -> str:
    return (
        r"\b(?:"
        r"0[xX][0-9a-fA-F_']+|0[bB][01_']+|0[oO][0-7_']+"
        r"|\d[\d_']*(?:\.[\d_']*)?(?:[eE][+-]?\d+)?[fFlLdDuU]*"
        r"|\.[\d_']+(?:[eE][+-]?\d+)?[fFlL]?"
        r")\b"
    )


# 标识符: 用 PCRE2 的 Unicode 属性而非 \uXXXX 转义
# (Qt 的 QRegularExpression 不支持 \u 转义, 用 \p{L} 才能同时支持中文等标识符)
_IDENT = r"[\p{L}_][\p{L}\p{N}_]*"


@lru_cache(maxsize=64)
def build_rules(language: str) -> Tuple[_Rule, ...]:
    """为某种语言构建正则规则序列 (带缓存)。

    非法正则会被安全跳过 (Qt 的 QRegularExpression 不会抛异常, 只会产生"无效对象",
    因此这里显式检查 :meth:`isValid`)。
    """
    spec = get_language(language)
    ci = not spec.case_sensitive
    rules: List[_Rule] = []

    def add(pattern: str, token: str, *, case_insensitive: bool = False) -> None:
        regex = _rx(pattern, case_insensitive=case_insensitive or ci)
        if not regex.isValid():
            return
        rules.append(_Rule(regex, token))

    # ---- 数字 ----
    add(_number_pattern(), "number")

    # ---- 常量 ----
    if spec.constants:
        add(rf"\b(?:{_alt(spec.constants)})\b", "constant")

    # ---- 内建 ----
    if spec.builtins:
        add(rf"\b(?:{_alt(spec.builtins)})\b", "builtin")

    # ---- 类型 ----
    if spec.types:
        add(rf"\b(?:{_alt(spec.types)})\b", "type")

    # ---- 关键字 ----
    controls = [k for k in spec.control_keywords]
    plain = [k for k in spec.keywords if k not in set(spec.control_keywords)]
    if plain:
        add(rf"\b(?:{_alt(plain)})\b", "keyword")
    if controls:
        add(rf"\b(?:{_alt(controls)})\b", "control")

    # ---- 函数名: 标识符后紧跟 ( ----
    if spec.function_call_highlight:
        add(rf"\b({_IDENT})\s*(?=\()", "function")

    # 属性/成员访问 (obj.field)
    add(rf"(?<=\.)({_IDENT})", "attribute")

    # ---- 变量前缀 ($var / @var) ----
    if spec.variable_prefix:
        prefix = re.escape(spec.variable_prefix)
        if spec.variable_prefix == "$":
            add(rf"\$\w+", "variable")
        elif spec.variable_prefix == "@":
            add(rf"@{_IDENT}", "variable")
        else:
            add(rf"{prefix}\w+", "variable")

    # ---- 注解 / 属性 ----
    if spec.annotation:
        ann = re.escape(spec.annotation)
        if spec.annotation.startswith("[["):
            add(r"#?\[\[[\s\S]*?\]\]", "annotation")
        elif spec.annotation in ("#[", "@"):
            add(rf"{ann}{_IDENT}(?:\([^)]*\))?", "annotation")

    # ---- 预处理指令 ----
    if spec.preprocessor:
        add(rf"^\s*{re.escape(spec.preprocessor)}\s*\w+", "preprocessor")

    # ---- 字符串 (单行) ----
    for delim in spec.string_delimiters:
        if len(delim) != 1:
            continue
        d = re.escape(delim)
        # 可选的字面量前缀 (r/b/f/u/L...), 结尾引号可选以容忍未闭合的字符串
        add(rf"(?:[rRbBuUfF]{{0,2}}){d}(?:\\.|[^\\{delim}\r\n])*{d}?", "string")
    if "`" in spec.raw_string_prefixes:
        add(r"`[^`\r\n]*`?", "string")

    # ---- 字符字面量 ----
    if spec.char_delimiter:
        ch = re.escape(spec.char_delimiter)
        add(rf"{ch}(?:\\.|[^\\{spec.char_delimiter}\r\n]){ch}", "string")

    # ---- HTML/XML 标签 ----
    if spec.id in ("html", "markdown"):
        add(r"</?([A-Za-z][\w:-]*)", "tag")
        add(rf"({_IDENT})\s*=", "attribute")

    # ---- CSS 选择器/属性 ----
    if spec.id in ("css",):
        add(r"[.#][A-Za-z_-][\w-]*", "type")
        add(rf"\b([a-z-]+)\s*(?=:)", "attribute")

    # ---- YAML/JSON 键 ----
    if spec.id in ("yaml", "json"):
        add(rf"(\"[^\"]*\"|'{_IDENT}'|{_IDENT})\s*(?=:)", "key")

    return tuple(rules)


# --------------------------------------------------------------------------------------
# 跨行扫描
# --------------------------------------------------------------------------------------


def scan_multiline(
    spec: LanguageSpec, text: str, start_state: int
) -> Tuple[List[Tuple[int, int, str]], int]:
    """扫描一行, 返回 ``([(起始, 长度, token)], 行末状态)``。

    只负责跨行结构 (块注释 / 三引号字符串) 与行注释, 其余交给正则。
    """
    spans: List[Tuple[int, int, str]] = []
    n = len(text)
    state = start_state

    block_open, block_close = (spec.block_comment or (None, None))
    triples = spec.triple_string_delimiters

    def find_unescaped(needle: str, start: int) -> int:
        index = text.find(needle, start)
        while index != -1:
            backslashes = 0
            probe = index - 1
            while probe >= 0 and text[probe] == "\\":
                backslashes += 1
                probe -= 1
            if backslashes % 2 == 0:
                return index
            index = text.find(needle, index + 1)
        return -1

    i = 0
    # ---- 续接上一行的状态 ----
    if state == STATE_BLOCK_COMMENT and block_close:
        if spec.nested_block_comment and block_open:
            depth = 1
            cursor = 0
            while cursor < n:
                if text.startswith(block_open, cursor):
                    depth += 1
                    cursor += len(block_open)
                elif text.startswith(block_close, cursor):
                    depth -= 1
                    cursor += len(block_close)
                    if depth == 0:
                        break
                else:
                    cursor += 1
            if depth == 0:
                spans.append((0, cursor, SPAN_COMMENT))
                i = cursor
                state = STATE_NORMAL
            else:
                spans.append((0, n, SPAN_COMMENT))
                return spans, STATE_BLOCK_COMMENT
        else:
            index = text.find(block_close)
            if index == -1:
                spans.append((0, n, SPAN_COMMENT))
                return spans, STATE_BLOCK_COMMENT
            end = index + len(block_close)
            spans.append((0, end, SPAN_COMMENT))
            i = end
            state = STATE_NORMAL
    elif state >= STATE_TRIPLE_BASE and triples:
        slot = state - STATE_TRIPLE_BASE
        if slot < len(triples):
            delim = triples[slot]
            index = find_unescaped(delim, 0)
            if index == -1:
                spans.append((0, n, SPAN_STRING))
                return spans, state
            end = index + len(delim)
            spans.append((0, end, SPAN_STRING))
            i = end
            state = STATE_NORMAL
        else:
            state = STATE_NORMAL

    # ---- 主扫描 ----
    line_markers = spec.all_line_comments
    while i < n:
        # 行注释优先: 其后所有内容都是注释 (PHP 等语言有多个行注释标记)
        matched_marker = None
        for marker in line_markers:
            if text.startswith(marker, i):
                matched_marker = marker
                break
        if matched_marker is not None:
            spans.append((i, n - i, SPAN_COMMENT))
            return spans, STATE_NORMAL

        # 块注释
        if block_open and text.startswith(block_open, i):
            if spec.nested_block_comment:
                depth = 1
                cursor = i + len(block_open)
                end = -1
                while cursor < n:
                    if text.startswith(block_open, cursor):
                        depth += 1
                        cursor += len(block_open)
                    elif block_close and text.startswith(block_close, cursor):
                        depth -= 1
                        cursor += len(block_close)
                        if depth == 0:
                            end = cursor
                            break
                    else:
                        cursor += 1
                if end == -1:
                    spans.append((i, n - i, SPAN_COMMENT))
                    return spans, STATE_BLOCK_COMMENT
                spans.append((i, end - i, SPAN_COMMENT))
                i = end
            else:
                index = text.find(block_close, i + len(block_open))
                if index == -1:
                    spans.append((i, n - i, SPAN_COMMENT))
                    return spans, STATE_BLOCK_COMMENT
                end = index + len(block_close)
                spans.append((i, end - i, SPAN_COMMENT))
                i = end
            continue

        # 三引号字符串
        matched_triple = False
        for slot, delim in enumerate(triples):
            if text.startswith(delim, i):
                index = find_unescaped(delim, i + len(delim))
                if index == -1:
                    spans.append((i, n - i, SPAN_STRING))
                    return spans, STATE_TRIPLE_BASE + slot
                end = index + len(delim)
                spans.append((i, end - i, SPAN_STRING))
                i = end
                matched_triple = True
                break
        if matched_triple:
            continue

        # 普通字符串: 跳过其内容, 避免把字符串里的 /* 当成注释
        char = text[i]
        if char in spec.string_delimiters or (
            spec.char_delimiter and char == spec.char_delimiter
        ):
            quote = char
            cursor = i + 1
            while cursor < n:
                if text[cursor] == "\\":
                    cursor += 2
                    continue
                if text[cursor] == quote:
                    cursor += 1
                    break
                cursor += 1
            i = max(cursor, i + 1)
            continue

        i += 1

    return spans, STATE_NORMAL


# --------------------------------------------------------------------------------------
# 高亮器
# --------------------------------------------------------------------------------------


class CodeHighlighter(QSyntaxHighlighter):
    """通用代码高亮器 (线程内单文档使用)。"""

    def __init__(
        self,
        document: QTextDocument,
        language: str = "python",
        theme: Theme = DEFAULT_THEME,
    ) -> None:
        super().__init__(document)
        self._theme = theme
        self._language = normalize_language(language)
        self._formats: Dict[str, QTextCharFormat] = {}
        self._rebuild_formats()

    # ---- 语言 ----
    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> None:
        normalized = normalize_language(language)
        if normalized == self._language:
            return
        self._language = normalized
        self._formats.clear()
        self._rebuild_formats()
        self.rehighlight()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._formats.clear()
        self._rebuild_formats()
        self.rehighlight()

    @property
    def spec(self) -> LanguageSpec:
        return get_language(self._language)

    # ---- 格式 ----
    def _rebuild_formats(self) -> None:
        t = self._theme
        mono = "Consolas"

        def fmt(color: str, *, bold: bool = False, italic: bool = False, underline: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(700)
            f.setFontItalic(italic)
            if underline:
                f.setFontUnderline(True)
            return f

        self._formats = {
            "comment": fmt(t.tok_comment, italic=True),
            "keyword": fmt(t.tok_keyword),
            "control": fmt(t.tok_control),
            "type": fmt(t.tok_type),
            "function": fmt(t.tok_function),
            "string": fmt(t.tok_string),
            "number": fmt(t.tok_number),
            "constant": fmt(t.tok_constant),
            "builtin": fmt(t.tok_builtin),
            "preprocessor": fmt(t.tok_preprocessor),
            "annotation": fmt(t.tok_annotation),
            "variable": fmt(t.tok_variable),
            "operator": fmt(t.tok_operator),
            "tag": fmt(t.tok_tag),
            "attribute": fmt(t.tok_attribute),
            "key": fmt(t.tok_key),
            "error": fmt(t.tok_error, underline=True),
        }
        self._mono = mono

    def _format_for(self, token: str) -> QTextCharFormat:
        return self._formats.get(token) or self._formats["operator"]

    # ---- 核心 ----
    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt 命名
        spec = self.spec
        previous = self.previousBlockState()
        start_state = previous if previous and previous > 0 else STATE_NORMAL

        # 1) 正则规则
        for rule in build_rules(self._language):
            iterator = rule.pattern.globalMatch(text)
            char_format = self._format_for(rule.token)
            while iterator.hasNext():
                match = iterator.next()
                # 有捕获组时只给第 1 组着色 (例如函数名、键)
                if match.lastCapturedIndex() >= 1 and match.capturedStart(1) >= 0:
                    self.setFormat(
                        match.capturedStart(1), match.capturedLength(1), char_format
                    )
                else:
                    self.setFormat(match.capturedStart(), match.capturedLength(), char_format)

        # 2) 跨行结构 / 行注释 (覆盖上一步)
        spans, end_state = scan_multiline(spec, text, start_state)
        for start, length, token in spans:
            if length <= 0:
                continue
            self.setFormat(start, length, self._format_for(token))

        self.setCurrentBlockState(end_state)


# --------------------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------------------


class PlainHighlighter(QSyntaxHighlighter):
    """不做任何着色的占位高亮器 (纯文本)。"""

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        self.setCurrentBlockState(STATE_NORMAL)


def create_highlighter(
    document: QTextDocument,
    language: str,
    theme: Theme = DEFAULT_THEME,
) -> QSyntaxHighlighter:
    """按语言创建高亮器 (纯文本语言返回 :class:`PlainHighlighter`)。"""
    if normalize_language(language) == "plaintext":
        return PlainHighlighter(document)
    return CodeHighlighter(document, language, theme)


def highlighted_tokens(language: str, code: str, theme: Theme = DEFAULT_THEME) -> List[Tuple[str, str]]:
    """离线高亮: 返回 ``[(文本片段, token 名), ...]``。

    语义与 :class:`CodeHighlighter` 完全一致 (后写的规则覆盖先写的), 但不依赖 GUI,
    用于导出 HTML、生成测试断言或做回归对比。
    """
    if not code:
        return []
    spec = get_language(language)
    rules = build_rules(normalize_language(language))

    # 与 QSyntaxHighlighter 一致: 逐行处理 (这样 ^ 锚点按行生效)
    lines = code.split("\n")
    labels: List[List[Optional[str]]] = []
    state = STATE_NORMAL

    for line in lines:
        row: List[Optional[str]] = [None] * len(line)
        for rule in rules:
            iterator = rule.pattern.globalMatch(line)
            while iterator.hasNext():
                match = iterator.next()
                if match.lastCapturedIndex() >= 1 and match.capturedStart(1) >= 0:
                    start, length = match.capturedStart(1), match.capturedLength(1)
                else:
                    start, length = match.capturedStart(), match.capturedLength()
                for pos in range(max(0, start), min(len(row), start + length)):
                    row[pos] = rule.token
        spans, state = scan_multiline(spec, line, state)
        for start, length, token in spans:
            for pos in range(max(0, start), min(len(row), start + length)):
                row[pos] = token
        labels.append(row)

    # 把标签序列压缩成连续片段
    out: List[Tuple[str, str]] = []
    for line, row in zip(lines, labels):
        cursor = 0
        length = len(row)
        while cursor < length:
            token = row[cursor]
            end = cursor + 1
            while end < length and row[end] == token:
                end += 1
            out.append((line[cursor:end], token or "text"))
            cursor = end
        if not row and line == "":
            continue
    # 还原换行 (调用方按 \n 拼接即可, 这里只返回可见片段)
    return out


__all__ = [
    "CodeHighlighter",
    "PlainHighlighter",
    "create_highlighter",
    "build_rules",
    "scan_multiline",
    "highlighted_tokens",
    "STATE_NORMAL",
    "STATE_BLOCK_COMMENT",
    "STATE_TRIPLE_BASE",
]
