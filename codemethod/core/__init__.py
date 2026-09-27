"""核心领域层: 语言注册表、三类实体、修订历史与仓储。

三类实体共用一套历史、检索与导出机制:

* **模块 (Entry)** —— 一个问题 + 多种语言实现;
* **空间 (Space)** —— 库内的完整项目结构 (多文件, 全部内容存进容器);
* **函数体 (Function)** —— 一个语言里的一个函数 + 变量含义表。
"""

from __future__ import annotations

from .languages import LANGUAGES, LanguageSpec, get_language, language_choices, normalize_language
from .models import (
    Entry,
    Implementation,
    Revision,
    STATUS_ORDER,
    STATUS_LABELS,
    new_id,
    utcnow,
)
from .spaces import (
    LanguageShare,
    ProjectFile,
    ReadmeHit,
    Space,
    build_tree,
    is_binary_path,
    is_readme_path,
    normalize_project_path,
    search_readmes,
)
from .functions import (
    REQUIRED_SYMBOL_KINDS,
    SYMBOL_KIND_LABELS,
    Function,
    Symbol,
    symbol_kind_label,
)
from .symbols import detect_symbols, merge_symbols, symbol_key, supported_languages
from .history import HistoryStore, build_revision_diff, kind_of_snapshot, summarize_changes
from .repository import Repository, RepositoryError, TagInfo, detect_function_symbols
from .query import QuerySpec, TagMatch, query_entries, sort_entries

__all__ = [
    # 语言
    "LANGUAGES",
    "LanguageSpec",
    "get_language",
    "language_choices",
    "normalize_language",
    # 模块
    "Entry",
    "Implementation",
    "Revision",
    "STATUS_ORDER",
    "STATUS_LABELS",
    "new_id",
    "utcnow",
    # 空间
    "Space",
    "ProjectFile",
    "LanguageShare",
    "ReadmeHit",
    "build_tree",
    "search_readmes",
    "normalize_project_path",
    "is_readme_path",
    "is_binary_path",
    # 函数体
    "Function",
    "Symbol",
    "SYMBOL_KIND_LABELS",
    "REQUIRED_SYMBOL_KINDS",
    "symbol_kind_label",
    "detect_symbols",
    "merge_symbols",
    "symbol_key",
    "supported_languages",
    "detect_function_symbols",
    # 历史 / 仓储 / 查询
    "HistoryStore",
    "build_revision_diff",
    "kind_of_snapshot",
    "summarize_changes",
    "Repository",
    "RepositoryError",
    "TagInfo",
    "QuerySpec",
    "TagMatch",
    "query_entries",
    "sort_entries",
]
