#!/usr/bin/env python
"""一键构建脚本: 生成 Windows 可执行程序 (PyInstaller 封装).

用法::

    python build.py                # 单文件 dist/CodeMethod.exe
    python build.py --onedir       # 目录模式 dist/CodeMethod/
    python build.py --ico          # 先从绘制好的图标生成 build_assets/codemethod.ico
    python build.py --tests        # 构建前先跑测试
    python build.py --clean        # 清理 build/ dist/

脚本会在需要时自动 ``pip install pyinstaller``。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "build_assets")
ICO = os.path.join(ASSETS, "codemethod.ico")
SPEC = os.path.join(ROOT, "codemethod.spec")

# Windows 控制台默认可能是 GBK, 中文/符号会直接抛 UnicodeEncodeError, 这里统一成 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老版本或已重定向
        pass


def run(cmd, **kwargs) -> int:
    printable = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    print(">>", printable, flush=True)
    return subprocess.call(cmd, cwd=ROOT, **kwargs)


def ensure_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("未检测到 PyInstaller, 正在安装…")
        if run([sys.executable, "-m", "pip", "install", "pyinstaller"]) != 0:
            print("PyInstaller 安装失败。", file=sys.stderr)
            return False
    return True


def make_ico() -> bool:
    """把程序内绘制的图标导出为多尺寸 .ico (供 Windows 可执行文件使用)。

    Qt 的 ICO 写入器只写单尺寸, 因此这里手工拼装 ICO 容器 (内嵌多张 PNG)。
    """
    os.makedirs(ASSETS, exist_ok=True)
    script = f'''
import os, struct, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, {ROOT!r})
from PySide6.QtCore import QBuffer, QByteArray
from PySide6.QtWidgets import QApplication
app = QApplication([])
from codemethod.ui.resources import app_icon
icon = app_icon()
sizes = [16, 24, 32, 48, 64, 128, 256]
pngs = []
for size in sizes:
    pixmap = icon.pixmap(size, size)
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    pngs.append(bytes(buffer.data()))
header = struct.pack("<HHH", 0, 1, len(pngs))
entries = b""
offset = 6 + 16 * len(pngs)
for size, data in zip(sizes, pngs):
    dim = 0 if size >= 256 else size
    entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
    offset += len(data)
with open(r"{ICO}", "wb") as handle:
    handle.write(header + entries + b"".join(pngs))
print("ICO 已生成:", r"{ICO}", len(pngs), "个尺寸")
'''
    return run([sys.executable, "-c", script]) == 0


def clean() -> None:
    for name in ("build", "dist", "__pycache__"):
        target = os.path.join(ROOT, name)
        if os.path.isdir(target):
            print("清理", target)
            shutil.rmtree(target, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 CodeMethod 可执行程序")
    parser.add_argument("--onedir", action="store_true", help="使用目录模式 (启动更快)")
    parser.add_argument("--ico", action="store_true", help="生成 .ico 图标后再打包")
    parser.add_argument("--tests", action="store_true", help="构建前运行测试")
    parser.add_argument("--clean", action="store_true", help="构建前清理 build/ 与 dist/")
    args = parser.parse_args()

    if args.tests:
        if run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]) != 0:
            print("测试未通过, 已中止构建。", file=sys.stderr)
            return 1

    if args.clean:
        clean()

    if args.ico or not os.path.exists(ICO):
        make_ico()

    if not ensure_pyinstaller():
        return 2

    env = dict(os.environ)
    if args.onedir:
        env["CODEMETHOD_ONEDIR"] = "1"
    else:
        env.pop("CODEMETHOD_ONEDIR", None)

    print(">> pyinstaller codemethod.spec --noconfirm")
    code = subprocess.call(
        [sys.executable, "-m", "PyInstaller", SPEC, "--noconfirm"], cwd=ROOT, env=env
    )
    if code != 0:
        print("打包失败。", file=sys.stderr)
        return code

    print()
    if args.onedir:
        print("构建完成: dist/CodeMethod/CodeMethod.exe")
    else:
        print("构建完成: dist/CodeMethod.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
