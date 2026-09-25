"""应用装配与启动。

用法::

    python -m codemethod                 # 启动 GUI (空库)
    python -m codemethod 我的代码库.cmdb  # 启动并打开指定文件
    python main.py                        # 等价入口
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from . import APP_NAME, APP_VERSION
from .core.repository import Repository
from .storage.database import Database, DatabaseError
from .ui.main_window import MainWindow
from .ui.resources import app_icon
from .ui.theme import DEFAULT_THEME, apply_theme, get_theme


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
        prerequisites="Python 3.10+ / Go 1.21+；无需第三方库；本地可用的 9000 端口。",
        tags=["network", "tcp", "server", "demo"],
        status="done",
        favorite=True,
    )
    repo.add_implementation(
        tcp.id,
        "python",
        title="基于 asyncio",
        notes="单线程事件循环, 适合高并发连接。",
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
        prerequisites="需要理解双向链表；Python 版本可直接用 OrderedDict；C++ 版本需要 C++17。",
        tags=["algorithm", "cache", "lru", "demo"],
        status="in_progress",
    )
    repo.add_implementation(
        lru.id,
        "python",
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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = create_argument_parser().parse_args(argv)

    # 允许在无显示器环境 (CI / 容器) 下运行界面代码
    if os.environ.get("CODEMETHOD_OFFSCREEN"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
