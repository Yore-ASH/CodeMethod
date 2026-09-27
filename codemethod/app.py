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
        check("主窗口构建", rows == 4, f"列表 {rows} 行")

        # 选中一个"有多种语言实现"的条目, 顺带验证多语言页签在打包产物里也正常
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
