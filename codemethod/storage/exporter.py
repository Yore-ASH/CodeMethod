"""导出 / 导入: Markdown、JSON 与 ZIP (含真实代码文件).

除了自家的 ``.cmdb`` / ``.cmj`` 容器, 还支持把库导出成人类可读的形式:

* **Markdown** — 一个条目一节, 每种语言一个带语法标注的围栏代码块, 末尾附修订历史;
* **JSON** — 便于其它程序消费;
* **ZIP** — 目录结构: 每个条目一个文件夹, 内含 ``entry.json``、``README.md``
  与真实的源码文件 (按语言扩展名), 可以直接解压进 IDE 继续开发。

ZIP 导出包含 ``codemethod.json`` (完整仓储数据), 因此可以无损导回。
"""

from __future__ import annotations

import json
import os
import time
import zipfile
from typing import Any, Dict, Iterable, List, Optional

from .. import APP_NAME, APP_VERSION
from ..core.functions import Function
from ..core.languages import get_language
from ..core.models import STATUS_LABELS, Entry, Revision, format_ts
from ..core.repository import Repository
from ..core.spaces import Space

# 语言 id -> Markdown 围栏标注 / 文件扩展名
_FENCE = {
    "c": "c",
    "cpp": "cpp",
    "go": "go",
    "java": "java",
    "python": "python",
    "php": "php",
    "rust": "rust",
    "javascript": "javascript",
    "typescript": "typescript",
    "csharp": "csharp",
    "kotlin": "kotlin",
    "swift": "swift",
    "ruby": "ruby",
    "lua": "lua",
    "sql": "sql",
    "bash": "bash",
    "json": "json",
    "yaml": "yaml",
    "html": "html",
    "css": "css",
    "markdown": "markdown",
    "plaintext": "text",
}


def _fence(language: str) -> str:
    return _FENCE.get(language, "text")


def _safe_name(text: str, *, fallback: str = "entry") -> str:
    """把标题转换成安全的文件名。"""
    cleaned = []
    for char in (text or "").strip():
        if char.isalnum() or char in "-_":
            cleaned.append(char)
        elif char in " \t":
            cleaned.append("_")
    name = "".join(cleaned).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    return (name or fallback)[:60]


# --------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------


def entry_to_markdown(entry: Entry, *, revisions: Optional[List[Revision]] = None) -> str:
    """把单个条目渲染成 Markdown。"""
    lines: List[str] = []
    lines.append(f"# {entry.display_title}")
    lines.append("")
    meta: List[str] = [f"状态: **{entry.status_label}**"]
    if entry.favorite:
        meta.append("★ 收藏")
    meta.append(f"更新: {format_ts(entry.updated_at)}")
    meta.append(f"条目 ID: `{entry.id}`")
    lines.append(" · ".join(meta))
    lines.append("")
    if entry.tags:
        lines.append("标签: " + " ".join(f"`#{t}`" for t in entry.tags))
        lines.append("")

    lines.append("## 描述")
    lines.append("")
    lines.append(entry.description.strip() or "_（未填写）_")
    lines.append("")

    lines.append("## 通用前置要求")
    lines.append("")
    lines.append(entry.prerequisites.strip() or "_（未填写）_")
    lines.append("")

    lines.append(f"## 实现 ({len(entry.active_implementations)} 种语言)")
    lines.append("")
    # 先给一张"每种语言各自需要什么"的对照表 —— 前置要求是分语言的
    if entry.active_implementations:
        lines.append("| 语言 | 前置要求 |")
        lines.append("| --- | --- |")
        for impl in entry.active_implementations:
            need = impl.prerequisites.strip().replace("\n", " ").replace("|", "\\|") or "—"
            lines.append(f"| {get_language(impl.language).name} | {need} |")
        lines.append("")

    for impl in entry.active_implementations:
        spec = get_language(impl.language)
        heading = f"### {spec.name}"
        if impl.title:
            heading += f" · {impl.title}"
        lines.append(heading)
        lines.append("")
        if impl.filename:
            lines.append(f"文件: `{impl.filename}`")
            lines.append("")
        lines.append("**前置要求**: " + (impl.prerequisites.strip() or "_（未填写）_"))
        lines.append("")
        if impl.notes:
            lines.append(f"> {impl.notes}")
            lines.append("")
        lines.append(f"```{_fence(impl.language)}")
        lines.append(impl.code.rstrip("\n"))
        lines.append("```")
        lines.append("")

    removed = [impl for impl in entry.implementations if impl.deleted]
    if removed:
        lines.append("## 已删除的实现")
        lines.append("")
        for impl in removed:
            lines.append(f"* {get_language(impl.language).name} · {impl.display_title}")
        lines.append("")

    if revisions:
        lines.append("## 修订历史")
        lines.append("")
        lines.append("| 时间 | 动作 | 说明 | 修订 ID |")
        lines.append("| --- | --- | --- | --- |")
        for rev in revisions:
            summary = (rev.summary or "").replace("|", "\\|")
            lines.append(
                f"| {format_ts(rev.timestamp)} | {rev.action_label} | {summary} | `{rev.id}` |"
            )
        lines.append("")
    return "\n".join(lines)


