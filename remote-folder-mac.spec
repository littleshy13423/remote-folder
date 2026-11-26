# -*- mode: python ; coding: utf-8 -*-
"""Build a macOS app that uses the system WebKit."""

import struct
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

if sys.platform != "darwin" or struct.calcsize("P") != 8:
    raise SystemExit("Build the macOS release with 64-bit Python on macOS.")

datas = [("static", "static")]
binaries = []
hiddenimports = [
    "webview.platforms.cocoa",
    "objc",
    "WebKit",
    "AppKit",
    "Foundation",
    "keyring.backends.macOS",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.null",
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
    excludes=["pytest", "tkinter", "clr", "pythonnet", "gi"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="remote-folder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="remote-folder",
)
app = BUNDLE(
    coll,
    name="remote-folder.app",
    bundle_identifier="app.remote-folder",
)
