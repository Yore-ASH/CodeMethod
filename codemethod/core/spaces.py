"""独立空间 (Space): 一个存在代码库内部、可以拥有完整项目结构的容器.

设计要点
--------
* 空间里的**每一个文件都存进 ``.cmdb``**, 因此整个项目是自包含的 —— 换台机器、
  只带一个文件就能完整还原目录树, 不依赖任何外部路径;
* 文件用**相对路径**标识 (``src/main.py``、``docs/README.md``), 目录结构由路径推导,
  不需要单独维护一棵目录树;
* 语言占比按**字节数**统计 (和 GitHub linguist 的口径一致), 并区分
  ``code`` / ``markup`` / ``data`` / ``prose`` 四类, 占比条默认只算代码;
* 自动索引全部 ``README.md`` 供检索。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import posixpath
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .languages import detect_language_from_filename, get_language, normalize_language
from .models import (
    STATUS_LABELS,
    STATUS_ORDER,
    format_ts,
    new_id,
    normalize_tags,
    utcnow,
)

# 被视为"项目说明文件"的名字 (大小写不敏感), 用于 README 检索
README_NAMES = frozenset(
    {"readme", "readme.md", "readme.rst", "readme.txt", "readme.markdown"}
)

# 二进制/不可编辑的扩展名: 默认按二进制处理
BINARY_EXTENSIONS = frozenset(
    {
        "png", "jpg", "jpeg", "gif", "bmp", "ico", "webp", "pdf", "zip", "gz", "bz2",
        "xz", "7z", "rar", "exe", "dll", "so", "dylib", "bin", "o", "a", "class",
        "jar", "pyc", "pyo", "woff", "woff2", "ttf", "otf", "mp3", "mp4", "avi",
        "mov", "wav", "db", "sqlite", "cmdb",
    }
)

# 可以直接在界面里预览的图片类型
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "webp", "ico"})

MAX_FILE_BYTES = 2 * 1024 * 1024      # 单文件文本内容上限, 防止库被巨型文件撑爆
MAX_BINARY_BYTES = 4 * 1024 * 1024    # 单个二进制文件的嵌入上限
MAX_SPACE_BINARY_BYTES = 32 * 1024 * 1024   # 单个空间里全部嵌入二进制的总量上限
MAX_FILES_PER_SPACE = 2000

# 十六进制预览一次取多少字节
HEX_PREVIEW_BYTES = 512


def is_binary_path(path: str) -> bool:
    name = posixpath.basename(path or "")
    if "." not in name:
        return False
    return name.rsplit(".", 1)[-1].lower() in BINARY_EXTENSIONS


def is_image_path(path: str) -> bool:
    name = posixpath.basename(path or "")
    if "." not in name:
        return False
    return name.rsplit(".", 1)[-1].lower() in IMAGE_EXTENSIONS


def looks_binary(data: bytes) -> bool:
    """按**内容**判断一段字节是不是二进制。

    扩展名不可靠 (``.dat`` / 无扩展名的可执行文件), 所以导入外部文件时还要看内容:

    * 出现 ``NUL`` 字节 → 二进制 (文本文件不会有);
    * 否则尝试按 UTF-8 解码, 解不开 → 二进制。

    这条规则对中国用户很重要 —— 不能简单地按"非 ASCII 字节比例"判断, 那样
    一个纯中文的 README 会被误判成二进制。
    """
    if not data:
        return False
    if b"\x00" in data[:8192]:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def decode_text(data: bytes) -> Optional[str]:
    """能当文本读就返回文本, 否则返回 ``None``。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def human_bytes(size: int) -> str:
    """``1536`` → ``1.5 KB``。"""
    value = float(max(0, int(size)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def hex_dump(data: bytes, *, limit: int = HEX_PREVIEW_BYTES, base_offset: int = 0) -> str:
    """生成 ``offset  hex  |ascii|`` 形式的十六进制预览。"""
    lines: List[str] = []
    chunk = data[: max(0, int(limit))]
    for offset in range(0, len(chunk), 16):
        block = chunk[offset: offset + 16]
        hex_part = " ".join(f"{byte:02X}" for byte in block)
        hex_part = f"{hex_part:<47}"
        text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in block)
        lines.append(f"{base_offset + offset:08X}  {hex_part}  |{text}|")
    if len(data) > len(chunk):
        lines.append(
            f"… 还有 {human_bytes(len(data) - len(chunk))} 未显示 "
            f"(共 {human_bytes(len(data))})"
        )
    return "\n".join(lines)


def normalize_project_path(path: str) -> str:
    """把用户输入的路径规范成 ``a/b/c.ext`` 形式。

    * 反斜杠统一成正斜杠 (Windows 用户习惯);
    * 去掉开头的 ``/`` 与 ``./``, 以及所有 ``..`` —— 库内路径不允许逃逸;
    * 压缩重复斜杠, 去掉空段与结尾斜杠。
    """
    raw = str(path or "").strip().replace("\\", "/")
    parts: List[str] = []
    for segment in raw.split("/"):
        segment = segment.strip()
        if not segment or segment == ".":
            continue
        if segment == "..":
            # 直接丢弃, 保证路径永远相对于空间根
            continue
        parts.append(segment)
    return "/".join(parts)


def is_readme_path(path: str) -> bool:
    """判断是否是 README 文件 (任意目录下的都算)。"""
    return posixpath.basename(normalize_project_path(path)).lower() in README_NAMES


# --------------------------------------------------------------------------------------
# ProjectFile
# --------------------------------------------------------------------------------------


@dataclass
class ProjectFile:
    """空间里的一个文件。

    文本文件把内容放在 ``content``; 二进制文件把**原始字节的 base64** 放在 ``data`` ——
    也就是说内容真的被**嵌进了 ``.cmdb``**, 不是对外部路径的引用。

    旧版本保存的二进制文件只有 ``size`` 没有 ``data`` (只记大小), 仍然能正常打开,
    界面会显示成"未嵌入"并允许重新导入。
    """

    path: str = ""
    content: str = ""
    language: str = "plaintext"
    binary: bool = False          # 二进制文件的内容以 base64 存在 data 里
    size: int = 0
    note: str = ""
    data: str = ""                # base64(原始字节); 空 = 未嵌入内容
    created_at: float = field(default_factory=utcnow)
    updated_at: float = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        self.path = normalize_project_path(self.path)
        if self.binary:
            self.language = "plaintext"
        elif not self.language or self.language == "plaintext":
            self.language = detect_language_from_filename(self.path)
        else:
            self.language = normalize_language(self.language)
        if not self.size:
            self.size = self.computed_size

    # ---- 便捷属性 ----
    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    @property
    def directory(self) -> str:
        parent = posixpath.dirname(self.path)
        return parent or "."

    @property
    def extension(self) -> str:
        name = self.name
        return name.rsplit(".", 1)[-1].lower() if "." in name else ""

    @property
    def is_readme(self) -> bool:
        return is_readme_path(self.path)

    @property
    def is_image(self) -> bool:
        return self.binary and self.extension in IMAGE_EXTENSIONS

    @property
    def has_data(self) -> bool:
        """二进制内容是否真的被嵌进了库里。"""
        return bool(self.data)

    @property
    def embedded(self) -> bool:
        """内容是否存在库内 (文本文件总是嵌入的)。"""
        return True if not self.binary else bool(self.data)

    @property
    def computed_size(self) -> int:
        """字节数 (UTF-8)。语言占比按这个口径统计, 与 GitHub 一致。"""
        if self.binary:
            return int(self.size)
        return len(self.content.encode("utf-8"))

    @property
    def stored_bytes(self) -> int:
        """这个文件在容器里大致占多少字节 (base64 会放大 1/3)。"""
        if self.binary:
            return len(self.data.encode("ascii")) if self.data else 0
        return self.computed_size

    @property
    def line_count(self) -> int:
        if self.binary or not self.content:
            return 0
        return len(self.content.splitlines())

    @property
    def category(self) -> str:
        return get_language(self.language).category

    @property
    def size_label(self) -> str:
        return human_bytes(self.computed_size)

    # ---- 二进制内容 ----
    def set_bytes(self, raw: bytes, *, note: Optional[str] = None) -> None:
        """把原始字节嵌进这个文件 (base64 存进 ``data``)。"""
        payload = bytes(raw)
        self.binary = True
        self.content = ""
        self.language = "plaintext"
        self.data = base64.b64encode(payload).decode("ascii")
        self.size = len(payload)
        if note is not None:
            self.note = note

    def raw_bytes(self) -> bytes:
        """解出嵌入的原始字节; 未嵌入或数据损坏时返回 ``b""``。"""
        if not self.data:
            return b""
        try:
            return base64.b64decode(self.data, validate=False)
        except (binascii.Error, ValueError):  # pragma: no cover - 只可能是被手工改坏
            return b""

    def sha256(self) -> str:
        raw = self.raw_bytes()
        return hashlib.sha256(raw).hexdigest() if raw else ""

    def hex_preview(self, *, limit: int = HEX_PREVIEW_BYTES) -> str:
        return hex_dump(self.raw_bytes(), limit=limit)

    def write_to(self, target: str) -> int:
        """把文件内容写到磁盘 (二进制按原始字节, 文本按 UTF-8), 返回字节数。"""
        if self.binary:
            raw = self.raw_bytes()
            with open(target, "wb") as handle:
                handle.write(raw)
            return len(raw)
        body = self.content.encode("utf-8")
        with open(target, "wb") as handle:
            handle.write(body)
        return len(body)

    def touch(self) -> None:
        self.updated_at = utcnow()
        self.size = self.computed_size

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "content": self.content,
            "language": self.language,
            "binary": self.binary,
            "size": self.size,
            "note": self.note,
            "data": self.data,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectFile":
        binary = bool(data.get("binary", False))
        return cls(
            path=str(data.get("path") or ""),
            content=str(data.get("content") or ""),
            language=str(data.get("language") or "plaintext"),
            binary=binary,
            size=int(data.get("size") or 0),
            note=str(data.get("note") or ""),
            data=str(data.get("data") or ""),
            created_at=float(data.get("created_at") or utcnow()),
            updated_at=float(data.get("updated_at") or utcnow()),
        )

    def clone(self) -> "ProjectFile":
        return ProjectFile.from_dict(self.to_dict())


# --------------------------------------------------------------------------------------
# 语言占比
# --------------------------------------------------------------------------------------


@dataclass
class LanguageShare:
    """一种语言在空间里的占比。"""

    language: str
    bytes: int
    files: int
    lines: int
    percent: float = 0.0

    @property
    def name(self) -> str:
        return get_language(self.language).name

    @property
    def color(self) -> str:
        return get_language(self.language).color

    @property
    def category(self) -> str:
        return get_language(self.language).category

    @property
    def percent_label(self) -> str:
        return f"{self.percent:.1f}%"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "name": self.name,
            "bytes": self.bytes,
            "files": self.files,
            "lines": self.lines,
            "percent": round(self.percent, 2),
            "color": self.color,
            "category": self.category,
        }


