"""应用装配与启动。

用法::

    python -m codemethod                 # 启动 GUI (空库)
    python -m codemethod 我的代码库.cmdb  # 启动并打开指定文件
    python main.py                        # 等价入口
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from typing import List, Optional, Sequence

from . import APP_NAME, APP_VERSION
from .core.repository import Repository
from .storage.database import Database, DatabaseError
from .ui.main_window import MainWindow
from .ui.resources import app_icon
from .ui.theme import DEFAULT_THEME, apply_theme, get_theme

# 一个合法的 1×1 透明 PNG (67 字节) —— 演示"把外部二进制嵌进代码库"
DEMO_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)


def build_demo_repository() -> Repository:
    """构造一个演示库 (首次启动时展示用法)。"""
    from .core.models import Implementation

    repo = Repository(
        name="示例代码库",
        description=(
            "这是 CodeMethod 的示例库。每条记录描述一个「功能」, "
            "可以挂多个标签, 并为它编写多种语言的实现; 所有修改都会进入历史, 可随时回滚。"
        ),
        author="CodeMethod",
    )

    tcp = repo.create_entry(
        title="TCP 回显服务器",
        description=(
            "接受客户端连接, 把收到的每一行原样返回, 支持多个客户端并发。\n"
            "用于验证网络连通性、压测以及作为协议调试的最小骨架。"
        ),
        prerequisites="本地可用的 9000 端口；需要理解 TCP 是面向字节流的, 没有消息边界。",
        tags=["network", "tcp", "server", "demo"],
        status="done",
        favorite=True,
    )
    repo.add_implementation(
        tcp.id,
        "python",
        title="基于 asyncio",
        notes="单线程事件循环, 适合高并发连接。",
        prerequisites="Python 3.10+（用到 asyncio.start_server 与 wait_closed）；仅标准库。",
        code='''import asyncio


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    print(f"[+] 客户端接入: {peer}")
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            writer.write(line)          # 原样回显
            await writer.drain()
    finally:
        print(f"[-] 客户端断开: {peer}")
        writer.close()
        await writer.wait_closed()


async def main(host: str = "0.0.0.0", port: int = 9000) -> None:
    server = await asyncio.start_server(handle, host, port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"监听 {addrs}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\\n已停止")
''',
    )
    repo.add_implementation(
        tcp.id,
        "go",
        title="基于 goroutine",
        notes="每个连接一个 goroutine, 标准库即可。",
        prerequisites="Go 1.21+；仅标准库 (net / bufio / log)；GOOS 任意。",
        code='''package main

import (
	"bufio"
	"io"
	"log"
	"net"
)

func handle(conn net.Conn) {
	defer conn.Close()
	addr := conn.RemoteAddr().String()
	log.Printf("[+] 客户端接入: %s", addr)
	reader := bufio.NewReader(conn)
	for {
		line, err := reader.ReadString('\\n')
		if len(line) > 0 {
			if _, werr := io.WriteString(conn, line); werr != nil {
				break
			}
		}
		if err != nil {
			break
		}
	}
	log.Printf("[-] 客户端断开: %s", addr)
}

func main() {
	listener, err := net.Listen("tcp", ":9000")
	if err != nil {
		log.Fatal(err)
	}
	defer listener.Close()
	log.Println("监听 :9000")
	for {
		conn, err := listener.Accept()
		if err != nil {
			log.Println(err)
			continue
		}
		go handle(conn)
	}
}
''',
    )

    lru = repo.create_entry(
        title="LRU 缓存",
        description=(
            "实现一个固定容量的最近最少使用缓存, get 与 put 均为 O(1)。\n"
            "核心思路: 哈希表 + 双向链表, 访问后把节点移到链表头部, 超容时淘汰尾部。"
        ),
        prerequisites="需要理解哈希表与双向链表的组合；与具体语言无关。",
        tags=["algorithm", "cache", "lru", "demo"],
        status="in_progress",
    )
    repo.add_implementation(
        lru.id,
        "python",
        prerequisites="Python 3.7+（OrderedDict.move_to_end 自 3.2 起提供）；仅标准库。",
        code='''from collections import OrderedDict


class LRUCache:
    """O(1) 的 LRU 缓存。"""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity 必须为正数")
        self.capacity = capacity
        self._data: "OrderedDict[object, object]" = OrderedDict()

    def get(self, key):
        if key not in self._data:
            return None
        self._data.move_to_end(key)      # 标记为最近使用
        return self._data[key]

    def put(self, key, value) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        if len(self._data) > self.capacity:
            self._data.popitem(last=False)   # 淘汰最久未使用

    def __len__(self) -> int:
        return len(self._data)
''',
    )
    repo.add_implementation(
        lru.id,
        "cpp",
        title="手写双向链表",
        notes="C++ 中 list + unordered_map 的组合, 也可用 std::list::splice。",
        prerequisites="C++17（用到 std::optional）；g++ 11+ 或 MSVC 19.3+；无需第三方库。",
        code='''#include <list>
#include <optional>
#include <stdexcept>
#include <unordered_map>

template <typename K, typename V>
class LRUCache {
public:
    explicit LRUCache(std::size_t capacity) : capacity_(capacity) {
        if (capacity == 0) throw std::invalid_argument("capacity 必须为正数");
    }

    std::optional<V> get(const K& key) {
        auto it = index_.find(key);
        if (it == index_.end()) return std::nullopt;
        items_.splice(items_.begin(), items_, it->second);   // 移到表头, O(1)
        return it->second->second;
    }

    void put(const K& key, const V& value) {
        auto it = index_.find(key);
        if (it != index_.end()) {
            it->second->second = value;
            items_.splice(items_.begin(), items_, it->second);
            return;
        }
        items_.emplace_front(key, value);
        index_[key] = items_.begin();
        if (items_.size() > capacity_) {
            index_.erase(items_.back().first);
            items_.pop_back();
        }
    }

    std::size_t size() const { return items_.size(); }

private:
    std::size_t capacity_;
    std::list<std::pair<K, V>> items_;
    std::unordered_map<K, typename std::list<std::pair<K, V>>::iterator> index_;
};
''',
    )

    fib = repo.create_entry(
        title="快速幂 / 模幂运算",
        description="在 O(log n) 时间内计算 a^n (以及 a^n mod m), 避免溢出。",
        prerequisites="需要理解二进制拆分与取模运算的结合律。",
        tags=["algorithm", "math", "demo"],
        status="planned",
    )
    repo.add_implementation(
        fib.id,
        "rust",
        prerequisites="Rust 1.75+ / cargo（含 #[cfg(test)] 单元测试，cargo test 可直接跑）。",
        code='''/// 计算 (base ^ exp) % modulus, 全程不溢出。
pub fn mod_pow(mut base: u128, mut exp: u128, modulus: u128) -> u128 {
    if modulus == 1 {
        return 0;
    }
    let mut result: u128 = 1;
    base %= modulus;
    while exp > 0 {
        if exp & 1 == 1 {
            result = result * base % modulus;
        }
        exp >>= 1;
        base = base * base % modulus;
    }
    result
}

#[cfg(test)]
mod tests {
    use super::mod_pow;

    #[test]
    fn matches_naive() {
        assert_eq!(mod_pow(2, 10, 1000), 24);
        assert_eq!(mod_pow(3, 0, 7), 1);
    }
}
''',
    )

    repo.create_entry(
        title="正则提取日志中的耗时",
        description="从形如 `took 123ms` 的日志行里提取全部耗时并求和。",
        prerequisites="",
        tags=["regex", "log", "demo"],
        status="idea",
    )

    # ---- 独立空间: 一个完整的多文件小项目 ----
    from .core.spaces import ProjectFile

    space = repo.create_space(
        name="tokenizer-service",
        description=(
            "一个可以把任意文本切成 token 的小服务。\n"
            "目录结构: src/ 放实现, tests/ 放测试, docs/ 放说明。"
        ),
        prerequisites="Python 3.10+ / Go 1.21+；不需要数据库；监听 8080。",
        tags=["project", "demo", "service"],
        status="in_progress",
        favorite=True,
        entry_point="src/tokenizer.py",
    )
    space_files = [
        ("README.md", """# tokenizer-service

