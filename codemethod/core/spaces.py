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
import os
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


# --------------------------------------------------------------------------------------
# 存储限制
# --------------------------------------------------------------------------------------
#
# **默认全部不限制** —— 你的库你做主, 喜欢塞多大的文件就塞多大的。
#
# 这里只提供一组"如果库变慢了可以拿来用"的推荐值 (``SUGGESTED_LIMITS``),
# 以及一个可以随时改的 ``StorageLimits``。真正的限制值存在代码库里, 跟着 .cmdb 走。
#
# 为什么曾经有硬上限 (现在为什么改成可选):
#
# * **语法高亮**是纯 Python 正则, 对超大文件会卡住界面 —— 这一点**不靠限制文件大小**
#   解决, 而是靠编辑器自身的降级 (见 ui/editor.py 的 LARGE_DOCUMENT_*), 超过阈值就
#   关掉高亮, 内容一个字节都不少;
# * **全文检索**会把每个文件的内容 casefold 成一份索引文本常驻内存, 库特别大时内存
#   会成倍增长 —— 现在给索引缓存加了总量预算, 超了就淘汰最大的那几条, 而不是拒绝导入;
# * **修订历史是全量快照**, 一个反复修改的大文件会在历史里留下多份 —— 这是"任意时刻
#   都能完整还原"的代价, 属于设计取舍, 不该靠拒绝用户来回避;
# * 保存/读取要把整个库序列化成 JSON 再 zlib 压缩, 库越大越慢、峰值内存越高。
#
# 所以现在: 默认不限制; 真想设限就去「文件 → 存储限制…」自己填, 并能在那里看到
# 每一项的代价说明。

