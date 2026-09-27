"""存储层: 可移植的 CodeMethod 容器格式.

对外暴露 :class:`~codemethod.storage.database.Database` (打开/保存/导入/导出)
以及底层的二进制/文本编解码函数。
"""

from __future__ import annotations

from .container import (
    BINARY_EXTENSIONS,
    CONTAINER_MAGIC,
    FOOTER_MAGIC,
    FORMAT_MAJOR,
    FORMAT_MINOR,
    TEXT_EXTENSIONS,
    CodeMethodFormatError,
    ContainerInfo,
    FormatKind,
    build_manifest,
    detect_format,
    pack_repository,
    probe_file,
    read_container,
    read_text,
    unpack_repository,
    verify_file,
    write_container,
    write_text,
)
from .database import Database, DatabaseError

__all__ = [
    "Database",
    "DatabaseError",
    "CodeMethodFormatError",
    "FormatKind",
    "ContainerInfo",
    "CONTAINER_MAGIC",
    "FOOTER_MAGIC",
    "FORMAT_MAJOR",
    "FORMAT_MINOR",
    "BINARY_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "pack_repository",
    "unpack_repository",
    "write_container",
    "read_container",
    "write_text",
    "read_text",
    "build_manifest",
    "detect_format",
    "probe_file",
    "verify_file",
]