# --------------------------------------------------------------------------------------
# 目录树
# --------------------------------------------------------------------------------------


@dataclass
class TreeNode:
    """由文件路径推导出来的目录节点 (仅用于界面展示)。"""

    name: str
    path: str
    is_dir: bool
    size: int = 0
    language: str = ""
    binary: bool = False
    embedded: bool = True       # 二进制文件是否把内容嵌进了库
    children: List["TreeNode"] = field(default_factory=list)

    def sorted_children(self) -> List["TreeNode"]:
        """目录在前、文件在后, 各自按名称排序。"""
        return sorted(self.children, key=lambda n: (not n.is_dir, n.name.casefold()))

    @property
    def total_size(self) -> int:
        if not self.is_dir:
            return self.size
        return sum(child.total_size for child in self.children)


def build_tree(files: Iterable[ProjectFile]) -> TreeNode:
    """把扁平的路径列表还原成目录树。"""
    root = TreeNode(name="", path="", is_dir=True)
    index: Dict[str, TreeNode] = {"": root}

    def ensure_dir(path: str) -> TreeNode:
        if path in index:
            return index[path]
        parent = ensure_dir(posixpath.dirname(path))
        node = TreeNode(name=posixpath.basename(path), path=path, is_dir=True)
        parent.children.append(node)
        index[path] = node
        return node

    for item in files:
        if not item.path:
            continue
        directory = ensure_dir(posixpath.dirname(item.path))
        node = TreeNode(
            name=item.name,
            path=item.path,
            is_dir=False,
            size=item.computed_size,
            language=item.language,
            binary=item.binary,
            embedded=item.embedded,
        )
        directory.children.append(node)
    return root