@dataclass
class StorageLimits:
    """空间文件的存储限制。**每一项 0 都表示不限制** (这也是默认值)。"""

    max_text_bytes: int = 0            # 单个文本文件
    max_binary_bytes: int = 0          # 单个二进制文件
    max_space_binary_bytes: int = 0    # 单个空间里嵌入的二进制总量
    max_files_per_space: int = 0       # 单个空间的文件数量

    def __post_init__(self) -> None:
        self.max_text_bytes = max(0, int(self.max_text_bytes or 0))
        self.max_binary_bytes = max(0, int(self.max_binary_bytes or 0))
        self.max_space_binary_bytes = max(0, int(self.max_space_binary_bytes or 0))
        self.max_files_per_space = max(0, int(self.max_files_per_space or 0))

    @property
    def unlimited(self) -> bool:
        return not any(
            (
                self.max_text_bytes,
                self.max_binary_bytes,
                self.max_space_binary_bytes,
                self.max_files_per_space,
            )
        )

    @property
    def summary(self) -> str:
        if self.unlimited:
            return "不限制"
        bits = []
        if self.max_text_bytes:
            bits.append(f"文本 {human_bytes(self.max_text_bytes)}")
        if self.max_binary_bytes:
            bits.append(f"二进制 {human_bytes(self.max_binary_bytes)}")
        if self.max_space_binary_bytes:
            bits.append(f"空间二进制 {human_bytes(self.max_space_binary_bytes)}")
        if self.max_files_per_space:
            bits.append(f"{self.max_files_per_space} 个文件")
        return " · ".join(bits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_text_bytes": self.max_text_bytes,
            "max_binary_bytes": self.max_binary_bytes,
            "max_space_binary_bytes": self.max_space_binary_bytes,
            "max_files_per_space": self.max_files_per_space,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "StorageLimits":
        if not isinstance(data, dict):
            return cls()
        return cls(
            max_text_bytes=int(data.get("max_text_bytes") or 0),
            max_binary_bytes=int(data.get("max_binary_bytes") or 0),
            max_space_binary_bytes=int(data.get("max_space_binary_bytes") or 0),
            max_files_per_space=int(data.get("max_files_per_space") or 0),
        )


# 「如果库变慢了」可以一键套用的推荐值 (不是默认值!)
SUGGESTED_LIMITS = StorageLimits(
    max_text_bytes=2 * 1024 * 1024,
    max_binary_bytes=4 * 1024 * 1024,
    max_space_binary_bytes=32 * 1024 * 1024,
    max_files_per_space=2000,
)

# 历史遗留的名字: 现在只是"推荐值"的别名, 不再用于强制拦截
MAX_FILE_BYTES = SUGGESTED_LIMITS.max_text_bytes
MAX_BINARY_BYTES = SUGGESTED_LIMITS.max_binary_bytes
MAX_SPACE_BINARY_BYTES = SUGGESTED_LIMITS.max_space_binary_bytes
MAX_FILES_PER_SPACE = SUGGESTED_LIMITS.max_files_per_space

# 导入整个目录时**默认跳过**的目录名 (可以在对话框里勾上"全部包含")
VCS_DIR_NAMES = frozenset({".git", ".svn", ".hg", ".bzr", "CVS"})
CACHE_DIR_NAMES = frozenset(
    {
        "__pycache__", ".venv", "venv", "env", "node_modules", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", ".tox", ".gradle", ".idea", ".vs",
        "target", "dist", "build", ".next", ".nuxt", "vendor",
    }
)
# 导入整个目录时**默认跳过**的文件后缀 (编译产物 / 缓存 / 编辑器临时文件)
SKIP_FILE_SUFFIXES = frozenset(
    {
        ".pyc", ".pyo", ".pyd", ".class", ".o", ".obj", ".pdb", ".ilk",
        ".swp", ".swo", ".tmp", ".bak", ".orig", ".rej", ".DS_Store",
    }
)

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


def looks_binary(data: bytes, *, truncated: bool = False) -> bool:
    """按**内容**判断一段字节是不是二进制。

    扩展名不可靠 (``.dat`` / 无扩展名的可执行文件), 所以导入外部文件时还要看内容:

    * 出现 ``NUL`` 字节 → 二进制 (文本文件不会有);
    * 否则尝试按 UTF-8 解码, 解不开 → 二进制。

    这条规则对中国用户很重要 —— 不能简单地按"非 ASCII 字节比例"判断, 那样
    一个纯中文的 README 会被误判成二进制。

    ``truncated=True`` 表示 ``data`` 只是文件开头的一段 (扫描阶段只读前 1 KB)。
    这时如果解码错误发生在**最后几个字节**, 那只是多字节字符被切断, 不算二进制。
    """
    if not data:
        return False
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError as exc:
        if truncated and exc.start >= len(data) - 4:
            return False
        return True


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


# --------------------------------------------------------------------------------------
# 整目录导入: 先"计划", 再执行
# --------------------------------------------------------------------------------------
#
# 把一整个目录 (含子目录) 打包进空间是个**破坏性**操作: 几百上千个文件、几 MB 大图、
# 一堆 .git 内部对象。所以这里把过程拆成两步:
#
#   1. ``scan_external_directory`` 只**读目录结构与每个文件的前几 KB**,
#      为每个文件算出一条计划 (库内路径 / 大小 / 文本还是二进制 / 是否跳过);
#   2. ``Repository.import_scan`` 照着计划执行。
#
# 界面因此可以先给出**准确的预览** (多少文件、多少字节、哪些会被跳过、为什么),
# 用户确认后不再重复扫描。


@dataclass
class ImportOptions:
    """整目录导入的选项 (界面上的那几个勾选框)。"""

    recursive: bool = True
    include_hidden: bool = True          # 包含 .gitignore / .env 这类隐藏文件
    include_vcs: bool = False            # 包含 .git / .svn / .hg
    include_caches: bool = False         # 包含 __pycache__ / node_modules / .venv …
    keep_structure: bool = True          # 保留子目录结构 (否则全部平铺到根)
    target_dir: str = ""                 # 全部落到空间里的哪个目录下
    # 0 = 不限制 (默认)。非 0 时超限的文件会被跳过并写明原因。
    max_text_bytes: int = 0
    max_binary_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recursive": self.recursive,
            "include_hidden": self.include_hidden,
            "include_vcs": self.include_vcs,
            "include_caches": self.include_caches,
            "keep_structure": self.keep_structure,
            "target_dir": self.target_dir,
            "max_text_bytes": self.max_text_bytes,
            "max_binary_bytes": self.max_binary_bytes,
        }

    @classmethod
    def from_limits(
        cls, limits: "StorageLimits", **overrides: Any
    ) -> "ImportOptions":
        """从代码库的存储限制生成导入选项 (限制为 0 时就是不限制)。"""
        options = cls(
            max_text_bytes=limits.max_text_bytes,
            max_binary_bytes=limits.max_binary_bytes,
        )
        for key, value in overrides.items():
            setattr(options, key, value)
        return options


@dataclass
class PlannedFile:
    """一个待导入文件在计划里的样子 (``reason`` 非空表示会被跳过)。"""

    source: str = ""             # 磁盘上的绝对路径
    path: str = ""               # 库内相对路径
    size: int = 0
    binary: bool = False         # 按二进制嵌入 (否则按文本存)
    reason: str = ""             # 跳过原因; 空字符串 = 会导入

    @property
    def skipped(self) -> bool:
        return bool(self.reason)

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    @property
    def size_label(self) -> str:
        return human_bytes(self.size)

    @property
    def kind_label(self) -> str:
        return "二进制" if self.binary else "文本"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "path": self.path,
            "size": self.size,
            "binary": self.binary,
            "reason": self.reason,
            "skipped": self.skipped,
        }


