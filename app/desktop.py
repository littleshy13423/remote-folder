from __future__ import annotations

from app import downloads


class DesktopBridge:
    def __init__(self) -> None:
        self._window = None

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
        if holder.get("error"):
            return {"ok": False, "error": str(holder["error"])}
        result = holder.get("result")
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else str(result)
        if not path:
            return {"ok": False, "cancelled": True}
        return {"ok": True, "token": downloads.issue(path), "path": path}
