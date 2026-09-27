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
from ..core.languages import get_language
from ..core.models import STATUS_LABELS, Entry, Revision, format_ts
from ..core.repository import Repository

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


def library_to_markdown(repo: Repository, entries: Optional[Iterable[Entry]] = None) -> str:
    """把整个库 (或指定条目) 渲染成 Markdown 文档。"""
    stats = repo.statistics()
    selected = list(entries) if entries is not None else repo.entries_list()

    lines: List[str] = []
    lines.append(f"# {repo.name}")
    lines.append("")
    if repo.description:
        lines.append(repo.description.strip())
        lines.append("")
    lines.append(
        f"> 由 {APP_NAME} {APP_VERSION} 导出 · {format_ts(time.time())} · "
        f"{stats['entries']} 个条目 · {stats['implementations']} 个实现 · "
        f"{stats['languages']} 种语言 · {stats['tags']} 个标签 · {stats['revisions']} 条修订"
    )
    lines.append("")

    if selected:
        lines.append("## 目录")
        lines.append("")
        for index, entry in enumerate(selected, 1):
            anchor = _safe_name(entry.display_title, fallback=f"entry-{index}").lower()
            lines.append(
                f"{index}. [{entry.display_title}](#{anchor}) — "
                + (", ".join("#" + t for t in entry.tags) or "无标签")
            )
        lines.append("")

    for entry in selected:
        lines.append("---")
        lines.append("")
        lines.append(entry_to_markdown(entry, revisions=repo.revisions(entry.id)))
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
    """导出为 ZIP: 每个条目一个文件夹 + 真实源码文件 + 完整数据。

    返回写入的条目数。
    """
    selected = list(entries) if entries is not None else repo.entries_list()
    used_names: Dict[str, int] = {}

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

        for entry in selected:
            base = _safe_name(entry.display_title)
            count = used_names.get(base, 0)
            used_names[base] = count + 1
            folder = base if count == 0 else f"{base}_{count + 1}"

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
    return len(selected)


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
    "library_to_markdown",
    "entry_to_json",
    "library_to_json",
    "read_json_export",
    "export_markdown",
    "export_json",
    "export_zip",
    "read_zip_export",
]