@dataclass
class DirectoryScan:
    """一次整目录扫描的结果 —— 目录树 + 每个文件的计划。"""

    root: str = ""
    files: List[PlannedFile] = field(default_factory=list)
    options: ImportOptions = field(default_factory=ImportOptions)
    error: str = ""                       # 扫描本身失败 (例如目录不存在)
    # 整个被剪掉的目录: [(相对路径, 原因)]。文件级的跳过在 PlannedFile.reason 里,
    # 但目录是在 os.walk 阶段就剪掉的 —— 不说出来的话, 用户会以为 .git "凭空消失"。
    pruned_dirs: List[Tuple[str, str]] = field(default_factory=list)

    # ---- 统计 ----
    @property
    def included(self) -> List[PlannedFile]:
        return [f for f in self.files if not f.skipped]

    @property
    def skipped(self) -> List[PlannedFile]:
        return [f for f in self.files if f.skipped]

    @property
    def text_files(self) -> List[PlannedFile]:
        return [f for f in self.included if not f.binary]

    @property
    def binary_files(self) -> List[PlannedFile]:
        return [f for f in self.included if f.binary]

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.included)

    @property
    def text_bytes(self) -> int:
        return sum(f.size for f in self.text_files)

    @property
    def binary_bytes(self) -> int:
        return sum(f.size for f in self.binary_files)

    @property
    def directories(self) -> List[str]:
        found: set = set()
        for item in self.included:
            directory = posixpath.dirname(item.path)
            while directory:
                found.add(directory)
                directory = posixpath.dirname(directory)
        return sorted(found)

    def reasons(self) -> Dict[str, int]:
        """跳过原因 → 数量 (给界面做汇总)。"""
        counts: Dict[str, int] = {}
        for item in self.skipped:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return counts

    def pruned_reasons(self) -> Dict[str, int]:
        """被整目录剪掉的原因 → 目录数量。"""
        counts: Dict[str, int] = {}
        for _path, reason in self.pruned_dirs:
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def summary(self) -> str:
        if self.error:
            return self.error
        parts = [
            f"{len(self.included)} 个文件 · {human_bytes(self.total_bytes)}",
            f"文本 {len(self.text_files)} 个 ({human_bytes(self.text_bytes)})",
            f"二进制 {len(self.binary_files)} 个 ({human_bytes(self.binary_bytes)})",
        ]
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 个文件")
        if self.pruned_dirs:
            parts.append(f"跳过 {len(self.pruned_dirs)} 个目录")
        return " · ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "summary": self.summary(),
            "options": self.options.to_dict(),
            "files": [f.to_dict() for f in self.files],
            "pruned_dirs": [{"path": p, "reason": r} for p, r in self.pruned_dirs],
        }


def scan_external_directory(
    directory: str, options: Optional[ImportOptions] = None
) -> DirectoryScan:
    """扫描一个外部目录, 产出"会导入哪些文件"的完整计划。

    只读取**每个文件的前 8 KB** 来判断文本/二进制, 所以扫描几千个文件也很快,
    且不会把大文件读进内存。
    """
    opts = options or ImportOptions()
    root = os.path.abspath(str(directory or ""))
    scan = DirectoryScan(root=root, options=opts)
    if not os.path.isdir(root):
        scan.error = f"目录不存在: {directory}"
        return scan

    target_prefix = normalize_project_path(opts.target_dir)
    sources: List[str] = []
    if opts.recursive:
        for current, dirs, names in os.walk(root):
            kept: List[str] = []
            for name in sorted(dirs):
                reason = _dir_skip_reason(name, opts)
                if reason:
                    scan.pruned_dirs.append(
                        ((os.path.relpath(os.path.join(current, name), root)).replace(os.sep, "/"), reason)
                    )
                    continue
                kept.append(name)
            dirs[:] = kept
            for name in sorted(names):
                sources.append(os.path.join(current, name))
    else:
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if os.path.isfile(full):
                sources.append(full)

    for source in sources:
        scan.files.append(_plan_one(source, root, target_prefix, opts))
    return scan


def _dir_skip_reason(name: str, opts: ImportOptions) -> str:
    """整个目录该不该剪掉? 返回原因 (空串 = 保留)。"""
    if name in VCS_DIR_NAMES and not opts.include_vcs:
        return f"版本控制目录 ({name})"
    if name in CACHE_DIR_NAMES and not opts.include_caches:
        return f"缓存/产物目录 ({name})"
    if name.startswith(".") and not opts.include_hidden:
        return "隐藏目录"
    return ""


