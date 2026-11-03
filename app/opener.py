from __future__ import annotations

import logging
import os
import posixpath
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app import notices, shell_open, transfer
from app.pool import ConnectionDropped, NotConnected, pool
from app.remote_path import parent_path


log = logging.getLogger("remote-folder")
_lock = threading.Lock()
_watches: dict[tuple[str, str], "Watch"] = {}
_thread: threading.Thread | None = None
_stop = threading.Event()


@dataclass
class Watch:
    session_id: str
    remote: str
    local: Path
    mtime_ns: int
    size: int
    fail_count: int = 0
    retry_after: float = 0


def open_cached(session_id: str, path: str, choose: bool) -> dict:
    remote = pool.call(session_id, lambda live: live.sftp.normalize(path))
    if _is_dirty(session_id, remote):
        _push_remote(session_id, remote, stable_seconds=0)
    if _is_dirty(session_id, remote):
        local = _local_of(session_id, remote)
        refreshed = False
    else:
        remote, local, refreshed = pool.call(
            session_id,
            lambda live: transfer.cache_file(live.sftp, session_id, remote),
        )
    _track(session_id, remote, local, refreshed)
    if choose:
        cancelled = shell_open.choose_application(local)
        if cancelled:
            return {"ok": True, "cancelled": True, "path": str(local), "message": ""}
        return {
            "ok": True,
            "path": str(local),
            "message": "已用所选程序打开。如果勾选了「始终使用此应用打开」，以后双击这个后缀都会用它。保存后会写回远程。",
        }
    shell_open.open_default(local)
    return {
        "ok": True,
        "path": str(local),
        "message": "已按这个后缀的默认程序打开。保存后会写回远程。",
    }


def flush_session(session_id: str) -> None:
    with _lock:
        remotes = [remote for sid, remote in _watches if sid == session_id]
    for remote in remotes:
        _push_remote(session_id, remote, stable_seconds=0)


def drop_session(session_id: str) -> None:
    with _lock:
        keys = [key for key in _watches if key[0] == session_id]
        for key in keys:
            del _watches[key]


def push_pending(stable_seconds: float = 0) -> int:
    with _lock:
        keys = list(_watches)
    count = 0
    for session_id, remote in keys:
        if _push_remote(session_id, remote, stable_seconds):
            count += 1
    return count


def _track(session_id: str, remote: str, local: Path, refreshed: bool) -> None:
    stat = local.stat()
    key = (session_id, remote)
    with _lock:
        watch = _watches.get(key)
        if watch is None:
            _watches[key] = Watch(session_id, remote, local, stat.st_mtime_ns, stat.st_size)
        elif refreshed:
            watch.local = local
            watch.remote = remote
            watch.mtime_ns = stat.st_mtime_ns
            watch.size = stat.st_size
            watch.fail_count = 0
            watch.retry_after = 0
    _ensure_thread()


def _is_dirty(session_id: str, remote: str) -> bool:
    watch = _watches.get((session_id, remote))
    if watch is None or not watch.local.exists():
        return False
    try:
        stat = watch.local.stat()
    except OSError:
        return False
    return stat.st_mtime_ns != watch.mtime_ns or stat.st_size != watch.size


def _local_of(session_id: str, remote: str) -> Path:
    watch = _watches.get((session_id, remote))
    if watch is None or not watch.local.is_file():
        raise ValueError("本机副本正被占用，请先关闭已打开的程序再试")
    return watch.local


def _push_remote(session_id: str, remote: str, stable_seconds: float) -> bool:
    watch = _watches.get((session_id, remote))
    if watch is None:
        return False
    if time.time() < watch.retry_after:
        return False
    try:
        stat = watch.local.stat()
    except OSError:
        return False
    if stat.st_mtime_ns == watch.mtime_ns and stat.st_size == watch.size:
        return False
    if stable_seconds > 0 and not _stable(watch.local, stable_seconds):
        return False
    try:
        pool.call(session_id, lambda live, item=watch: live.sftp.put(str(item.local), item.remote))
    except NotConnected:
        _remember_failure(watch, "连接已断开，保存还没有写回远程")
        watch.retry_after = time.time() + 5
        return False
    except ConnectionDropped as exc:
        _remember_failure(watch, f"写回远程失败：{_name(watch)}（{exc}）")
        watch.retry_after = time.time() + 5
        return False
    except OSError as exc:
        _remember_failure(watch, f"写回远程失败：{_name(watch)}（{exc}）")
        return False
    try:
        after = watch.local.stat()
    except OSError:
        after = stat
    with _lock:
        current = _watches.get((session_id, remote))
        if current is watch:
            current.mtime_ns = after.st_mtime_ns
            current.size = after.st_size
            current.fail_count = 0
            current.retry_after = 0
    notices.add(f"已写回远程：{_name(watch)}")
    return True


def _remember_failure(watch: Watch, message: str) -> None:
    with _lock:
        watch.fail_count += 1
        count = watch.fail_count
    if count == 1 or count % 30 == 0:
        notices.add(message, level="error")
        log.warning(message)


def _stable(path: Path, seconds: float) -> bool:
    try:
        first = path.stat()
    except OSError:
        return False
    time.sleep(seconds)
    try:
        second = path.stat()
    except OSError:
        return False
    return first.st_mtime_ns == second.st_mtime_ns and first.st_size == second.st_size


def _ensure_thread() -> None:
    global _thread
    if os.environ.get("RF_SYNC_DISABLE"):
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="remote-folder-sync", daemon=True)
        _thread.start()


def _loop() -> None:
    while not _stop.wait(1.0):
        try:
            push_pending(stable_seconds=1.0)
        except Exception:
            log.exception("写回远程时出错")


def _name(watch: Watch) -> str:
    return posixpath.basename(watch.remote.rstrip("/")) or parent_path(watch.remote)
