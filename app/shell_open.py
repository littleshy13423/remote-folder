from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


_OAIF_ALLOW_REGISTRATION = 0x1
_OAIF_EXEC = 0x4
_ERROR_CANCELLED = 0x800704C7
_ERROR_NO_ASSOCIATION = 1155


def open_default(path: Path) -> None:
    if _record("open", path):
        return
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError as exc:
        if getattr(exc, "winerror", None) == _ERROR_NO_ASSOCIATION:
            raise OSError("这个后缀还没有关联程序。请右键该文件，选择「打开方式」。") from exc
        raise OSError(f"无法打开：{exc}") from exc


def choose_application(path: Path) -> bool:
    """Show the system Open With dialog. Return True when the user cancels."""
    if _record("choose", path):
        return False
    if os.name != "nt":
        open_default(path)
        return False
    try:
        return _choose_dialog(path)
    except OSError:
        raise
    except Exception:
        _choose_rundll(path)
        return False


def _choose_dialog(path: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    class OpenAsInfo(ctypes.Structure):
        _fields_ = [
            ("pcszFile", ctypes.c_wchar_p),
            ("pcszClass", ctypes.c_wchar_p),
            ("oaifInFlags", wintypes.DWORD),
        ]

    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    foreground = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    this_thread = kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != this_thread and user32.AttachThreadInput(this_thread, fg_thread, True))

    ole32 = ctypes.WinDLL("ole32")
    hr_init = ole32.CoInitializeEx(None, 0x2)
    try:
        info = OpenAsInfo(str(path), None, _OAIF_ALLOW_REGISTRATION | _OAIF_EXEC)
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHOpenWithDialog.argtypes = [wintypes.HWND, ctypes.POINTER(OpenAsInfo)]
        shell32.SHOpenWithDialog.restype = ctypes.c_long
        code = shell32.SHOpenWithDialog(foreground or None, ctypes.byref(info)) & 0xFFFFFFFF
    finally:
        if hr_init in (0, 1):
            ole32.CoUninitialize()
        if attached:
            user32.AttachThreadInput(this_thread, fg_thread, False)
    if code == 0:
        return False
    if code in (_ERROR_CANCELLED, 1):
        return True
    raise OSError(f"打不开「打开方式」窗口（{code:#x}）")


def _choose_rundll(path: Path) -> None:
    subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(path)])


def _record(action: str, path: Path) -> bool:
    target = os.environ.get("RF_OPEN_DRY_RUN")
    if not target:
        return False
    Path(target).write_text(
        json.dumps({"action": action, "path": str(path)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return True