# --------------------------------------------------------------------------------------
# Space
# --------------------------------------------------------------------------------------


@dataclass
class Space:
    """一个独立空间: 库内的完整项目结构。"""

    name: str = ""
    description: str = ""
    prerequisites: str = ""
    tags: List[str] = field(default_factory=list)
    files: List[ProjectFile] = field(default_factory=list)
    status: str = "planned"
    favorite: bool = False
    entry_point: str = ""          # 入口文件路径, 例如 "src/main.py"
    id: str = field(default_factory=lambda: new_id("spc_"))
    created_at: float = field(default_factory=utcnow)
    updated_at: float = field(default_factory=utcnow)
    version: int = 1
    deleted: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tags = normalize_tags(self.tags)
        if self.status not in STATUS_ORDER:
            self.status = "planned"
        self.files = list(self.files)
        self.entry_point = normalize_project_path(self.entry_point)

    # ---- 与模块/函数体统一的接口 (列表与详情面板依赖这几个属性) ----
    kind = "space"

    @property
    def kind_label(self) -> str:
        return "空间"

    @property
    def display_title(self) -> str:
        return self.name.strip() or "未命名空间"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def badge_count(self) -> int:
        return len(self.files)

    @property
    def badge_label(self) -> str:
        return f"{len(self.files)} 文件"

    @property
    def languages(self) -> List[str]:
        seen: List[str] = []
        for item in self.files:
            if item.language not in seen:
                seen.append(item.language)
        return seen

    @property
    def total_lines(self) -> int:
        return sum(item.line_count for item in self.files)

    @property
    def total_size(self) -> int:
        return sum(item.computed_size for item in self.files)

    @property
    def binary_files(self) -> List[ProjectFile]:
        return [item for item in self.files if item.binary]

    @property
    def embedded_binary_files(self) -> List[ProjectFile]:
        """内容真的被嵌进库里的二进制文件。"""
        return [item for item in self.files if item.binary and item.data]

    @property
    def embedded_binary_bytes(self) -> int:
        """全部已嵌入二进制的原始字节总数 (不含 base64 放大)。"""
        return sum(item.size for item in self.embedded_binary_files)

    def binary_budget_left(self, *, exclude_path: str = "") -> int:
        """还能再嵌入多少字节的二进制 (``exclude_path`` 用于覆盖已有文件的场景)。"""
        used = sum(
            item.size
            for item in self.files
            if item.binary and item.data and item.path != normalize_project_path(exclude_path)
        )
        return max(0, MAX_SPACE_BINARY_BYTES - used)

    @property
    def search_blob(self) -> str:
        """供全文检索使用的合成文本 (小写)。

        空间里的**每个文件内容**都会参与检索 —— 这正是"自动检索库中的
        README.md"所依赖的基础。
        """
        parts = [self.name, self.description, self.prerequisites, " ".join(self.tags)]
        for item in self.files:
            parts.append(item.path)
            parts.append(item.note)
            if not item.binary:
                parts.append(item.content)
        return "\n".join(parts).casefold()

    # ---- 文件操作 ----
    def get_file(self, path: str) -> Optional[ProjectFile]:
        wanted = normalize_project_path(path)
        for item in self.files:
            if item.path == wanted:
                return item
        return None

    def has_path(self, path: str) -> bool:
        return self.get_file(path) is not None

    def put_file(self, file: ProjectFile) -> ProjectFile:
        """插入或覆盖一个文件 (按路径)。"""
        existing = self.get_file(file.path)
        if existing is not None:
            self.files[self.files.index(existing)] = file
        else:
            self.files.append(file)
        return file

    def remove_file(self, path: str) -> bool:
        wanted = normalize_project_path(path)
        for item in list(self.files):
            if item.path == wanted:
                self.files.remove(item)
                return True
        return False

    def remove_directory(self, path: str) -> int:
        """删除一个目录及其下全部文件, 返回删除数量。"""
        prefix = normalize_project_path(path)
        if not prefix:
            return 0
        prefix += "/"
        doomed = [item for item in self.files if item.path.startswith(prefix)]
        for item in doomed:
            self.files.remove(item)
        return len(doomed)

    def directories(self) -> List[str]:
        """全部目录路径 (含中间层级), 排序后返回。"""
        found: set = set()
        for item in self.files:
            directory = posixpath.dirname(item.path)
            while directory:
                found.add(directory)
                directory = posixpath.dirname(directory)
        return sorted(found)

    def tree(self) -> TreeNode:
        return build_tree(self.files)

    # ---- README ----
    @property
    def readme_files(self) -> List[ProjectFile]:
        return [item for item in self.files if item.is_readme and not item.binary]

    @property
    def has_readme(self) -> bool:
        return bool(self.readme_files)

    # ---- 语言占比 ----
    def language_shares(self, *, include_non_code: bool = False) -> List[LanguageShare]:
        """按字节数计算语言占比, 降序返回。

        ``include_non_code=False`` (默认) 时只统计 ``category == "code"`` 的语言,
        与 GitHub 的语言条口径一致; 文档/数据/配置仍然会在完整表格里列出。
        """
        buckets: Dict[str, Dict[str, int]] = {}
        for item in self.files:
            spec = get_language(item.language)
            if not include_non_code and spec.category != "code":
                continue
            bucket = buckets.setdefault(item.language, {"bytes": 0, "files": 0, "lines": 0})
            bucket["bytes"] += item.computed_size
            bucket["files"] += 1
            bucket["lines"] += item.line_count

        total = sum(bucket["bytes"] for bucket in buckets.values())
        shares: List[LanguageShare] = []
        for language, bucket in buckets.items():
            share = LanguageShare(
                language=language,
                bytes=bucket["bytes"],
                files=bucket["files"],
                lines=bucket["lines"],
            )
            share.percent = (bucket["bytes"] / total * 100.0) if total else 0.0
            shares.append(share)
        shares.sort(key=lambda s: (-s.bytes, s.name))
        return shares

    def language_summary(self) -> str:
        """一行文字占比, 例如 ``Python 62.1% · Go 30.4% · Markdown 7.5%``。"""
        shares = self.language_shares()
        if not shares:
            return "（暂无代码文件）"
        return " · ".join(f"{s.name} {s.percent_label}" for s in shares)

    # ---- 变更 ----
    def touch(self) -> None:
        self.updated_at = utcnow()
        self.version += 1

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prerequisites": self.prerequisites,
            "tags": list(self.tags),
            "status": self.status,
            "favorite": self.favorite,
            "entry_point": self.entry_point,
            "files": [item.to_dict() for item in self.files],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "deleted": self.deleted,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Space":
        files = [
            ProjectFile.from_dict(item)
            for item in (data.get("files") or [])
            if isinstance(item, dict)
        ]
        return cls(
            id=str(data.get("id") or new_id("spc_")),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            prerequisites=str(data.get("prerequisites") or ""),
            tags=list(data.get("tags") or []),
            files=files,
            status=str(data.get("status") or "planned"),
            favorite=bool(data.get("favorite", False)),
            entry_point=str(data.get("entry_point") or ""),
            created_at=float(data.get("created_at") or utcnow()),
            updated_at=float(data.get("updated_at") or utcnow()),
            version=int(data.get("version") or 1),
            deleted=bool(data.get("deleted", False)),
            extra=dict(data.get("extra") or {}),
        )

    def clone(self) -> "Space":
        return Space.from_dict(self.to_dict())


