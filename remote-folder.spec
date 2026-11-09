# -*- mode: python ; coding: utf-8 -*-
"""Build one Windows EXE with Python, using the system WebView2 runtime."""

import struct
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

if sys.platform != "win32" or struct.calcsize("P") != 8:
    raise SystemExit("Build the Windows x64 release with 64-bit Python on Windows.")

datas = [("static", "static")]
binaries = []
hiddenimports = [
    "clr",
    "pythonnet",
    "webview.platforms.edgechromium",
    "keyring.backends.Windows",
    "win32ctypes",
    "win32ctypes.core",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("uvicorn")

for package in ("webview", "pythonnet", "clr_loader", "cryptography", "paramiko", "keyring"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["app/__main__.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="remote-folder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
