# -*- mode: python ; coding: utf-8 -*-
"""Build one Windows EXE, including Python and a private WebView2 runtime."""

import os
import struct
import sys
from pathlib import Path

from PyInstaller.building.datastruct import Tree
from PyInstaller.utils.hooks import collect_all, collect_submodules

if sys.platform != "win32" or struct.calcsize("P") != 8:
    raise SystemExit("Build the Windows x64 release with 64-bit Python on Windows.")

runtime_dir = Path(os.environ.get("RF_WEBVIEW2_RUNTIME", "build/webview2/runtime"))
if not (runtime_dir / "msedgewebview2.exe").is_file():
    raise SystemExit("The bundled WebView2 runtime is missing. Run build.ps1 first.")

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
# Keep Microsoft's signed runtime files byte-for-byte unchanged. Adding this
# tree after Analysis also avoids scanning its browser DLLs as Python binaries.
a.datas += Tree(str(runtime_dir), prefix="webview2", excludes=["._*"])
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