把文本切成 token 的小服务, 提供 Python 与 Go 两套实现。

## 目录

- `src/` — 实现
- `tests/` — 测试
- `docs/` — 说明

## 快速开始

```bash
python src/tokenizer.py "hello world"
```

## 说明

服务默认监听 **8080**, 支持 HTTP 与 gRPC 两种协议。
分词规则见 `docs/RULES.md`, 目前只做空白切分。
"""),
        ("docs/RULES.md", """# 分词规则

1. 以空白字符切分
2. 连续空白视为一个分隔符
3. 保留大小写

后续计划: 支持正则自定义分隔符、支持中文分词。
"""),
        ("src/tokenizer.py", '''"""把文本切成 token。"""

from typing import List


def tokenize(text: str, keep_case: bool = True) -> List[str]:
    """按空白切分, 忽略连续空白。"""
    tokens = text.split()
    if not keep_case:
        tokens = [t.lower() for t in tokens]
    return tokens


def count(text: str) -> int:
    return len(tokenize(text))
'''),
        ("src/main.py", '''"""命令行入口。"""

import sys

from tokenizer import tokenize


def main(argv: list[str]) -> int:
    text = " ".join(argv[1:]) or "hello world"
    for index, token in enumerate(tokenize(text), 1):
        print(f"{index:>3}  {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''),
        ("src/server.go", '''package main

import (
	"fmt"
	"net/http"
	"strings"
)

// split 按空白切分, 与 Python 版保持同样的语义。
func split(text string) []string {
	return strings.Fields(text)
}

func main() {
	http.HandleFunc("/tokenize", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, "%q", split(r.URL.Query().Get("text")))
	})
	http.ListenAndServe(":8080", nil)
}
'''),
        ("tests/test_tokenizer.py", '''import unittest

from tokenizer import tokenize


class TestTokenizer(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(tokenize("a b c"), ["a", "b", "c"])

    def test_collapses_whitespace(self):
        self.assertEqual(tokenize("a   b"), ["a", "b"])
'''),
    ]
    for path, content in space_files:
        repo.put_space_file(space.id, path, content)

    # 二进制文件也是**嵌进库里**的 (base64), 拷走一个 .cmdb 就能完整还原。
    # 这里放一个真实的 1×1 PNG, 用来演示图片预览与"导出到磁盘"。
    repo.put_space_file(space.id, "docs/logo.png", binary_data=DEMO_PNG)

    # ---- 函数体: 自动检测变量并要求填写含义 (同一个函数可以有多语言实现) ----
    function = repo.create_function(
        "mod_pow",
        "python",
        '''def mod_pow(base: int, exp: int, modulus: int = 1000000007) -> int:
    """快速幂取模: 计算 (base ** exp) % modulus, 全程不溢出。"""
    result = 1
    base %= modulus
    while exp > 0:
        if exp & 1:
            result = result * base % modulus
        exp >>= 1
        base = base * base % modulus
    return result
''',
        description="经典的二进制快速幂, O(log exp)。同一个函数用 Python / Go / Rust 三种语言分别实现。",
        prerequisites="仅用整数运算, 无第三方依赖。",
        implementation_prerequisites="Python 3.6+",
        tags=["algorithm", "math", "demo"],
        status="done",
    )
    # 给必填项填上含义, 演示"变量含义表"
    meanings = {
        "base": "底数",
        "exp": "指数",
        "modulus": "取模的模数, 默认 1e9+7",
        "result": "累计的结果",
        "return": "base 的 exp 次方对 modulus 取模的结果",
    }
    python_impl = function.active_implementations[0]
    for symbol in python_impl.symbols:
        if symbol.name in meanings:
            symbol.meaning = meanings[symbol.name]
    repo.set_function_symbols(
        function.id, python_impl.symbols, implementation_id=python_impl.id,
        summary="填写变量含义 (Python)",
    )

    # 同一个函数的 Go 实现 —— 前置要求与变量表都是这一种语言自己的
    go_impl = repo.add_function_implementation(
        function.id,
        "go",
        '''package modpow

// modPow 计算 (base ** exp) % modulus, 全程不溢出。
func modPow(base, exp, modulus int64) int64 {
	result := int64(1)
	base %= modulus
	for exp > 0 {
		if exp&1 == 1 {
			result = result * base % modulus
		}
		exp >>= 1
		base = base * base % modulus
	}
	return result
}
''',
        signature="func modPow(base, exp, modulus int64) int64",
        prerequisites="Go 1.21+；只用标准库, 需要 64 位整数。",
        notes="Go 版直接用 int64 算术, 溢出行为与 Python 的大整数不同, 所以必须由调用方保证 base/modulus 在 int64 范围内。",
    )
    go_meanings = {
        "base": "底数",
        "exp": "指数",
        "modulus": "取模的模数",
        "result": "累计的结果",
        "return": "base 的 exp 次方对 modulus 取模的结果",
    }
    for symbol in go_impl.symbols:
        if symbol.name in go_meanings:
            symbol.meaning = go_meanings[symbol.name]
    repo.set_function_symbols(
        function.id, go_impl.symbols, implementation_id=go_impl.id,
        summary="填写变量含义 (Go)",
    )

    # 再来一个 Rust 实现
    rust_impl = repo.add_function_implementation(
        function.id,
        "rust",
        '''/// 快速幂取模: 计算 (base ** exp) % modulus。
pub fn mod_pow(mut base: u128, mut exp: u128, modulus: u128) -> u128 {
    let mut result: u128 = 1;
    base %= modulus;
    while exp > 0 {
        if exp & 1 == 1 {
            result = result * base % modulus;
        }
        exp >>= 1;
        base = base * base % modulus;
    }
    result
}
''',
        signature="pub fn mod_pow(base: u128, exp: u128, modulus: u128) -> u128",
        prerequisites="Rust 1.70+；使用 u128 以便容纳中间乘积, 需要 rustc 2021 edition。",
        notes="Rust 版显式使用 u128 避免乘法溢出, 代价是只能处理 128 位以内的模数。",
    )
    rust_meanings = {
        "base": "底数",
        "exp": "指数",
        "modulus": "取模的模数",
        "result": "累计的结果",
        "return": "base 的 exp 次方对 modulus 取模的结果",
    }
    for symbol in rust_impl.symbols:
        if symbol.name in rust_meanings:
            symbol.meaning = rust_meanings[symbol.name]
    repo.set_function_symbols(
        function.id, rust_impl.symbols, implementation_id=rust_impl.id,
        summary="填写变量含义 (Rust)",
    )

    return repo


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="多标签代码实现规划与知识库 (PySide6 GUI)",
    )
    parser.add_argument("file", nargs="?", help="要打开的 .cmdb / .cmj 代码库文件")
    parser.add_argument("--demo", action="store_true", help="启动时载入示例库")
    parser.add_argument("--theme", choices=["dark+", "light"], default="dark+", help="界面主题")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument(
        "--selftest",
        metavar="REPORT",
        help="不启动界面, 跑一遍自检 (容器读写/导出/高亮/界面构建) 并把报告写到指定文件; "
        "主要用于验证 PyInstaller 打包后的程序是否完整",
    )
    return parser


def run_self_test(report_path: Optional[str] = None) -> int:
    """无界面自检: 覆盖打包后最容易出问题的地方。

    检查项: 依赖导入、语言注册表、容器读写与校验、三种导出、语法高亮、界面构建。
    返回 0 表示全部通过。报告同时写到 ``report_path`` (若有) 与标准输出。
    """
    import tempfile

    from .core.languages import all_languages
    from .storage import exporter
    from .storage.container import verify_file
    from .ui.highlighter import highlighted_tokens

    lines: List[str] = []
    failures: List[str] = []

    def archive_bytes(path: str, member: str) -> bytes:
        import zipfile

        with zipfile.ZipFile(path) as archive:
            return archive.read(member)

    def check(name: str, condition: bool, detail: str = "") -> None:
        lines.append(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    lines.append(f"{APP_NAME} {APP_VERSION} 自检")
    lines.append(f"Python {sys.version.split()[0]} · frozen={getattr(sys, 'frozen', False)}")
    lines.append(f"可执行文件: {sys.executable}")
    lines.append("")

    # 1) 语言注册表
    languages = list(all_languages())
    check("语言注册表", len(languages) >= 20, f"{len(languages)} 种语言")
    for required in ("c", "cpp", "go", "java", "python", "php", "rust"):
        check(f"必需语言 {required}", any(spec.id == required for spec in languages))

    # 2) 语法高亮 (每种语言都能产出 token)
    for spec in languages:
        tokens = highlighted_tokens(spec.id, "int main() { return 0; } // x\n")
        check(f"高亮 {spec.name}", len(tokens) > 0, f"{len(tokens)} 个片段")

    # 3) 容器读写 + 校验 + 导出
    with tempfile.TemporaryDirectory(prefix="codemethod-selftest-") as tmp:
        repo = build_demo_repository()
        database = Database(repo)

        binary_path = os.path.join(tmp, "selftest.cmdb")
        text_path = os.path.join(tmp, "selftest.cmj")
        result_bin = database.save(binary_path)
        result_txt = database.save(text_path, binary=False, backup=False)
        check("保存二进制容器", os.path.exists(binary_path), result_bin.describe())
        check("保存文本容器", os.path.exists(text_path), result_txt.describe())

        ok_bin, problems_bin, _ = verify_file(binary_path)
        check("二进制容器完整性", ok_bin, "; ".join(problems_bin) or "CRC32/SHA-256 一致")
        ok_txt, problems_txt, _ = verify_file(text_path)
        check("文本容器完整性", ok_txt, "; ".join(problems_txt) or "integrity 一致")

        reopened = Database.open(binary_path)
        check(
            "重新打开并比对条目数",
            len(reopened.repository.entries) == len(repo.entries),
            f"{len(reopened.repository.entries)} 个条目",
        )
        check(
            "修订历史完整",
            reopened.repository.history.total_count() == repo.history.total_count(),
            f"{reopened.repository.history.total_count()} 条修订",
        )

        # 前置要求是按语言分开存的, 存盘再读回必须还是分开的
        multi = next(
            (e for e in repo.entries_list() if len(e.active_implementations) > 1), None
        )
        if multi is None:
            check("示例库含多语言实现", False, "找不到多语言条目")
        else:
            needs = [impl.prerequisites for impl in multi.active_implementations]
            check(
                "前置要求分语言存储",
                len(set(needs)) == len(needs) and all(needs),
                " / ".join(f"{n[:28]}" for n in needs),
            )
            reopened_entry = reopened.repository.get(multi.id)
            restored_needs = (
                [impl.prerequisites for impl in reopened_entry.active_implementations]
                if reopened_entry
                else []
            )
            check("前置要求存盘后仍按语言保留", restored_needs == needs)

        # ---- 三类实体都能存进同一个容器 ----
        stats = repo.statistics()
        check(
            "模块 / 空间 / 函数体 同时存在",
            stats["modules"] > 0 and stats["spaces"] > 0 and stats["functions"] > 0,
            f"{stats['modules']} 模块 · {stats['spaces']} 空间 · {stats['functions']} 函数体",
        )
        check(
            "容器清单统计三类实体",
            reopened.repository.statistics()["spaces"] == stats["spaces"],
        )

        # 独立空间: 项目结构完整往返
        sample_space = next(iter(repo.spaces.values()), None)
        if sample_space is None:
            check("示例库含独立空间", False)
        else:
            restored_space = reopened.repository.spaces.get(sample_space.id)
            check(
                "空间文件全部存进容器",
                restored_space is not None
                and {f.path for f in restored_space.files} == {f.path for f in sample_space.files},
                f"{len(sample_space.files)} 个文件 · {sample_space.language_summary()}",
            )
            check(
                "空间语言占比可计算",
                bool(sample_space.language_shares())
                and abs(sum(s.percent for s in sample_space.language_shares()) - 100.0) < 0.5,
                sample_space.language_summary(),
            )
            hits = repo.space_readme_hits("token")
            check(
                "README 自动索引可检索",
                len(hits) > 0,
                f"{len(sample_space.readme_files)} 个 README, 命中 {len(hits)} 处",
            )

        # 函数体: 多语言实现 + 变量检测 + 含义表往返
        sample_function = next(iter(repo.functions.values()), None)
        if sample_function is None:
            check("示例库含函数体", False)
        else:
            check(
                "同一个函数有多种语言实现",
                len(sample_function.active_implementations) >= 2,
                "、".join(impl.language_name for impl in sample_function.active_implementations),
            )
            check(
                "自动检测到变量声明",
                sample_function.total_symbols > 0,
                ", ".join(s.name for s in sample_function.all_symbols[:6]),
            )
            check(
                "每种语言有自己的前置要求",
                all(impl.prerequisites.strip() for impl in sample_function.active_implementations),
            )
            check(
                "变量含义已填写",
                not sample_function.required_symbols_missing_meaning,
            )
            restored_function = reopened.repository.functions.get(sample_function.id)
            check(
                "多语言实现的含义表存盘后保留",
                restored_function is not None
                and [
                    (impl.language, [(s.name, s.kind, s.meaning) for s in impl.symbols])
                    for impl in restored_function.active_implementations
                ]
                == [
                    (impl.language, [(s.name, s.kind, s.meaning) for s in impl.symbols])
                    for impl in sample_function.active_implementations
                ],
            )

        md = os.path.join(tmp, "lib.md")
        js = os.path.join(tmp, "lib.json")
        zp = os.path.join(tmp, "lib.zip")
        exporter.export_markdown(repo, md)
        exporter.export_json(repo, js)
        exporter.export_zip(repo, zp)
        check("导出 Markdown", os.path.getsize(md) > 0, f"{os.path.getsize(md)} 字节")
        check("导出 JSON", os.path.getsize(js) > 0, f"{os.path.getsize(js)} 字节")
        check("导出 ZIP", os.path.getsize(zp) > 0, f"{os.path.getsize(zp)} 字节")
        check(
            "ZIP 回读",
            len(exporter.read_zip_export(zp)["entries"]) == len(repo.entries),
        )
        # 空间在 ZIP 里应该还原成真实的项目目录树
        if sample_space is not None:
            import zipfile

            with zipfile.ZipFile(zp) as archive:
                names = archive.namelist()
            nested = [n for n in names if n.startswith("spaces/") and n.count("/") >= 2]
            check(
                "ZIP 里空间是真实目录树",
                len(nested) >= len(sample_space.files),
                f"{len(nested)} 个文件按原路径还原",
            )
            # 嵌入的二进制在 ZIP 里必须是**原始字节**, 而不是占位说明
            embedded = sample_space.embedded_binary_files
            if embedded:
                target = embedded[0]
                hit = [n for n in names if n.endswith(target.path)]
                check(
                    "ZIP 里二进制还原成原始字节",
                    bool(hit) and archive_bytes(zp, hit[0]) == target.raw_bytes(),
                    f"{target.path} · {len(target.raw_bytes())} 字节",
                )

        # 4) 界面构建 (offscreen, 覆盖 Qt 插件是否被正确打包)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        apply_theme(app, DEFAULT_THEME)
        window = MainWindow(Database(build_demo_repository()))
        # 把窗口的 QSettings 指到临时目录: 自检不应污染用户的真实配置
        window.settings = QSettings(os.path.join(tmp, "selftest.ini"), QSettings.Format.IniFormat)
        window.resize(1280, 800)
        window.show()
        for _ in range(4):
            app.processEvents()
        rows = window.entry_list.entry_model.rowCount()
        expected_rows = stats["modules"] + stats["spaces"] + stats["functions"]
        check("主窗口构建", rows == expected_rows, f"列表 {rows} 行 / 期望 {expected_rows}")

        # ---- 整目录打包: 扫描计划 → 照着计划导入 ----
        from .core.spaces import ImportOptions, scan_external_directory

        project = os.path.join(tmp, "packme")
        os.makedirs(os.path.join(project, "src", "utils"), exist_ok=True)
        os.makedirs(os.path.join(project, ".git", "objects"), exist_ok=True)
        os.makedirs(os.path.join(project, "__pycache__"), exist_ok=True)
        with open(os.path.join(project, "README.md"), "wb") as handle:
            handle.write("# packme\n中文说明\n".encode("utf-8"))
        with open(os.path.join(project, "src", "main.py"), "wb") as handle:
            handle.write(b"print(1)\n")
        with open(os.path.join(project, "src", "utils", "h.py"), "wb") as handle:
            handle.write(b"def h():\n    return 1\n")
        with open(os.path.join(project, ".git", "objects", "ab"), "wb") as handle:
            handle.write(b"\x00\x01")
        with open(os.path.join(project, "__pycache__", "m.pyc"), "wb") as handle:
            handle.write(b"\x00\x00")
        os.makedirs(os.path.join(project, "assets"), exist_ok=True)
        with open(os.path.join(project, "assets", "logo.png"), "wb") as handle:
            handle.write(DEMO_PNG)

        pack_scan = scan_external_directory(project, ImportOptions())
        check(
            "整目录扫描保留子目录结构",
            {f.path for f in pack_scan.included}
            == {"README.md", "src/main.py", "src/utils/h.py", "assets/logo.png"},
            "、".join(sorted(f.path for f in pack_scan.included)),
        )
        check(
            "整目录扫描跳过 .git / __pycache__ 并说明原因",
            {path for path, _r in pack_scan.pruned_dirs} >= {".git", "__pycache__"},
            "、".join(f"{p}({r})" for p, r in pack_scan.pruned_dirs),
        )
        check(
            "整目录扫描区分文本与二进制",
            len(pack_scan.text_files) == 3 and len(pack_scan.binary_files) == 1,
            pack_scan.summary(),
        )
        pack_space = repo.create_space("打包验证")
        pack_report = repo.import_scan(pack_space.id, pack_scan)
        packed = {f.path for f in repo.require_space(pack_space.id).files}
        check(
            "照着计划导入的结果与计划一致",
            packed == {f.path for f in pack_scan.included} and pack_report.added == 4,
            f"{pack_report.added} 个文件",
        )
        check(
            "打包后二进制字节一致",
            repo.require_space(pack_space.id).get_file("assets/logo.png").raw_bytes() == DEMO_PNG,
        )
        check(
            "打包只产生一条修订",
            repo.revisions(pack_space.id)[0].action == "file_add"
            and "打包导入" in repo.revisions(pack_space.id)[0].summary,
            repo.revisions(pack_space.id)[0].summary,
        )
        from unittest import mock as _mock

        with _mock.patch("codemethod.core.spaces.MAX_SPACE_BINARY_BYTES", 1):
            try:
                repo.import_scan(pack_space.id, pack_scan)
                check("超预算时拒绝打包", False, "竟然没报错")
            except Exception as exc:
                check("超预算时拒绝打包", "只剩" in str(exc), str(exc).splitlines()[0])
            forced = repo.import_scan(pack_space.id, pack_scan, force=True)
            check("显式确认后可以越过软上限", forced.changed > 0, forced.summary())
        repo.delete_item(pack_space.id, hard=True)

        # ---- 标签统计必须覆盖三类实体 ----
        tag_probe = Repository(name="tagprobe")
        tag_probe.create_entry("m", "d", "p", ["shared"])
        tag_probe.create_space("s", "d", "p", ["shared", "space-only"])
        tag_probe.create_function("f", "python", "def f():\n    return 1\n", tags=["shared"])
        usage = tag_probe.tag_usage()
        check(
            "标签统计覆盖模块/空间/函数体",
            usage.get("shared") == 3 and usage.get("space-only") == 1,
            str(usage),
        )

        # ---- 对话框必须放得进屏幕 (否则底部的「保存」会跑到屏幕外) ----
        from .ui.layout_util import SCREEN_MARGIN, available_geometry, clamp_size

        rect = available_geometry(window)
        if rect is None:
            check("取得屏幕可用区域", False, "拿不到屏幕信息")
        else:
            width, height = clamp_size(4000, 3000)
            check(
                "超大对话框会被夹到屏幕内",
                width <= int(rect.width() * SCREEN_MARGIN) + 1
                and height <= int(rect.height() * SCREEN_MARGIN) + 1,
                f"屏幕 {rect.width()}×{rect.height()} → 夹到 {width}×{height}",
            )
            check(
                "小尺寸对话框不被放大",
                clamp_size(600, 400) == (600, 400),
            )
            # 主窗口自己也不能超出屏幕 —— 否则它下面的对话框会跟着沉到屏幕外
            frame = window.frameGeometry()
            check(
                "主窗口完整落在屏幕可用区域内",
                rect.contains(frame),
                f"窗口 {frame.width()}×{frame.height()} / 屏幕 {rect.width()}×{rect.height()}",
            )

        # ---- 模块: 多语言页签 ----
        target = next(
            (e for e in window.db.repository.entries_list() if len(e.active_implementations) > 1),
            None,
        )
        if target is None:
            check("存在多语言实现条目", False, "示例库里找不到")
        else:
            window.entry_list.select_entry(target.id)
            window.refresh_detail()
            for _ in range(3):
                app.processEvents()
            expected_tabs = 1 + len(target.active_implementations)
            shown = window.detail_panel.current_entry()
            check(
                "选中条目后详情跟随",
                shown is not None and shown.id == target.id,
                shown.display_title if shown else "空",
            )
            check(
                "每个语言一个页签",
                window.detail_panel.tabs.count() == expected_tabs,
                f"{window.detail_panel.tabs.count()} 个页签 / 期望 {expected_tabs}",
            )
            check(
                "语法高亮已挂载",
                len(window.detail_panel._previews) == len(target.active_implementations),
                f"{len(window.detail_panel._previews)} 个代码视图",
            )

        # ---- 空间: 详情面板切到项目结构 ----
        # 注意: 这里的示例必须取自**窗口自己的**代码库 —— 上面的 repo 是另一份实例,
        # id 并不相同, 用它的 id 去选中会选不中。
        ui_repo = window.db.repository
        ui_space = next(iter(ui_repo.spaces.values()), None)
        ui_function = next(iter(ui_repo.functions.values()), None)
        if ui_space is not None:
            window.entry_list.select_entry(ui_space.id)
            window.refresh_detail()
            for _ in range(3):
                app.processEvents()
            check(
                "空间详情面板被激活",
                window.detail_stack.currentWidget() is window.space_panel,
            )
            space_shown = window.space_panel.current_space()
            check("空间详情跟随", space_shown is not None and space_shown.id == ui_space.id)
            check(
                "项目结构树已填充",
                window.space_panel.tree.topLevelItemCount() > 0,
                f"{len(ui_space.files)} 个文件",
            )
            check(
                "语言占比条已渲染",
                len(window.space_panel.language_bar._shares) > 0,
                ui_space.language_summary(),
            )
            # 嵌入的二进制文件: 内容在库里 + 有专门的预览页
            embedded = ui_space.embedded_binary_files
            check(
                "二进制文件已嵌入库内",
                bool(embedded) and all(f.raw_bytes() for f in embedded),
                "、".join(f"{f.path} ({f.size}B)" for f in embedded) or "无",
            )
            if embedded:
                binary_file = embedded[0]
                check(
                    "嵌入的字节与原始文件一致",
                    binary_file.raw_bytes() == DEMO_PNG,
                    f"{len(binary_file.raw_bytes())} 字节",
                )
                window.space_panel.show_file(binary_file.path)
                for _ in range(3):
                    app.processEvents()
                check(
                    "二进制预览页被激活",
                    window.space_panel.tabs.currentIndex() == 2
                    and window.space_panel.binary_preview.meta_label.text() != "",
                    window.space_panel.binary_preview.meta_label.text(),
                )
                window.space_panel.show_file(sorted(
                    f.path for f in ui_space.files if not f.binary
                )[0])

        # ---- 函数体: 详情面板切到变量表 ----
        if ui_function is not None:
            window.entry_list.select_entry(ui_function.id)
            window.refresh_detail()
            for _ in range(3):
                app.processEvents()
            check(
                "函数体详情面板被激活",
                window.detail_stack.currentWidget() is window.function_panel,
            )
            function_shown = window.function_panel.current_function()
            current_impl = function_shown.active_implementations[0] if (
                function_shown is not None and function_shown.active_implementations
            ) else None
            check(
                "变量含义表已填充",
                current_impl is not None
                and window.function_panel.symbol_table.rowCount() == len(current_impl.symbols),
                f"{window.function_panel.symbol_table.rowCount()} 行",
            )
            check(
                "语言实现下拉框列出全部语言",
                function_shown is not None
                and window.function_panel.language_combo.count()
                == len(function_shown.active_implementations),
                f"{window.function_panel.language_combo.count()} 种语言",
            )

        # ---- 类别切换 ----
        window.set_kind_filter("space")
        for _ in range(3):
            app.processEvents()
        check(
            "类别切换只看空间",
            window.entry_list.entry_model.rowCount() == stats["spaces"],
            f"{window.entry_list.entry_model.rowCount()} 行",
        )
        window.set_kind_filter("all")
        for _ in range(3):
            app.processEvents()

        # 标记为已保存, 否则 close() 会弹出"是否保存"的模态框把自检卡死
        window.db.repository.mark_clean()
        window.close()
        for _ in range(3):
            app.processEvents()
        # 显式销毁, 这样自检也可以在单元测试里被反复调用而不会残留窗口
        window.deleteLater()
        for _ in range(3):
            app.processEvents()

    lines.append("")
    lines.append(f"结果: {'全部通过' if not failures else '失败 ' + str(len(failures)) + ' 项: ' + ', '.join(failures)}")
    report = "\n".join(lines)

    if report_path:
        try:
            with open(report_path, "w", encoding="utf-8") as handle:
                handle.write(report + "\n")
        except OSError as exc:  # pragma: no cover - 防御
            print(f"无法写入报告 {report_path}: {exc}", file=sys.stderr)
    print(report)
    return 0 if not failures else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = create_argument_parser().parse_args(argv)

    # 允许在无显示器环境 (CI / 容器) 下运行界面代码
    if os.environ.get("CODEMETHOD_OFFSCREEN"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    if args.selftest is not None:
        # 自检完全离屏运行, 用于验证打包产物 (CI 或人工确认)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        report = None if args.selftest in ("", "-") else args.selftest
        return run_self_test(report)

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(list(argv) if argv else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)
    app.setWindowIcon(app_icon())

    theme = get_theme(args.theme)
    apply_theme(app, theme)

    database: Optional[Database] = None
    if args.file:
        try:
            database = Database.open(args.file)
        except DatabaseError as exc:
            print(f"无法打开 {args.file}: {exc}", file=sys.stderr)
            return 2
    elif args.demo:
        database = Database(build_demo_repository())
    if database is None:
        database = Database.create()

    window = MainWindow(database, theme_key=theme.key)
    window.show()

    if args.demo and not args.file:
        window.set_status("已载入示例库 — 按 Ctrl+N 添加你自己的条目, Ctrl+S 保存为 .cmdb")
    return app.exec()


__all__ = ["main", "create_argument_parser", "build_demo_repository"]
