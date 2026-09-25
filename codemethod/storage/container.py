"""CodeMethod 容器格式 ``.cmdb`` (二进制) 与 ``.cmj`` (文本).

格式规范 v1.0
=============

二进制容器 ``.cmdb`` 由四段组成, 全部小端序 (little-endian)::

    ┌──────────────────────── 头部 HEADER (固定 64 字节) ────────────────────────┐
    │ off  size 类型    字段          说明                                        │
    │ 0    8    char[8] magic         b"CMDBFMT\\x01"                            │
    │ 8    2    uint16  major         主版本号 (不兼容变更时递增)                 │
    │ 10   2    uint16  minor         次版本号 (向后兼容增量)                     │
    │ 12   4    uint32  flags         bit0 载荷已压缩, bit1 含清单,               │
    │                                 bit2 载荷校验为 CRC32, bit3 已加密(预留)    │
    │ 16   4    uint32  header_size   头部字节数 (=64), 便于将来扩展              │
    │ 20   8    uint64  payload_len   载荷存储长度 (压缩后)                       │
    │ 28   8    uint64  payload_raw   载荷原始长度 (压缩前)                       │
    │ 36   4    uint32  payload_crc   载荷字节的 CRC32                            │
    │ 40   8    uint64  created       创建时间 (Unix 秒)                          │
    │ 48   8    uint64  modified      最后修改时间 (Unix 秒)                      │
    │ 56   8    uint64  manifest_off  清单块偏移 (0 表示无清单)                   │
    ├──────────────────────── 载荷 PAYLOAD ──────────────────────────────────────┤
    │ payload_len 字节: zlib(JSON UTF-8), 或未压缩的 JSON UTF-8                   │
    ├──────────────────────── 清单 MANIFEST ─────────────────────────────────────┤
    │ JSON UTF-8 (可选压缩): 快速索引 + 载荷 SHA-256, 无需解压即可列出条目        │
    ├──────────────────────── 尾部 FOOTER (固定 32 字节) ────────────────────────┤
    │ 0    8    uint64  manifest_len  清单块长度                                  │
    │ 8    8    uint64  file_size     文件总长度 (用于检测截断)                   │
    │ 16   4    uint32  manifest_crc  清单块 CRC32                                │
    │ 20   4    uint32  footer_crc    CRC32(头部 64 字节 ‖ 本尾部前 20 字节)      │
    │ 24   8    char[8] end_magic     b"CMDBEND\\x00"                            │
    └─────────────────────────────────────────────────────────────────────────────┘

``footer_crc`` 覆盖头部是刻意设计: 这样**任何**头部字段 (包括版本号与时间戳) 被改写
都能被检测出来, 而不仅仅是长度/校验和被连带影响的字段。

文本容器 ``.cmj`` 是一个普通 JSON 文档, 便于用 git diff / 手工编辑::

    {
      "format": "codemethod-repository",
      "format_version": "1.0",
      "generator": "CodeMethod 1.0.0",
      "created_at": ..., "modified_at": ...,
      "stats": {...},
      "integrity": {"algorithm": "sha256", "digest": "..."},   # 对 repository 段
      "repository": { ...同 Repository.to_dict()... }
    }

两种容器可以互相无损转换 (``Database.convert``)。
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import time
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .. import APP_NAME, APP_VERSION

# --------------------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------------------

CONTAINER_MAGIC = b"CMDBFMT\x01"
FOOTER_MAGIC = b"CMDBEND\x00"
FMT = "CMDBFMT1"

FORMAT_MAJOR = 1
FORMAT_MINOR = 0

HEADER_STRUCT = struct.Struct("<8sHHIIQQIQQQ")
FOOTER_STRUCT = struct.Struct("<QQII8s")
assert HEADER_STRUCT.size == 64, HEADER_STRUCT.size
assert FOOTER_STRUCT.size == 32, FOOTER_STRUCT.size

FLAG_COMPRESSED = 0x0001
FLAG_MANIFEST = 0x0002
FLAG_CRC32 = 0x0004
FLAG_ENCRYPTED = 0x0008

BINARY_EXTENSIONS: Tuple[str, ...] = (".cmdb", ".cmx")
TEXT_EXTENSIONS: Tuple[str, ...] = (".cmj", ".cmjson")
ALL_EXTENSIONS: Tuple[str, ...] = BINARY_EXTENSIONS + TEXT_EXTENSIONS

TEXT_FORMAT_TAG = "codemethod-repository"
TEXT_FORMAT_VERSION = "1.0"


class CodeMethodFormatError(Exception):
    """容器格式错误 (魔数不符 / 截断 / 校验失败 / 版本过新)。"""


class FormatKind(str, Enum):
    """文件格式种类。"""

    BINARY = "binary"
    TEXT = "text"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            FormatKind.BINARY: "二进制容器 (.cmdb)",
            FormatKind.TEXT: "文本容器 (.cmj)",
            FormatKind.UNKNOWN: "未知格式",
        }[self]


# --------------------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------------------


@dataclass
class ContainerInfo:
    """通过 :func:`probe_file` 得到的容器元信息 (不需要解压载荷)。"""

    path: str
    kind: FormatKind
    major: int = 0
    minor: int = 0
    flags: int = 0
    created: float = 0.0
    modified: float = 0.0
    payload_len: int = 0
    payload_raw: int = 0
    file_size: int = 0
    compressed: bool = False
    manifest: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def version(self) -> str:
        return f"{self.major}.{self.minor}"

    @property
    def ratio(self) -> float:
        if not self.payload_raw:
            return 1.0
        return self.payload_len / self.payload_raw

    def describe(self) -> str:
        parts = [
            f"格式: {self.kind.label}",
            f"版本: {self.version}",
            f"大小: {_human_size(self.file_size)}",
        ]
        if self.kind is FormatKind.BINARY:
            parts.append(
                f"载荷: {_human_size(self.payload_len)} / 原始 {_human_size(self.payload_raw)}"
                f" (压缩率 {self.ratio:.1%})"
            )
            parts.append(f"标记: 0x{self.flags:04X}")
        if self.modified:
            parts.append("修改: " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.modified)))
        return "\n".join(parts)


def human_size(num: float) -> str:
    """把字节数格式化为易读字符串。"""
    return _human_size(num)


def _human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024.0 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} GB"


# --------------------------------------------------------------------------------------
# 载荷编解码
# --------------------------------------------------------------------------------------


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _decode_json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def pack_repository(repository_data: Dict[str, Any]) -> bytes:
    """把仓储 dict 编码为 JSON 载荷 (未压缩)。"""
    return _json_bytes(repository_data)


def unpack_repository(payload: bytes) -> Dict[str, Any]:
    """解码 JSON 载荷。"""
    try:
        data = _decode_json(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover - 防御
        raise CodeMethodFormatError(f"载荷不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CodeMethodFormatError("载荷根节点必须是对象")
    return data


# --------------------------------------------------------------------------------------
# 清单
# --------------------------------------------------------------------------------------


def build_manifest(repository_data: Dict[str, Any], payload_bytes: bytes) -> Dict[str, Any]:
    """构造清单: 无需解压即可展示的索引信息 + 载荷摘要。"""
    entries = repository_data.get("entries") or []
    history = repository_data.get("history") or {}
    timelines = history.get("timelines") if isinstance(history, dict) else {}
    revision_total = sum(len(v or []) for v in (timelines or {}).values())

    index: List[Dict[str, Any]] = []
    language_counter: Dict[str, int] = {}
    tag_counter: Dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        impls = [i for i in (entry.get("implementations") or []) if not i.get("deleted")]
        langs = sorted({str(i.get("language")) for i in impls})
        for lang in langs:
            language_counter[lang] = language_counter.get(lang, 0) + 1
        for tag in entry.get("tags") or []:
            tag_counter[str(tag)] = tag_counter.get(str(tag), 0) + 1
        index.append(
            {
                "id": entry.get("id"),
                "title": entry.get("title"),
                "status": entry.get("status"),
                "tags": list(entry.get("tags") or []),
                "languages": langs,
                "deleted": bool(entry.get("deleted")),
                "updated_at": entry.get("updated_at"),
                "revisions": len((timelines or {}).get(entry.get("id"), []) or []),
            }
        )

    return {
        "format": TEXT_FORMAT_TAG,
        "format_version": TEXT_FORMAT_VERSION,
        "generator": f"{APP_NAME} {APP_VERSION}",
        "created_at": repository_data.get("created_at"),
        "modified_at": repository_data.get("modified_at"),
        "stats": {
            "entries": len(index),
            "deleted_entries": sum(1 for item in index if item["deleted"]),
            "implementations": sum(len(i.get("implementations") or []) for i in entries if isinstance(i, dict)),
            "revisions": revision_total,
            "tags": len(tag_counter),
            "languages": sorted(language_counter),
        },
        "languages": language_counter,
        "tags": tag_counter,
        "entries": index,
        "integrity": {
            "algorithm": "sha256",
            "digest": hashlib.sha256(payload_bytes).hexdigest(),
            "payload_bytes": len(payload_bytes),
        },
        "written_at": time.time(),
    }


# --------------------------------------------------------------------------------------
# 二进制容器写入
# --------------------------------------------------------------------------------------


def write_container(
    path: str,
    repository_data: Dict[str, Any],
    *,
    compress: bool = True,
    compress_level: int = 6,
    with_manifest: bool = True,
) -> ContainerInfo:
    """把仓储数据写成 ``.cmdb`` 二进制容器, 返回写入后的元信息。"""
    blob, info = build_container_bytes(
        repository_data,
        compress=compress,
        compress_level=compress_level,
        with_manifest=with_manifest,
    )
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())
    info.path = path
    info.file_size = len(blob)
    return info


def build_container_bytes(
    repository_data: Dict[str, Any],
    *,
    compress: bool = True,
    compress_level: int = 6,
    with_manifest: bool = True,
) -> Tuple[bytes, ContainerInfo]:
    """构造 ``.cmdb`` 容器的完整字节流, 返回 ``(bytes, 元信息)``。

    与 :func:`write_container` 的区别是不落盘, 便于测试与内存传输。
    """
    payload_raw = pack_repository(repository_data)

    flags = FLAG_CRC32
    payload = payload_raw
    if compress:
        compressed = zlib.compress(payload_raw, compress_level)
        if len(compressed) < len(payload_raw):
            payload = compressed
            flags |= FLAG_COMPRESSED

    created = int(float(repository_data.get("created_at") or time.time()))
    modified = int(float(repository_data.get("modified_at") or time.time()))

    manifest_bytes = b""
    manifest_off = 0
    if with_manifest:
        manifest = build_manifest(repository_data, payload_raw)
        manifest_bytes = zlib.compress(_json_bytes(manifest), compress_level)
        flags |= FLAG_MANIFEST
        manifest_off = HEADER_STRUCT.size + len(payload)

    header = HEADER_STRUCT.pack(
        CONTAINER_MAGIC,
        FORMAT_MAJOR,
        FORMAT_MINOR,
        flags,
        HEADER_STRUCT.size,
        len(payload),
        len(payload_raw),
        zlib.crc32(payload) & 0xFFFFFFFF,
        created,
        modified,
        manifest_off,
    )

    file_size = HEADER_STRUCT.size + len(payload) + len(manifest_bytes) + FOOTER_STRUCT.size
    footer_body = struct.pack(
        "<QQI", len(manifest_bytes), file_size, zlib.crc32(manifest_bytes) & 0xFFFFFFFF
    )
    # footer_crc 覆盖 (头部 + 尾部主体), 使头部任何字节的篡改都可被检出
    footer_crc = zlib.crc32(header + footer_body) & 0xFFFFFFFF
    footer = FOOTER_STRUCT.pack(
        len(manifest_bytes),
        file_size,
        zlib.crc32(manifest_bytes) & 0xFFFFFFFF,
        footer_crc,
        FOOTER_MAGIC,
    )

    blob = header + payload + manifest_bytes + footer
    info = ContainerInfo(
        path="<memory>",
        kind=FormatKind.BINARY,
        major=FORMAT_MAJOR,
        minor=FORMAT_MINOR,
        flags=flags,
        created=float(created),
        modified=float(modified),
        payload_len=len(payload),
        payload_raw=len(payload_raw),
        file_size=file_size,
        compressed=bool(flags & FLAG_COMPRESSED),
    )
    return blob, info


# --------------------------------------------------------------------------------------
# 二进制容器读取
# --------------------------------------------------------------------------------------


def read_container(path: str, *, verify: bool = True) -> Tuple[Dict[str, Any], ContainerInfo]:
    """读取 ``.cmdb`` 二进制容器。"""
    with open(path, "rb") as handle:
        blob = handle.read()
    return read_container_bytes(blob, path=path, verify=verify)


def read_container_bytes(
    blob: bytes, *, path: str = "<memory>", verify: bool = True
) -> Tuple[Dict[str, Any], ContainerInfo]:
    """从内存字节读取二进制容器。"""
    if len(blob) < HEADER_STRUCT.size + FOOTER_STRUCT.size:
        raise CodeMethodFormatError("文件过小, 不是有效的 CodeMethod 容器")

    try:
        (
            magic,
            major,
            minor,
            flags,
            header_size,
            payload_len,
            payload_raw,
            payload_crc,
            created,
            modified,
            manifest_off,
        ) = HEADER_STRUCT.unpack_from(blob, 0)
    except struct.error as exc:  # pragma: no cover - 防御
        raise CodeMethodFormatError(f"头部解析失败: {exc}") from exc

    if magic != CONTAINER_MAGIC:
        raise CodeMethodFormatError("魔数不匹配, 不是 .cmdb 文件")
    if major > FORMAT_MAJOR:
        raise CodeMethodFormatError(
            f"文件版本 {major}.{minor} 高于本程序支持的 {FORMAT_MAJOR}.{FORMAT_MINOR}, 请升级 CodeMethod"
        )

    warnings: List[str] = []
    footer = blob[-FOOTER_STRUCT.size:]
    f_manifest_len, f_file_size, f_manifest_crc, f_footer_crc, f_magic = FOOTER_STRUCT.unpack(footer)
    if f_magic != FOOTER_MAGIC:
        warnings.append("尾部魔数缺失, 文件可能被截断或由早期版本写入")
        f_manifest_len = 0
    else:
        # footer_crc = CRC32(头部 64 字节 ‖ 尾部前 20 字节)
        expected_crc = zlib.crc32(blob[: header_size] + footer[:20]) & 0xFFFFFFFF
        if verify and expected_crc != f_footer_crc:
            warnings.append("头部/尾部校验和不匹配 (元数据已被篡改或损坏)")
        if f_file_size and f_file_size != len(blob):
            warnings.append(f"文件长度不符: 记录 {f_file_size}, 实际 {len(blob)}")

    payload_start = header_size
    payload_end = payload_start + payload_len
    if payload_end > len(blob):
        raise CodeMethodFormatError("载荷越界, 文件不完整")

    payload = blob[payload_start:payload_end]
    if verify and (zlib.crc32(payload) & 0xFFFFFFFF) != payload_crc:
        raise CodeMethodFormatError("载荷 CRC32 校验失败, 文件已损坏")

    manifest_data: Optional[Dict[str, Any]] = None
    manifest_bytes = b""
    if f_manifest_len:
        manifest_start = payload_end
        manifest_end = manifest_start + f_manifest_len
        if manifest_end > len(blob) - FOOTER_STRUCT.size:
            warnings.append("清单块越界, 已忽略")
        else:
            manifest_bytes = blob[manifest_start:manifest_end]
            if verify and (zlib.crc32(manifest_bytes) & 0xFFFFFFFF) != f_manifest_crc:
                warnings.append("清单校验和不匹配")
            try:
                manifest_data = _decode_json(zlib.decompress(manifest_bytes))
            except Exception:  # pragma: no cover - 防御
                warnings.append("清单解析失败, 已忽略")
                manifest_data = None

    if flags & FLAG_ENCRYPTED:
        raise CodeMethodFormatError("文件已加密, 当前版本尚未实现解密")

    if flags & FLAG_COMPRESSED:
        try:
            raw = zlib.decompress(payload)
        except zlib.error as exc:
            raise CodeMethodFormatError(f"解压失败: {exc}") from exc
    else:
        raw = payload

    if len(raw) != payload_raw:
        warnings.append(f"载荷长度不符: 记录 {payload_raw}, 实际 {len(raw)}")

    data = unpack_repository(raw)

    if verify and manifest_data:
        expect = (manifest_data.get("integrity") or {}).get("digest")
        actual = hashlib.sha256(raw).hexdigest()
        if expect and expect != actual:
            warnings.append("SHA-256 摘要与清单不一致")

    info = ContainerInfo(
        path=path,
        kind=FormatKind.BINARY,
        major=major,
        minor=minor,
        flags=flags,
        created=float(created),
        modified=float(modified),
        payload_len=payload_len,
        payload_raw=payload_raw,
        file_size=len(blob),
        compressed=bool(flags & FLAG_COMPRESSED),
        manifest=manifest_data,
        warnings=warnings,
    )
    return data, info


# --------------------------------------------------------------------------------------
# 文本容器
# --------------------------------------------------------------------------------------


def write_text(path: str, repository_data: Dict[str, Any], *, indent: int = 2) -> ContainerInfo:
    """把仓储数据写成 ``.cmj`` 文本容器 (可直接 diff / 手工编辑)。"""
    payload = pack_repository(repository_data)
    stats = (build_manifest(repository_data, payload)).get("stats", {})
    document = {
        "format": TEXT_FORMAT_TAG,
        "format_version": TEXT_FORMAT_VERSION,
        "generator": f"{APP_NAME} {APP_VERSION}",
        "created_at": repository_data.get("created_at"),
        "modified_at": repository_data.get("modified_at"),
        "stats": stats,
        "integrity": {
            "algorithm": "sha256",
            "digest": hashlib.sha256(payload).hexdigest(),
            "payload_bytes": len(payload),
        },
        "repository": repository_data,
    }
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    text = json.dumps(document, ensure_ascii=False, indent=indent)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())

    size = os.path.getsize(path)
    return ContainerInfo(
        path=path,
        kind=FormatKind.TEXT,
        major=FORMAT_MAJOR,
        minor=FORMAT_MINOR,
        created=float(repository_data.get("created_at") or 0.0),
        modified=float(repository_data.get("modified_at") or 0.0),
        payload_len=size,
        payload_raw=len(payload),
        file_size=size,
        compressed=False,
        manifest={"stats": stats},
    )


def read_text(path: str, *, verify: bool = True) -> Tuple[Dict[str, Any], ContainerInfo]:
    """读取 ``.cmj`` 文本容器。"""
    with open(path, "r", encoding="utf-8") as handle:
        raw_text = handle.read()
    return read_text_string(raw_text, path=path, verify=verify)


def read_text_string(
    raw_text: str, *, path: str = "<memory>", verify: bool = True
) -> Tuple[Dict[str, Any], ContainerInfo]:
    try:
        document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise CodeMethodFormatError(f"不是合法 JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise CodeMethodFormatError("文本容器根节点必须是对象")

    warnings: List[str] = []
    repository = document.get("repository")
    if repository is None:
        # 兼容: 直接就是一个 Repository dict
        if "entries" in document:
            warnings.append("缺少 format 包装, 按裸仓储数据读取")
            repository = document
            document = {"format": TEXT_FORMAT_TAG, "format_version": TEXT_FORMAT_VERSION}
        else:
            raise CodeMethodFormatError("未找到 repository 字段")

    if document.get("format") not in (None, TEXT_FORMAT_TAG):
        warnings.append(f"未知 format 标记: {document.get('format')}")

    if verify:
        integrity = document.get("integrity") or {}
        expect = integrity.get("digest")
        if expect:
            actual = hashlib.sha256(pack_repository(repository)).hexdigest()
            if expect != actual:
                warnings.append("integrity.digest 与内容不一致 (文件可能被手工修改)")

    info = ContainerInfo(
        path=path,
        kind=FormatKind.TEXT,
        major=FORMAT_MAJOR,
        minor=FORMAT_MINOR,
        created=float(repository.get("created_at") or 0.0),
        modified=float(repository.get("modified_at") or 0.0),
        payload_len=len(raw_text.encode("utf-8")),
        payload_raw=len(pack_repository(repository)),
        file_size=len(raw_text.encode("utf-8")),
        compressed=False,
        manifest={"stats": (build_manifest(repository, pack_repository(repository))).get("stats", {})},
        warnings=warnings,
    )
    return repository, info


# --------------------------------------------------------------------------------------
# 探测 / 校验 / 转换
# --------------------------------------------------------------------------------------


def detect_format(path: str) -> FormatKind:
    """根据内容 (而非扩展名) 判断格式。"""
    try:
        with open(path, "rb") as handle:
            head = handle.read(8)
    except OSError:
        return FormatKind.UNKNOWN
    if head == CONTAINER_MAGIC:
        return FormatKind.BINARY
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"{"):
        return FormatKind.TEXT
    return FormatKind.UNKNOWN


def probe_file(path: str) -> ContainerInfo:
    """读取容器元信息。二进制格式只读头部/清单, 不解析仓储内容。"""
    kind = detect_format(path)
    if kind is FormatKind.BINARY:
        with open(path, "rb") as handle:
            blob = handle.read()
        _, info = read_container_bytes(blob, path=path, verify=False)
        return info
    if kind is FormatKind.TEXT:
        data, info = read_text(path, verify=False)
        info.payload_raw = len(pack_repository(data))
        return info
    raise CodeMethodFormatError(f"无法识别的文件格式: {path}")


def verify_file(path: str) -> Tuple[bool, List[str], Optional[ContainerInfo]]:
    """完整校验一个容器文件。

    返回 ``(是否通过, 问题列表, 元信息)``。
    """
    problems: List[str] = []
    info: Optional[ContainerInfo] = None
    kind = detect_format(path)
    if kind is FormatKind.UNKNOWN:
        return False, ["无法识别格式 (魔数与 JSON 均不匹配)"], None
    try:
        if kind is FormatKind.BINARY:
            data, info = read_container(path, verify=True)
        else:
            data, info = read_text(path, verify=True)
    except CodeMethodFormatError as exc:
        return False, [str(exc)], None
    except OSError as exc:
        return False, [f"读取失败: {exc}"], None

    problems.extend(info.warnings)
    # 结构完整性检查
    if not isinstance(data.get("entries"), list):
        problems.append("缺少 entries 列表")
    if not isinstance(data.get("history"), dict):
        problems.append("缺少 history 段 (历史追踪将不可用)")
    return (not problems), problems, info


def read_any(path: str, *, verify: bool = True) -> Tuple[Dict[str, Any], ContainerInfo]:
    """按内容自动识别并读取任意一种容器。"""
    kind = detect_format(path)
    if kind is FormatKind.BINARY:
        return read_container(path, verify=verify)
    if kind is FormatKind.TEXT:
        return read_text(path, verify=verify)
    # 兜底: 按扩展名再试一次
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTENSIONS:
        return read_text(path, verify=verify)
    raise CodeMethodFormatError(f"无法识别的文件格式: {os.path.basename(path)}")


__all__ = [
    "CONTAINER_MAGIC",
    "FOOTER_MAGIC",
    "FORMAT_MAJOR",
    "FORMAT_MINOR",
    "FLAG_COMPRESSED",
    "FLAG_MANIFEST",
    "FLAG_CRC32",
    "FLAG_ENCRYPTED",
    "BINARY_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "ALL_EXTENSIONS",
    "TEXT_FORMAT_TAG",
    "TEXT_FORMAT_VERSION",
    "CodeMethodFormatError",
    "FormatKind",
    "ContainerInfo",
    "pack_repository",
    "unpack_repository",
    "build_manifest",
    "build_container_bytes",
    "write_container",
    "read_container",
    "read_container_bytes",
    "write_text",
    "read_text",
    "read_text_string",
    "detect_format",
    "probe_file",
    "verify_file",
    "read_any",
    "human_size",
]
