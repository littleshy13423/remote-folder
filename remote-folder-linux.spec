# -*- mode: python ; coding: utf-8 -*-
"""Build one Linux x64 binary. GTK and WebKitGTK stay on the system."""

import struct
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

if not sys.platform.startswith("linux") or struct.calcsize("P") != 8:
    raise SystemExit("Build the Linux x64 release with 64-bit Python on Linux.")

datas = [("static", "static")]
binaries = []
hiddenimports = [
    "webview.platforms.gtk",
    "gi",
    "keyring.backends.SecretService",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.null",
    "jeepney",
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

for package in ("cryptography", "paramiko", "keyring"):
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
    excludes=["pytest", "tkinter", "clr", "pythonnet", "win32ctypes"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="remote-folder-linux-x64",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
