from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path

from app.home import get_home

_guard = threading.Lock()
_pending: dict[str, str] = {}
_LIMIT = 50


def issue(path: str) -> str:
    token = secrets.token_hex(16)
    with _guard:
        _pending[token] = path
    return token


def take(token: str) -> str:
    with _guard:
        path = _pending.pop((token or "").strip(), "")
    if not path:
        raise ValueError("保存位置已失效，请重新选择")
    return path


def remember(name: str, path: str, size: int) -> None:
    item = {
        "name": name,
        "path": path,
        "bytes": int(size),
        "savedAt": datetime.now().isoformat(timespec="seconds"),
    }
    with _guard:
        items = _read()
        items.insert(0, item)
        del items[_LIMIT:]
        _write(items)


def list_items() -> list[dict]:
    with _guard:
        return _read()


def forget(path: str, saved_at: str = "") -> None:
    target = path or ""
    when = saved_at or ""
    with _guard:
        items = _read()
        kept = []
        match = None
        for item in items:
            same_path = item.get("path") == target
            same_time = not when or item.get("savedAt") == when
            if match is None and same_path and same_time:
                match = item
                continue
            kept.append(item)
        if match is None:
            raise ValueError("这条记录已经不在了")
        stored = match.get("path") or ""
        if os.path.lexists(stored):
            if os.path.isdir(stored) and not os.path.islink(stored):
                raise ValueError("这条记录指向一个文件夹，没有删除")
            try:
                os.remove(stored)
            except OSError as exc:
                detail = exc.strerror or str(exc)
                raise ValueError(f"文件没能删掉：{detail}") from exc
        _write(kept)


def _file() -> Path:
    return get_home() / "downloads.json"


def _read() -> list[dict]:
    path = _file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and isinstance(item.get("path"), str)]


def _write(items: list[dict]) -> None:
    _file().write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def downloads_dir() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
                if isinstance(value, str) and value:
                    return value
        except OSError:
            pass
    return str(Path.home() / "Downloads")
