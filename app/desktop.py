from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading

from app import downloads


class DesktopBridge:
    def __init__(self) -> None:
        self._window = None

    def double_click_time_ms(self) -> int:
        if sys.platform == "darwin":
            return _mac_double_click_time()
        if os.name != "nt":
            return _linux_double_click_time()
        return ctypes.windll.user32.GetDoubleClickTime()

    def show_download_history(self) -> dict:
        window = self._window
        form = getattr(window, "native", None) if window is not None else None
        control = getattr(form, "webview", None) if form is not None else None
        if control is None:
            return {"ok": False, "error": "窗口还没准备好"}
        holder: dict[str, str] = {}

        def open_dialog() -> None:
            try:
                control.CoreWebView2.OpenDefaultDownloadDialog()
            except Exception as exc:
                holder["error"] = str(exc) or "打不开下载记录"

        try:
            from System import Action

            if control.InvokeRequired:
                control.Invoke(Action(open_dialog))
            else:
                open_dialog()
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "打不开下载记录"}
        if holder.get("error"):
            return {"ok": False, "error": holder["error"]}
        return {"ok": True}

    def choose_save_path(self, filename: str = "") -> dict:
        if os.name != "nt":
            return self._choose_save_path_linux(filename)
        window = self._window
        form = getattr(window, "native", None) if window is not None else None
        if window is None or form is None:
            return {"ok": False, "error": "窗口还没准备好"}
        from webview import FileDialog

        holder: dict[str, object] = {}

        def ask() -> None:
            try:
                holder["result"] = window.create_file_dialog(
                    FileDialog.SAVE,
                    directory=downloads.downloads_dir(),
                    save_filename=filename or "download",
                )
            except Exception as exc:
                holder["error"] = str(exc) or "无法选择保存位置"

        try:
            from System import Action

            if form.InvokeRequired:
                form.Invoke(Action(ask))
            else:
                ask()
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "无法选择保存位置"}
        return _save_choice(holder.get("result"), holder.get("error"))

    def _choose_save_path_linux(self, filename: str) -> dict:
        window = self._window
        if window is None:
            return {"ok": False, "error": "窗口还没准备好"}
        from webview import FileDialog

        holder: dict[str, object] = {}

        def ask() -> None:
            try:
                holder["result"] = window.create_file_dialog(
                    FileDialog.SAVE,
                    directory=downloads.downloads_dir(),
                    save_filename=filename or "download",
                )
            except Exception as exc:
                holder["error"] = str(exc) or "无法选择保存位置"

        try:
            _run_on_gui(ask)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "无法选择保存位置"}
        return _save_choice(holder.get("result"), holder.get("error"))


def _save_choice(result: object, error: object) -> dict:
    if error:
        return {"ok": False, "error": str(error)}
    if not result:
        return {"ok": False, "cancelled": True}
    path = result[0] if isinstance(result, (list, tuple)) else str(result)
    if not path:
        return {"ok": False, "cancelled": True}
    return {"ok": True, "token": downloads.issue(path), "path": path}


def _run_on_gui(fn):
    if threading.current_thread() is threading.main_thread():
        return fn()
    if sys.platform == "darwin":
        return _run_on_cocoa_main(fn)
    try:
        from gi.repository import GLib
    except Exception:
        return fn()
    box: dict[str, object] = {}
    done = threading.Event()

    def run() -> bool:
        try:
            box["value"] = fn()
        except Exception as exc:
            box["error"] = exc
        finally:
            done.set()
        return False

    GLib.idle_add(run)
    if not done.wait(180):
        raise TimeoutError("窗口没有响应")
    if "error" in box:
        raise box["error"]
    return box.get("value")


_cocoa_caller = None


def _run_on_cocoa_main(fn):
    try:
        from Foundation import NSObject
        import objc
    except Exception:
        return fn()
    global _cocoa_caller
    if _cocoa_caller is None:

        class Caller(NSObject):
            def initWithCallback_(self, callback):
                self = objc.super(Caller, self).init()
                if self is None:
                    return None
                self.callback = callback
                return self

            def call_(self, _sender) -> None:
                callback = self.callback
                self.callback = None
                callback()

        _cocoa_caller = Caller
    box: dict[str, object] = {}
    done = threading.Event()

    def run() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:
            box["error"] = exc
        finally:
            done.set()

    caller = _cocoa_caller.alloc().initWithCallback_(run)
    caller.performSelectorOnMainThread_withObject_waitUntilDone_("call:", None, False)
    if not done.wait(180):
        raise TimeoutError("窗口没有响应")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _mac_double_click_time() -> int:
    try:
        result = subprocess.run(
            ["defaults", "read", "-g", "com.apple.mouse.doubleClickThreshold"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        seconds = float((result.stdout or "").strip())
        millis = int(seconds * 1000)
        if 100 <= millis <= 2000:
            return millis
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return 500


def _linux_double_click_time() -> int:
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.peripherals.mouse", "double-click"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        value = int((result.stdout or "").strip())
        if 100 <= value <= 2000:
            return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return 400
