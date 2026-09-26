#!/usr/bin/env python
"""一键构建脚本: 把 CodeMethod 封装成 Windows 可执行程序 (PyInstaller).

用法::

    python build.py                 # 单文件 dist/CodeMethod.exe
    python build.py --onedir        # 目录版 dist/CodeMethod/ (启动更快)
    python build.py --all           # 两种都构建
    python build.py --zip           # 目录版额外打成 dist/CodeMethod-<版本>-win64.zip
    python build.py --all --zip     # 一次拿到全部发行物
    python build.py --tests         # 构建前先跑 191 个单元测试
    python build.py --no-selftest   # 跳过构建后的自动自检

构建完成后默认会对产物执行 ``CodeMethod.exe --selftest`` (离屏跑容器读写/导出/
高亮/界面构建), 自检不通过则整体返回非零 —— 避免"打出来了但跑不起来"。

脚本会在需要时自动 ``pip install pyinstaller``。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "build_assets")
ICO = os.path.join(ASSETS, "codemethod.ico")
VERSION_FILE = os.path.join(ASSETS, "version_info.txt")
SPEC = os.path.join(ROOT, "codemethod.spec")
DIST = os.path.join(ROOT, "dist")
# 目录模式下 PyInstaller 的中间产物: 旁边没有 _internal, 双击会报
# "Failed to load Python DLL ...\build\codemethod\_internal\python3xx.dll"
STAGING_EXE = os.path.join(ROOT, "build", "codemethod", "CodeMethod.exe")
BUILD_README = os.path.join(ROOT, "build", "README.txt")

# Windows 控制台默认可能是 GBK, 中文/符号会直接抛 UnicodeEncodeError, 这里统一成 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老版本或已重定向
        pass


def app_version() -> str:
    """从包元信息读取版本号 (不导入 PySide6, 构建早期也能用)。"""
    namespace: dict = {}
    init_path = os.path.join(ROOT, "codemethod", "__init__.py")
    with open(init_path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(("APP_NAME", "APP_VERSION")):
                key, _, value = line.partition("=")
                namespace[key.strip()] = value.strip().strip('"').strip("'")
    return namespace.get("APP_VERSION", "0.0.0")


def run(cmd, **kwargs) -> int:
    printable = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
    print(">>", printable, flush=True)
    return subprocess.call(cmd, cwd=ROOT, **kwargs)


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024.0 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} GB"


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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

    Qt 的 ICO 写入器只写单尺寸, 因此手工拼装 ICO 容器 (内嵌多张 PNG)。
    """
    os.makedirs(ASSETS, exist_ok=True)
    script = f'''
import os, struct, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, {ROOT!r})
from PySide6.QtCore import QBuffer
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


def make_version_file(version: str) -> str:
    """生成 PyInstaller 的 Windows 版本资源文件 (影响"属性 → 详细信息")。"""
    os.makedirs(ASSETS, exist_ok=True)
    parts = [int(p) if p.isdigit() else 0 for p in version.split(".")]
    while len(parts) < 4:
        parts.append(0)
    quad = ", ".join(str(p) for p in parts[:4])
    content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({quad}),
    prodvers=({quad}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', 'CodeMethod'),
        StringStruct('FileDescription', 'CodeMethod — 多标签代码实现规划与知识库'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'CodeMethod'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', 'CodeMethod.exe'),
        StringStruct('ProductName', 'CodeMethod'),
        StringStruct('ProductVersion', '{version}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""
    with open(VERSION_FILE, "w", encoding="utf-8") as handle:
        handle.write(content)
    print("版本资源已生成:", VERSION_FILE)
    return VERSION_FILE


def clean() -> None:
    for name in ("build", "dist", "__pycache__"):
        target = os.path.join(ROOT, name)
        if os.path.isdir(target):
            print("清理", target)
            shutil.rmtree(target, ignore_errors=True)


def pyinstaller(onedir: bool) -> int:
    env = dict(os.environ)
    if onedir:
        env["CODEMETHOD_ONEDIR"] = "1"
    else:
        env.pop("CODEMETHOD_ONEDIR", None)
    print(">> pyinstaller codemethod.spec --noconfirm "
          + ("(目录模式)" if onedir else "(单文件)"))
    return subprocess.call(
        [sys.executable, "-m", "PyInstaller", SPEC, "--noconfirm"], cwd=ROOT, env=env
    )


def exe_path(onedir: bool) -> str:
    if onedir:
        return os.path.join(DIST, "CodeMethod", "CodeMethod.exe")
    return os.path.join(DIST, "CodeMethod.exe")


def run_selftest(path: str) -> bool:
    """对构建产物执行内置自检, 返回是否通过。"""
    print(f">> {os.path.basename(path)} --selftest")
    report = os.path.join(ROOT, "build", f"selftest-{os.path.basename(path)}.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"   # 不弹窗
    env.pop("PYTHONPATH", None)
    try:
        code = subprocess.call([path, "--selftest", report], cwd=ROOT, env=env)
    except OSError as exc:
        print("无法启动产物:", exc, file=sys.stderr)
        return False

    if os.path.exists(report):
        with open(report, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        failures = [line for line in lines if line.startswith("[FAIL]")]
        total = sum(1 for line in lines if line.startswith("["))
        print(f"   自检: {total - len(failures)}/{total} 项通过, 报告 {report}")
        for line in failures:
            print("   " + line)
    else:
        print("   自检未生成报告 (退出码 %d)" % code, file=sys.stderr)
    return code == 0


def make_zip(version: str) -> str:
    """把目录版打包成 zip, 便于直接分发。"""
    source = os.path.join(DIST, "CodeMethod")
    if not os.path.isdir(source):
        print("没有目录版产物, 跳过打包 zip。先运行 --onedir 或 --all。", file=sys.stderr)
        return ""
    target = os.path.join(DIST, f"CodeMethod-{version}-win64.zip")
    print(">> 打包", target)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for base, _dirs, files in os.walk(source):
            for name in files:
                full = os.path.join(base, name)
                archive.write(full, os.path.relpath(full, source))
    return target


def report_artifact(path: str) -> None:
    if not os.path.exists(path):
        return
    size = os.path.getsize(path)
    print(f"   {os.path.relpath(path, ROOT):<42} {human_size(size):>10}  sha256 {sha256_of(path)[:16]}…")


def remove_staging_exe() -> bool:
    """删除目录模式的中间产物 exe (spec 里已删一次, 这里兜底)。

    那个 exe 与真正的成品同名同图标, 但旁边没有 ``_internal``, 用户误点会看到
    ``Failed to load Python DLL`` —— 留着它只有坏处。
    """
    if not os.path.exists(STAGING_EXE):
        return True
    try:
        os.remove(STAGING_EXE)
        print("   已移除中间产物:", os.path.relpath(STAGING_EXE, ROOT))
        return True
    except OSError as exc:
        print(f"   无法移除中间产物 {STAGING_EXE}: {exc}", file=sys.stderr)
        return False


def write_build_readme(version: str) -> None:
    """在 build/ 放一个说明, 防止有人把中间产物当成发行物。"""
    try:
        os.makedirs(os.path.dirname(BUILD_README), exist_ok=True)
        with open(BUILD_README, "w", encoding="utf-8") as handle:
            handle.write(
                f"""这里是 PyInstaller 的中间构建目录 — 不要运行这里的任何 exe
============================================================

本目录里的 CodeMethod.exe (如果存在) 是**引导程序半成品**:
它旁边没有 _internal\\, 直接双击会报

    Failed to load Python DLL '...\\build\\codemethod\\_internal\\python3xx.dll'

真正的发行物在 dist\\ 下, 请运行:

    dist\\CodeMethod.exe                    单文件绿色版 (推荐, 拷走即用)
    dist\\CodeMethod\\CodeMethod.exe         目录版 (整个 CodeMethod\\ 文件夹一起拷贝)
    dist\\CodeMethod-{version}-win64.zip     目录版的压缩包

注意: 目录版必须连同同级的 _internal\\ 文件夹一起移动, 只拷 exe 会报同样的错。
想只拿一个文件, 用单文件版 dist\\CodeMethod.exe。

本目录可以随时删除; 重新构建时会自动生成。
"""
            )
    except OSError:  # pragma: no cover - 防御
        pass


def verify_layout(built: list) -> list:
    """检查发行物布局, 返回问题列表 (空列表 = 没问题)。"""
    problems: list = []
    for path in built:
        if not os.path.exists(path):
            problems.append(f"缺少产物: {path}")
            continue
        # 目录版必须自带 _internal, 否则运行时找不到 python3xx.dll / Qt 插件
        if os.path.basename(os.path.dirname(path)) == "CodeMethod":
            internal = os.path.join(os.path.dirname(path), "_internal")
            if not os.path.isdir(internal):
                problems.append(f"目录版缺少 _internal 目录: {internal}")
            else:
                dll = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
                if not os.path.exists(os.path.join(internal, dll)):
                    problems.append(f"目录版 _internal 里没有 {dll}")
    if os.path.exists(STAGING_EXE):
        problems.append(f"build/ 下仍残留中间产物 exe (极易被误运行): {STAGING_EXE}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 CodeMethod 可执行程序")
    parser.add_argument("--onedir", action="store_true", help="只构建目录模式 (启动更快)")
    parser.add_argument("--all", action="store_true", help="单文件与目录模式都构建")
    parser.add_argument("--zip", action="store_true", help="把目录版打成 zip 分发包")
    parser.add_argument("--ico", action="store_true", help="强制重新生成 .ico 图标")
    parser.add_argument("--tests", action="store_true", help="构建前运行单元测试")
    parser.add_argument("--no-selftest", action="store_true", help="跳过构建后的自动自检")
    parser.add_argument("--clean", action="store_true", help="构建前清理 build/ 与 dist/")
    args = parser.parse_args()

    version = app_version()
    print(f"=== 构建 CodeMethod {version} ===")

    if args.tests:
        print(">> 构建前测试")
        if run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]) != 0:
            print("测试未通过, 已中止构建。", file=sys.stderr)
            return 1

    if args.clean:
        clean()

    if not ensure_pyinstaller():
        return 2

    if args.ico or not os.path.exists(ICO):
        make_ico()
    make_version_file(version)

    want_onedir = args.onedir or args.all
    want_onefile = (not args.onedir) or args.all

    built: list = []
    for onedir in ([False, True] if (want_onefile and want_onedir) else
                   ([True] if want_onedir else [False])):
        if pyinstaller(onedir) != 0:
            print("打包失败。", file=sys.stderr)
            return 3
        path = exe_path(onedir)
        if not os.path.exists(path):
            print(f"未找到产物: {path}", file=sys.stderr)
            return 4
        built.append(path)

    if not args.no_selftest:
        for path in built:
            if not run_selftest(path):
                print("自检未通过, 构建视为失败。", file=sys.stderr)
                return 5

    # 清掉中间产物并检查发行物布局 (防止用户点到跑不起来的半成品)
    remove_staging_exe()
    write_build_readme(version)
    layout_problems = verify_layout(built)
    if layout_problems:
        print("\n发行物布局检查未通过:", file=sys.stderr)
        for item in layout_problems:
            print("  • " + item, file=sys.stderr)
        return 6

    zip_path = make_zip(version) if args.zip else ""

    print()
    print("=== 发行物 ===")
    for path in built:
        report_artifact(path)
    if zip_path:
        report_artifact(zip_path)
    print()
    print("请运行上面的 dist 产物 (不要运行 build/ 下的中间文件):")
    if any(os.path.basename(os.path.dirname(p)) != "CodeMethod" for p in built):
        print("  单文件版: dist\\CodeMethod.exe          ← 拷走即可, 只有一个文件")
    if any(os.path.basename(os.path.dirname(p)) == "CodeMethod" for p in built):
        print("  目录版:   dist\\CodeMethod\\CodeMethod.exe ← 整个 CodeMethod\\ 文件夹一起拷贝")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
