# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 — 把 CodeMethod 封装成单文件 Windows 可执行程序.

用法::

    pyinstaller codemethod.spec --noconfirm            # 单文件 (推荐分发)
    CODEMETHOD_ONEDIR=1 pyinstaller codemethod.spec    # 目录模式 (启动更快)

产物: ``dist/CodeMethod.exe`` (单文件) 或 ``dist/CodeMethod/`` (目录模式)。

说明
----
* 程序没有任何数据文件依赖 —— 图标由 ``codemethod/ui/resources.py`` 用 QPainter 现场绘制,
  源码内也不读取外部资源, 因此不需要 ``datas``。
* 主动排除 Qt 中本程序用不到的庞大模块 (WebEngine / 3D / Multimedia / Quick 等),
  可把产物体积从 ~250 MB 压到 ~60 MB 量级。
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

ONEDIR = bool(os.environ.get("CODEMETHOD_ONEDIR"))

# 只需要这些 PySide6 子模块
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

# 明确排除: 体积巨大且与本程序无关
excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQml",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtUiTools",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtNetwork",
    "PySide6.QtXml",
    # 常见但无关的第三方库
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "PIL",
    "pytest",
    "setuptools",
    "pip",
]

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon_path = os.path.join("build_assets", "codemethod.ico")
if not os.path.exists(icon_path):
    icon_path = None

if ONEDIR:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="CodeMethod",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon_path,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="CodeMethod",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="CodeMethod",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon_path,
    )