def space_to_markdown(space: Space, *, revisions: Optional[List[Revision]] = None) -> str:
    """把一个独立空间渲染成 Markdown (语言占比 + 目录树 + README 摘要)。"""
    lines: List[str] = []
    lines.append(f"# 空间: {space.display_title}")
    lines.append("")
    meta = [f"状态: **{space.status_label}**"]
    if space.favorite:
        meta.append("★ 收藏")
    meta.append(f"更新: {format_ts(space.updated_at)}")
    meta.append(f"空间 ID: `{space.id}`")
    lines.append(" · ".join(meta))
    lines.append("")
    if space.tags:
        lines.append("标签: " + " ".join(f"`#{t}`" for t in space.tags))
        lines.append("")

    # 语言占比 (GitHub 风格)
    shares = space.language_shares()
    if shares:
        lines.append("## 语言占比")
        lines.append("")
        lines.append("```")
        total_bar = 40
        bar = "".join(
            _bar_char(share) * max(1, round(share.percent / 100 * total_bar)) for share in shares
        )
        lines.append(bar)
        lines.append("```")
        lines.append("")
        lines.append("| 语言 | 占比 | 字节 | 文件 | 行数 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for share in shares:
            lines.append(
                f"| {share.name} | {share.percent_label} | {share.bytes} "
                f"| {share.files} | {share.lines} |"
            )
        lines.append("")

    lines.append("## 前置要求")
    lines.append("")
    lines.append(space.prerequisites.strip() or "_（未填写）_")
    lines.append("")

    lines.append("## 描述")
    lines.append("")
    lines.append(space.description.strip() or "_（未填写）_")
    lines.append("")

    lines.append(f"## 项目结构 ({len(space.files)} 个文件)")
    lines.append("")
    if space.files:
        lines.append("```")
        for file in sorted(space.files, key=lambda f: f.path):
            size = f"{file.size} B"
            lines.append(f"{file.path:<52} {get_language(file.language).name:<12} {size}")
        lines.append("```")
        lines.append("")

    for readme in space.readme_files:
        lines.append(f"### {readme.path}")
        lines.append("")
        body = readme.content.strip()
        lines.append(body or "_（空文件）_")
        lines.append("")

    if revisions:
        lines.append("## 修订历史")
        lines.append("")
        lines.append("| 时间 | 动作 | 说明 | 修订 ID |")
        lines.append("| --- | --- | --- | --- |")
        for rev in revisions:
            lines.append(
                f"| {format_ts(rev.timestamp)} | {rev.action_label} "
                f"| {(rev.summary or '').replace('|', chr(92) + '|')} | `{rev.id}` |"
            )
        lines.append("")
    return "\n".join(lines)


def _bar_char(share) -> str:
    """占比条用方块表示, 这里统一用同一个字符 (颜色交给 Markdown 渲染器)。"""
    return "█"


def function_to_markdown(function: Function, *, revisions: Optional[List[Revision]] = None) -> str:
    """把一个函数体渲染成 Markdown (代码 + **变量含义表**)。"""
    lines: List[str] = []
    lines.append(f"# 函数体: {function.display_title}")
    lines.append("")
    meta = [f"语言: **{function.language_name}**", f"状态: **{function.status_label}**"]
    if function.favorite:
        meta.append("★ 收藏")
    meta.append(f"更新: {format_ts(function.updated_at)}")
    lines.append(" · ".join(meta))
    lines.append("")
    if function.tags:
        lines.append("标签: " + " ".join(f"`#{t}`" for t in function.tags))
        lines.append("")

    lines.append("## 前置要求")
    lines.append("")
    lines.append(function.prerequisites.strip() or "_（未填写）_")
    lines.append("")

    if function.description.strip():
        lines.append("## 说明")
        lines.append("")
        lines.append(function.description.strip())
        lines.append("")

    if function.signature.strip():
        lines.append("## 函数签名")
        lines.append("")
        lines.append(f"```{_fence(function.language)}")
        lines.append(function.signature.strip())
        lines.append("```")
        lines.append("")

    lines.append("## 变量含义")
    lines.append("")
    if function.symbols:
        lines.append("| 名称 | 种类 | 类型 | 默认值 | 含义 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for symbol in function.symbols:
            meaning = symbol.meaning.replace("|", chr(92) + "|") or "_（未填写）_"
            lines.append(
                f"| `{symbol.name}` | {symbol.kind_label} | {symbol.type or '—'} "
                f"| {symbol.default or '—'} | {meaning} |"
            )
        lines.append("")
    else:
        lines.append("_（尚未检测到变量）_")
        lines.append("")

    lines.append("## 代码")
    lines.append("")
    lines.append(f"```{_fence(function.language)}")
    lines.append(function.code.rstrip("\n"))
    lines.append("```")
    lines.append("")

    if revisions:
        lines.append("## 修订历史")
        lines.append("")
        lines.append("| 时间 | 动作 | 说明 | 修订 ID |")
        lines.append("| --- | --- | --- | --- |")
        for rev in revisions:
            lines.append(
                f"| {format_ts(rev.timestamp)} | {rev.action_label} "
                f"| {(rev.summary or '').replace('|', chr(92) + '|')} | `{rev.id}` |"
            )
        lines.append("")
    return "\n".join(lines)


def library_to_markdown(repo: Repository, entries: Optional[Iterable[Entry]] = None) -> str:
    """把整个库渲染成 Markdown (模块 + 空间 + 函数体)。"""
    stats = repo.statistics()
    modules = list(entries) if entries is not None else repo.entries_list()
    spaces = [s for s in repo.spaces.values() if not s.deleted]
    functions = [f for f in repo.functions.values() if not f.deleted]

    lines: List[str] = []
    lines.append(f"# {repo.name}")
    lines.append("")
    if repo.description:
        lines.append(repo.description.strip())
        lines.append("")
    lines.append(
        f"> 由 {APP_NAME} {APP_VERSION} 导出 · {format_ts(time.time())} · "
        f"{stats['modules']} 个模块 · {stats['spaces']} 个空间 · "
        f"{stats['functions']} 个函数体 · {stats['implementations']} 个实现 · "
        f"{stats['tags']} 个标签 · {stats['revisions']} 条修订"
    )
    lines.append("")

    sections = (
        ("模块", modules, entry_to_markdown),
        ("独立空间", spaces, space_to_markdown),
        ("函数体", functions, function_to_markdown),
    )
    if any(items for _title, items, _fn in sections):
        lines.append("## 目录")
        lines.append("")
        for title, items, _fn in sections:
            if not items:
                continue
            lines.append(f"**{title}** ({len(items)})")
            lines.append("")
            for index, item in enumerate(items, 1):
                lines.append(
                    f"{index}. {item.display_title} — "
                    + (", ".join("#" + t for t in item.tags) or "无标签")
                )
            lines.append("")

    for title, items, render in sections:
        if not items:
            continue
        lines.append(f"# {title} ({len(items)})")
        lines.append("")
        for item in items:
            lines.append("---")
            lines.append("")
            lines.append(render(item, revisions=repo.revisions(item.id)))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------------------


def entry_to_json(entry: Entry, *, revisions: Optional[List[Revision]] = None, indent: int = 2) -> str:
    data = entry.to_dict()
    if revisions:
        data["revisions"] = [rev.to_dict() for rev in revisions]
    return json.dumps(data, ensure_ascii=False, indent=indent)


def library_to_json(repo: Repository, *, indent: int = 2, include_history: bool = True) -> str:
    document = {
        "format": "codemethod-json-export",
        "format_version": "1.0",
        "generator": f"{APP_NAME} {APP_VERSION}",
        "exported_at": time.time(),
        "stats": repo.statistics(),
        "repository": repo.to_dict(include_history=include_history),
    }
    return json.dumps(document, ensure_ascii=False, indent=indent)


def read_json_export(text: str) -> Dict[str, Any]:
    """读取 JSON 导出 (或裸仓储 dict), 返回仓储 dict。"""
    data = json.loads(text)
    if isinstance(data, dict) and "repository" in data:
        return data["repository"]
    if isinstance(data, dict) and "entries" in data:
        return data
    raise ValueError("不是有效的 CodeMethod JSON 导出")


# --------------------------------------------------------------------------------------
# 文件写入
# --------------------------------------------------------------------------------------


def export_markdown(repo: Repository, path: str, entries: Optional[Iterable[Entry]] = None) -> int:
    text = library_to_markdown(repo, entries)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return len(text)


def export_json(repo: Repository, path: str, *, include_history: bool = True) -> int:
    text = library_to_json(repo, include_history=include_history)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return len(text)


def export_zip(repo: Repository, path: str, entries: Optional[Iterable[Entry]] = None) -> int:
    """导出为 ZIP。

    目录结构::

        codemethod.json                 完整数据 (可以再导回)
        README.md                       总索引
        modules/<模块名>/               每种语言一个源码文件 + entry.json
        spaces/<空间名>/                **真实项目目录树**, 与空间里一模一样
        functions/<函数名>.md           代码 + 变量含义表
        functions/<函数名>.json

    返回写入的对象数量 (模块 + 空间 + 函数体)。
    """
    selected = list(entries) if entries is not None else repo.entries_list()
    spaces = [s for s in repo.spaces.values() if not s.deleted]
    functions = [f for f in repo.functions.values() if not f.deleted]
    used_names: Dict[str, int] = {}

    def unique_folder(base: str) -> str:
        count = used_names.get(base, 0)
        used_names[base] = count + 1
        return base if count == 0 else f"{base}_{count + 1}"

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(
            "codemethod.json",
            json.dumps(
                {
                    "format": "codemethod-zip-export",
                    "format_version": "1.0",
                    "generator": f"{APP_NAME} {APP_VERSION}",
                    "exported_at": time.time(),
                    "repository": repo.to_dict(include_history=True),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr("README.md", library_to_markdown(repo, selected))

        # ---- 模块 ----
        for entry in selected:
            folder = f"modules/{unique_folder(_safe_name(entry.display_title))}"
            archive.writestr(
                f"{folder}/entry.json",
                entry_to_json(entry, revisions=repo.revisions(entry.id)),
            )
            archive.writestr(
                f"{folder}/README.md",
                entry_to_markdown(entry, revisions=repo.revisions(entry.id)),
            )
            file_names: Dict[str, int] = {}
            for impl in entry.active_implementations:
                filename = impl.filename or f"main.{get_language(impl.language).extensions[0]}"
                filename = os.path.basename(filename)
                seen = file_names.get(filename, 0)
                file_names[filename] = seen + 1
                if seen:
                    stem, ext = os.path.splitext(filename)
                    filename = f"{stem}_{seen + 1}{ext}"
                archive.writestr(f"{folder}/{filename}", impl.code)

        # ---- 独立空间: 按原始路径还原成真实项目目录树 ----
        for space in spaces:
            folder = f"spaces/{unique_folder(_safe_name(space.display_title))}"
            archive.writestr(
                f"{folder}/.codemethod-space.json",
                json.dumps(space.to_dict(), ensure_ascii=False, indent=2),
            )
            archive.writestr(
                f"{folder}/.codemethod-space.md",
                space_to_markdown(space, revisions=repo.revisions(space.id)),
            )
            for file in space.files:
                if not file.path:
                    continue
                # 二进制文件在库里只存了大小, 导出时放一个说明占位
                body = (
                    f"（二进制文件, {file.size} 字节; CodeMethod 只记录了大小, 未保存内容）\n"
                    if file.binary
                    else file.content
                )
                archive.writestr(f"{folder}/{file.path}", body)

        # ---- 函数体 ----
        for function in functions:
            base = unique_folder(_safe_name(function.display_title))
            archive.writestr(
                f"functions/{base}.md",
                function_to_markdown(function, revisions=repo.revisions(function.id)),
            )
            archive.writestr(
                f"functions/{base}.json",
                entry_to_json(function, revisions=repo.revisions(function.id)),
            )

    return len(selected) + len(spaces) + len(functions)


def read_zip_export(path: str) -> Dict[str, Any]:
    """读取由 :func:`export_zip` 产生的 ZIP, 返回仓储 dict。"""
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if "codemethod.json" not in names:
            raise ValueError("ZIP 中缺少 codemethod.json (不是 CodeMethod 导出的包)")
        payload = json.loads(archive.read("codemethod.json").decode("utf-8"))
    repository = payload.get("repository")
    if not isinstance(repository, dict) or "entries" not in repository:
        raise ValueError("ZIP 中的 codemethod.json 结构不完整")
    return repository


__all__ = [
    "entry_to_markdown",
    "space_to_markdown",
    "function_to_markdown",
    "library_to_markdown",
    "entry_to_json",
    "library_to_json",
    "read_json_export",
    "export_markdown",
    "export_json",
    "export_zip",
    "read_zip_export",
]