# --------------------------------------------------------------------------------------
# README 检索
# --------------------------------------------------------------------------------------


@dataclass
class ReadmeHit:
    """README 检索命中的一行。"""

    space_id: str
    space_name: str
    path: str
    line_number: int
    line: str
    context: List[Tuple[int, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "space_id": self.space_id,
            "space_name": self.space_name,
            "path": self.path,
            "line_number": self.line_number,
            "line": self.line,
        }


def search_readmes(
    spaces: Iterable[Space],
    query: str,
    *,
    context_lines: int = 2,
    max_hits: int = 300,
) -> List[ReadmeHit]:
    """在全部空间的全部 README 文件里做子串检索 (大小写不敏感)。

    这是"自动检索所有库中的 README.md 以允许用户搜索"的实现: 天然覆盖任意层级
    (``README.md`` / ``docs/README.md`` / ``README.rst`` …)。
    """
    needle = (query or "").strip().casefold()
    if not needle:
        return []

    hits: List[ReadmeHit] = []
    for space in spaces:
        if space.deleted:
            continue
        for readme in space.readme_files:
            lines = readme.content.splitlines()
            for index, line in enumerate(lines):
                if needle not in line.casefold():
                    continue
                start = max(0, index - context_lines)
                end = min(len(lines), index + context_lines + 1)
                hits.append(
                    ReadmeHit(
                        space_id=space.id,
                        space_name=space.display_title,
                        path=readme.path,
                        line_number=index + 1,
                        line=line.strip()[:300],
                        context=[(i + 1, lines[i]) for i in range(start, end)],
                    )
                )
                if len(hits) >= max_hits:
                    return hits
    return hits


__all__ = [
    "ProjectFile",
    "Space",
    "LanguageShare",
    "TreeNode",
    "ReadmeHit",
    "build_tree",
    "search_readmes",
    "normalize_project_path",
    "is_readme_path",
    "is_binary_path",
    "is_image_path",
    "looks_binary",
    "decode_text",
    "hex_dump",
    "human_bytes",
    "README_NAMES",
    "BINARY_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "MAX_FILE_BYTES",
    "MAX_BINARY_BYTES",
    "MAX_SPACE_BINARY_BYTES",
    "MAX_FILES_PER_SPACE",
    "HEX_PREVIEW_BYTES",
]