def _plan_one(
    source: str, root: str, target_prefix: str, opts: ImportOptions
) -> PlannedFile:
    name = os.path.basename(source)
    try:
        size = os.path.getsize(source)
    except OSError as exc:
        return PlannedFile(source=source, path=name, reason=f"读不到大小: {exc.strerror or exc}")

    if name.startswith(".") and not opts.include_hidden:
        return PlannedFile(source=source, path=name, size=size, reason="隐藏文件")

    relative = _relative_under(source, root, opts)
    if not relative:
        return PlannedFile(source=source, path=name, size=size, reason="路径无效")
    path = normalize_project_path(f"{target_prefix}/{relative}" if target_prefix else relative)
    if not path:
        return PlannedFile(source=source, path=name, size=size, reason="路径无效")

    suffix = posixpath.splitext(name)[1].lower()
    if suffix in SKIP_FILE_SUFFIXES:
        return PlannedFile(source=source, path=path, size=size, reason=f"缓存/临时文件 ({suffix})")

    head = _read_head(source)
    binary = is_binary_path(path) or looks_binary(head, truncated=True)

    if binary:
        if opts.max_binary_bytes and size > opts.max_binary_bytes:
            return PlannedFile(
                source=source, path=path, size=size, binary=True,
                reason=f"超过二进制上限 {human_bytes(opts.max_binary_bytes)}",
            )
        return PlannedFile(source=source, path=path, size=size, binary=True)

    if opts.max_text_bytes and size > opts.max_text_bytes:
        # 设了文本上限时, 大文本改按二进制嵌入 —— 反正界面里也是只读预览,
        # 这样"完全打包"就不会因为一个 3 MB 的日志文件而失败。
        if not opts.max_binary_bytes or size <= opts.max_binary_bytes:
            return PlannedFile(source=source, path=path, size=size, binary=True)
        return PlannedFile(
            source=source, path=path, size=size,
            reason=f"超过上限 {human_bytes(opts.max_binary_bytes)}",
        )
    return PlannedFile(source=source, path=path, size=size)


def _relative_under(source: str, root: str, opts: ImportOptions) -> str:
    """决定文件在库内的相对路径。"""
    name = os.path.basename(source)
    if not opts.keep_structure:
        return name
    try:
        relative = os.path.relpath(source, root)
    except ValueError:      # pragma: no cover - 跨盘符
        return name
    if relative.startswith(".."):      # pragma: no cover - 不该发生
        return name
    return relative.replace(os.sep, "/")


def _read_head(source: str, limit: int = 1024) -> bytes:
    """只读开头一小段用于判断文本/二进制。

    1 KB 足够看出有没有 ``NUL`` 和能不能按 UTF-8 解码, 而读取成本只有 8 KB 的
    1/7 —— 扫几千个文件时这个差别很明显 (尤其是刚写完、还冷着的文件)。
    """
    try:
        with open(source, "rb") as handle:
            return handle.read(limit)
    except OSError:      # pragma: no cover - 权限问题
        return b""


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

    def binary_budget_left(
        self, limit: int = 0, *, exclude_path: str = ""
    ) -> int:
        """还能再嵌入多少字节的二进制。

        ``limit`` 为 0 (默认) 表示**不限制**, 此时返回 ``None`` 语义的大数 ——
        调用方用 :meth:`binary_over_budget` 判断更清楚。
        """
        used = sum(
            item.size
            for item in self.files
            if item.binary and item.data and item.path != normalize_project_path(exclude_path)
        )
        if not limit:
            return -1                       # -1 = 不限制
        return max(0, limit - used)

    def binary_over_budget(self, incoming: int, limit: int = 0, *, exclude_path: str = "") -> bool:
        """再嵌入 ``incoming`` 字节会不会超过 ``limit`` (limit=0 恒为 False)。"""
        if not limit:
            return False
        used = sum(
            item.size
            for item in self.files
            if item.binary and item.data and item.path != normalize_project_path(exclude_path)
        )
        return used + incoming > limit

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
    "ImportOptions",
    "PlannedFile",
    "DirectoryScan",
    "StorageLimits",
    "SUGGESTED_LIMITS",
    "scan_external_directory",
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
    "VCS_DIR_NAMES",
    "CACHE_DIR_NAMES",
    "SKIP_FILE_SUFFIXES",
    "MAX_FILE_BYTES",
    "MAX_BINARY_BYTES",
    "MAX_SPACE_BINARY_BYTES",
    "MAX_FILES_PER_SPACE",
    "HEX_PREVIEW_BYTES",
]
