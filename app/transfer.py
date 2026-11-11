from __future__ import annotations

import errno
import os
import posixpath
import shutil
import stat
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import paramiko

from app.home import get_home
from app.remote_path import is_directory, mode_string, parent_path, remote_join, safe_name, safe_relative


MAX_UPLOAD_ITEMS = 5000
LARGE_OPEN_BYTES = 32 * 1024 * 1024
# Paramiko 默认把整个文件的读请求一次发出去。OpenSSH 大约只同时处理 64 个，超出后不再回数据，下载会一直停住。
_READ_AHEAD = 16
_activity_guard = threading.Lock()
_activity: dict[str, dict] = {}


def owner_and_group(attr) -> tuple[str, str]:
    longname = getattr(attr, "longname", None) or ""
    parts = longname.split()
    if len(parts) >= 4 and parts[0][:1] in "dlcbps-" and parts[1].isdigit():
        return parts[2], parts[3]
    uid = getattr(attr, "st_uid", None)
    gid = getattr(attr, "st_gid", None)
    return ("" if uid is None else str(uid), "" if gid is None else str(gid))


def list_directory(sftp, path: str) -> dict:
    current = sftp.normalize(path)
    entries = []
    for attr in sftp.listdir_attr(current):
        name = attr.filename
        if name in (".", ".."):
            continue
        mode = attr.st_mode or 0
        link = stat.S_ISLNK(mode)
        directory = stat.S_ISDIR(mode)
        if link:
            try:
                followed = sftp.stat(remote_join(current, name))
                directory = stat.S_ISDIR(followed.st_mode or 0)
            except OSError:
                directory = False
        owner, group = owner_and_group(attr)
        entries.append(
            {
                "name": name,
                "path": remote_join(current, name),
                "isDir": directory,
                "isLink": link,
                "size": None if directory else int(attr.st_size or 0),
                "mtime": int(attr.st_mtime or 0),
                "mode": mode_string(mode),
                "owner": owner,
                "group": group,
            }
        )
    entries.sort(key=lambda item: (not item["isDir"], item["name"].lower()))
    return {"path": current, "parent": parent_path(current), "entries": entries}


def make_directory(sftp, parent: str, name: str) -> dict:
    folder = safe_name(name)
    current = sftp.normalize(parent)
    path = remote_join(current, folder)
    _reject_existing(sftp, path, folder)
    sftp.mkdir(path)
    return {"ok": True, "path": path}


def make_file(sftp, parent: str, name: str) -> dict:
    filename = safe_name(name)
    current = sftp.normalize(parent)
    path = remote_join(current, filename)
    _reject_existing(sftp, path, filename)
    with sftp.file(path, "wb"):
        pass
    return {"ok": True, "path": path}


def _reject_existing(sftp, path: str, name: str) -> None:
    try:
        sftp.lstat(path)
    except OSError:
        return
    raise ValueError(f"已经有同名项目：{name}")


def rename_path(sftp, path: str, new_name: str) -> dict:
    name = safe_name(new_name)
    current = sftp.normalize(path)
    if current == "/":
        raise ValueError("不能重命名根目录")
    destination = remote_join(parent_path(current), name)
    try:
        sftp.posix_rename(current, destination)
    except OSError:
        sftp.rename(current, destination)
    return {"ok": True, "path": destination}


class _RmUnavailable(Exception):
    pass


def delete_paths(sftp, paths: list[str], client=None, as_root: bool = False, sudo_password: str | None = None) -> dict:
    if not paths:
        raise ValueError("请选择要删除的项目")
    targets = [_delete_target(sftp, path) for path in paths]
    if client is not None:
        try:
            for batch in _batches(targets, 200):
                _rm(client, batch, as_root, sudo_password)
            return {"ok": True}
        except _RmUnavailable:
            pass
    for current in targets:
        _remove(sftp, current)
    return {"ok": True}


def remove_command(paths: list[str], as_root: bool = False, sudo_password: str | None = None) -> str:
    quoted = " ".join(_shell_quote(path) for path in paths)
    command = f"rm -rf -- {quoted}"
    if not as_root:
        return command
    if sudo_password:
        return f"sudo -S -p RFSUDO -- {command}"
    return f"sudo -n -- {command}"


def _delete_target(sftp, path: str) -> str:
    raw = (path or "").replace("\\", "/")
    if "\x00" in raw:
        raise ValueError("路径无效")
    if not raw.startswith("/"):
        raw = sftp.normalize(raw or ".")
    parent = sftp.normalize(parent_path(raw))
    name = posixpath.basename(raw.rstrip("/"))
    if not name or name in (".", ".."):
        raise ValueError("不能删除根目录")
    current = remote_join(parent, name)
    if current == "/":
        raise ValueError("不能删除根目录")
    try:
        sftp.lstat(current)
    except FileNotFoundError:
        raise
    except OSError as exc:
        text = str(exc).lower()
        if getattr(exc, "errno", None) == errno.ENOENT or "no such file" in text:
            raise FileNotFoundError(current) from exc
        raise
    return current


def _batches(items: list[str], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _rm(client, paths: list[str], as_root: bool, sudo_password: str | None) -> None:
    command = remove_command(paths, as_root, sudo_password)
    try:
        stdin, stdout, stderr = client.exec_command(command)
    except (paramiko.SSHException, EOFError, OSError) as exc:
        raise _RmUnavailable() from exc
    try:
        if as_root and sudo_password and command.startswith("sudo -S "):
            stdin.write(sudo_password + "\n")
            stdin.flush()
        try:
            stdin.channel.shutdown_write()
        except Exception:
            pass
        errors: list[bytes] = []
        reader = threading.Thread(target=_read_all, args=(stderr, errors), daemon=True)
        reader.start()
        code = stdout.channel.recv_exit_status()
        reader.join(timeout=5)
    except (paramiko.SSHException, EOFError, OSError) as exc:
        raise _RmUnavailable() from exc
    detail = b"".join(errors).decode("utf-8", errors="replace").strip()
    lowered = detail.lower()
    if code == 127 or "command not found" in lowered:
        raise _RmUnavailable()
    if code not in (0, None):
        message = _rm_message(detail, code)
        if "permission denied" in lowered or "operation not permitted" in lowered or "not permitted" in lowered:
            raise PermissionError(message)
        if "no such file" in lowered:
            raise FileNotFoundError(message)
        raise OSError(message)


def _read_all(stream, sink: list[bytes]) -> None:
    try:
        sink.append(stream.read())
    except Exception:
        return


def _rm_message(detail: str, code: int) -> str:
    lines = [line for line in detail.splitlines() if line.strip() and "RFSUDO" not in line and "[sudo]" not in line.lower()]
    text = "\n".join(lines).strip()
    return text or f"远程删除失败（{code}）"


def _remove(sftp, path: str) -> None:
    attr = sftp.lstat(path)
    if is_directory(attr.st_mode, follow_dir=False) and stat.S_ISDIR(attr.st_mode or 0):
        for child in sftp.listdir_attr(path):
            if child.filename in (".", ".."):
                continue
            _remove(sftp, remote_join(path, child.filename))
        sftp.rmdir(path)
        return
    sftp.remove(path)


class _TarUnavailable(Exception):
    pass


def upload_tar_command(dest: str, as_root: bool = False, sudo_password: str | None = None) -> str:
    return _as_root(f"tar -xf - -C {_shell_quote(dest)}", as_root, sudo_password)


def download_tar_command(pairs: list[tuple[str, str]], as_root: bool = False, sudo_password: str | None = None) -> str:
    parts = ["tar", "-cf", "-"]
    for parent, name in pairs:
        parts.extend(("-C", _shell_quote(parent), _shell_quote(name)))
    return _as_root(" ".join(parts), as_root, sudo_password)


def _as_root(command: str, as_root: bool, sudo_password: str | None) -> str:
    if not as_root:
        return command
    if sudo_password:
        return f"sudo -S -p RFSUDO -- {command}"
    return f"sudo -n -- {command}"


def _open_tar_channel(client, command: str):
    try:
        stdin, stdout, stderr = client.exec_command(command)
    except (paramiko.SSHException, EOFError, OSError) as exc:
        raise _TarUnavailable() from exc
    channel = stdin.channel
    try:
        channel.settimeout(None)
    except Exception:
        pass
    return stdin, stdout, stderr, channel


def _drain_remote(*streams):
    sinks: list[list] = []
    threads = []
    for stream in streams:
        sink: list = []
        sinks.append(sink)
        thread = threading.Thread(target=_read_all, args=(stream, sink), daemon=True)
        thread.start()
        threads.append(thread)
    return sinks, threads


def _sink_text(sinks: list[list]) -> str:
    parts = []
    for sink in sinks:
        for chunk in sink:
            parts.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace"))
    return "".join(parts).strip()


def _close_stream(stream) -> None:
    try:
        stream.close()
    except Exception:
        pass


def _finish_tar(channel, sinks, threads, allow_fallback: bool) -> None:
    try:
        code = channel.recv_exit_status()
    except (paramiko.SSHException, EOFError, OSError) as exc:
        if allow_fallback:
            raise _TarUnavailable() from exc
        raise OSError(f"远程打包传输中断：{exc}") from exc
    for thread in threads:
        thread.join(timeout=5)
    detail = _sink_text(sinks)
    lowered = detail.lower()
    missing = code == 127 or "command not found" in lowered or "tar: not found" in lowered
    if missing or (allow_fallback and code not in (0, None)):
        raise _TarUnavailable()
    if code not in (0, None):
        message = _tar_failure(detail, code)
        if "permission denied" in lowered or "operation not permitted" in lowered:
            raise PermissionError(message)
        raise OSError(message)


def _tar_failure(detail: str, code: int) -> str:
    lines = [line for line in detail.splitlines() if line.strip() and "RFSUDO" not in line and "[sudo]" not in line.lower()]
    return "\n".join(lines).strip() or f"远程打包传输失败（{code}）"


def _write_sudo_password(stdin, command: str, as_root: bool, sudo_password: str | None) -> None:
    if as_root and sudo_password and command.startswith("sudo -S "):
        stdin.write(sudo_password + "\n")
        stdin.flush()


def _iter_upload_tar(client, dest: str, sized, as_root: bool, sudo_password: str | None, directory_count: int):
    total = sum(item[2] for item in sized)
    count = len(sized) + directory_count
    command = upload_tar_command(dest, as_root, sudo_password)
    stdin, stdout, stderr, channel = _open_tar_channel(client, command)
    sinks, threads = _drain_remote(stdout, stderr)
    done = 0
    last_name = ""
    try:
        _write_sudo_password(stdin, command, as_root, sudo_password)
        if channel.exit_status_ready():
            _finish_tar(channel, sinks, threads, allow_fallback=True)
            raise _TarUnavailable()
        yield {
            "phase": "write",
            "done": 0,
            "total": total,
            "index": directory_count,
            "count": count,
            "name": "",
            "message": "正在打包上传…",
        }
        last_percent = -1
        with tarfile.open(fileobj=stdin, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            for index, (source, relative, size) in enumerate(sized, start=directory_count + 1):
                source.seek(0)
                info = tarfile.TarInfo(name=relative)
                info.size = size
                info.mtime = int(time.time())
                archive.addfile(info, source)
                done += size
                last_name = relative
                percent = (done * 100 // total) if total else (index * 100 // count)
                if index == count or index % 20 == 0 or percent != last_percent:
                    last_percent = percent
                    yield {
                        "phase": "write",
                        "done": done,
                        "total": total,
                        "index": index,
                        "count": count,
                        "name": relative,
                        "message": f"正在上传 {relative}",
                    }
    except _TarUnavailable:
        raise
    except (paramiko.SSHException, EOFError, BrokenPipeError, OSError) as exc:
        if done == 0:
            raise _TarUnavailable() from exc
        raise OSError(f"远程打包传输中断：{exc}") from exc
    finally:
        _close_stream(stdin)
    _finish_tar(channel, sinks, threads, allow_fallback=done == 0)
    yield {
        "phase": "write",
        "done": total,
        "total": total,
        "index": count,
        "count": count,
        "name": last_name,
        "message": f"正在上传 {last_name}" if last_name else "正在打包上传…",
        "ok": True,
    }


def iter_upload(
    sftp, dest: str, files, relatives: list[str], client=None,
    as_root: bool = False, sudo_password: str | None = None, *, directories: list[str] | None = None,
):
    if len(files) != len(relatives):
        raise ValueError("上传文件和路径数量不一致")
    directories = directories or []
    if not files and not directories:
        raise ValueError("没有选择文件或目录")
    count = len(files) + len(directories)
    if count > MAX_UPLOAD_ITEMS:
        raise ValueError(f"一次最多上传 {MAX_UPLOAD_ITEMS} 个项目")
    directories = [safe_relative(path) for path in directories]
    current = sftp.normalize(dest)
    pending = list(zip(files, relatives))
    count_hint = len(pending)
    sized = []
    total = 0
    for index, (upload_file, relative) in enumerate(pending, start=1):
        if index == 1 or index == count_hint or index % 20 == 0:
            yield {"phase": "prepare", "message": f"正在统计要上传的文件 {index}/{count_hint}…"}
        source = upload_file.file
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(0)
        sized.append((source, safe_relative(relative), size))
        total += size
    directory_count = len(directories)
    if directories:
        file_paths = {relative for _, relative, _ in sized}
        for directory in directories:
            parent = directory
            while parent:
                if parent in file_paths:
                    raise ValueError(f"上传的文件与目录重名：{parent}")
                parent = posixpath.dirname(parent)
        for index, directory in enumerate(directories, start=1):
            _ensure_dir(sftp, remote_join(current, directory))
            if index == 1 or index == directory_count or index % 20 == 0:
                yield {"phase": "write", "index": index, "count": count, "done": 0, "total": total,
                       "name": directory, "message": f"正在创建目录 {directory}"}
    if client is not None and len(sized) >= 2:
        try:
            yield from _iter_upload_tar(client, current, sized, as_root, sudo_password, directory_count)
            return
        except _TarUnavailable:
            for source, _relative, _size in sized:
                try:
                    source.seek(0)
                except Exception:
                    pass
    done = 0
    last_percent = -1
    last_index = -1

    def emit(index: int, name: str, final: bool = False):
        nonlocal last_percent, last_index
        if total:
            percent = 100 if final else done * 100 // total
        else:
            percent = 100 if final else index * 100 // count
        if not final and percent == last_percent and index == last_index:
            return None
        last_percent = percent
        last_index = index
        payload = {"phase": "write", "done": done, "total": total, "index": index, "count": count, "name": name}
        if name:
            payload["message"] = f"正在上传 {name}"
        if final:
            payload["ok"] = True
            payload["count"] = count
        return payload

    if not directories:
        yield emit(0, "")
    for index, (source, relative, _size) in enumerate(sized, start=directory_count + 1):
        started = emit(index, relative)
        if started:
            yield started
        remote = remote_join(current, relative)
        _ensure_dir(sftp, posixpath.dirname(remote))
        source.seek(0)
        with sftp.file(remote, "wb") as remote_file:
            while True:
                chunk = source.read(256 * 1024)
                if not chunk:
                    break
                remote_file.write(chunk)
                done += len(chunk)
                event = emit(index, relative)
                if event:
                    yield event
        event = emit(index, relative)
        if event:
            yield event
    finished = emit(count, sized[-1][1] if sized else directories[-1], True)
    if finished:
        yield finished


def _ensure_dir(sftp, path: str) -> None:
    path = posixpath.normpath(path)
    if path in ("", ".", "/"):
        return
    try:
        attr = sftp.stat(path)
    except OSError:
        _ensure_dir(sftp, posixpath.dirname(path))
        try:
            sftp.mkdir(path)
            return
        except OSError as exc:
            # Another upload may have created this directory since our stat.
            try:
                attr = sftp.stat(path)
            except OSError:
                raise exc
    if not stat.S_ISDIR(attr.st_mode or 0):
        raise ValueError(f"无法创建目录，因为已有同名文件：{path}")


def note_activity(session_id: str, done: int, total: int, name: str, message: str = "") -> None:
    with _activity_guard:
        _activity[session_id] = {"done": int(done), "total": int(total), "name": name, "message": message}


def read_activity(session_id: str) -> dict | None:
    with _activity_guard:
        item = _activity.get(session_id)
        return dict(item) if item else None


def clear_activity(session_id: str) -> None:
    with _activity_guard:
        _activity.pop(session_id, None)


def _should_tar_download(sftp, paths: list[str]) -> bool:
    if any(path == "/" for path in paths):
        return False
    if len(paths) == 1 and not _is_real_dir(sftp, paths[0]):
        return False
    return True


def _tar_pairs(paths: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for path in paths:
        name = posixpath.basename(path.rstrip("/"))
        if not name or name in (".", ".."):
            raise _TarUnavailable()
        pairs.append((parent_path(path), name))
    return pairs


def _tar_member_name(name: str) -> str:
    parts = []
    for part in name.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("远程打包包含无效路径")
        parts.append(part)
    return "/".join(parts)


def _remote_from_member(pairs: list[tuple[str, str]], arcname: str) -> str | None:
    for parent, name in pairs:
        if arcname == name or arcname.startswith(name + "/"):
            return remote_join(parent, arcname)
    return None


def _download_filename(paths: list[str]) -> str:
    if len(paths) == 1:
        base = posixpath.basename(paths[0].rstrip("/")) or "folder"
        return base + ".zip"
    return "download.zip"


def _copy_into_zip(archive, arc: str, source, progress, state: dict) -> None:
    name = posixpath.basename(arc) or arc
    with archive.open(arc, "w") as target:
        while True:
            chunk = source.read(256 * 1024)
            if not chunk:
                break
            target.write(chunk)
            state["done"] += len(chunk)
            if progress:
                progress(state["done"], 0, name, f"正在下载 {name}（已接收 {_format_bytes(state['done'])}）")
    state["files"] += 1


def _materialize_tar(sftp, paths, progress, client, as_root: bool, sudo_password: str | None):
    pairs = _tar_pairs(paths)
    command = download_tar_command(pairs, as_root, sudo_password)
    if len(command) > 256 * 1024:
        raise _TarUnavailable()
    temporary = tempfile.NamedTemporaryFile(delete=False, prefix="rf-", suffix=".zip")
    temporary.close()
    stdin, stdout, stderr, channel = _open_tar_channel(client, command)
    sinks, threads = _drain_remote(stderr)
    state = {"done": 0, "files": 0}
    try:
        _write_sudo_password(stdin, command, as_root, sudo_password)
        _close_stream(stdin)
        if progress:
            progress(0, 0, "", "正在打包下载…")
        with zipfile.ZipFile(temporary.name, "w", compression=zipfile.ZIP_STORED) as archive:
            with tarfile.open(fileobj=stdout, mode="r|") as source:
                for member in source:
                    arc = _tar_member_name(member.name)
                    if not arc:
                        continue
                    if member.isdir():
                        archive.writestr(arc.rstrip("/") + "/", "")
                        continue
                    if member.isfile():
                        extracted = source.extractfile(member)
                        if extracted is None:
                            continue
                        with extracted:
                            _copy_into_zip(archive, arc, extracted, progress, state)
                        continue
                    remote = _remote_from_member(pairs, arc)
                    if remote is None:
                        continue
                    try:
                        with sftp.open(remote, "rb") as extracted:
                            _copy_into_zip(archive, arc, extracted, progress, state)
                    except OSError:
                        continue
        _finish_tar(channel, sinks, threads, allow_fallback=state["files"] == 0 and state["done"] == 0)
    except _TarUnavailable:
        _unlink(temporary.name)
        raise
    except (tarfile.TarError, paramiko.SSHException, EOFError, OSError) as exc:
        _unlink(temporary.name)
        detail = _sink_text(sinks)
        lowered = detail.lower()
        if state["files"] == 0 or "command not found" in lowered or "tar: not found" in lowered:
            raise _TarUnavailable() from exc
        raise OSError(detail or f"远程打包传输中断：{exc}") from exc
    finally:
        _close_stream(stdin)
        try:
            channel.close()
        except Exception:
            pass
    return temporary.name, _download_filename(paths), "application/zip"


def materialize(sftp, paths: list[str], progress=None, client=None, as_root: bool = False, sudo_password: str | None = None) -> tuple[str, str, str]:
    if not paths:
        raise ValueError("请选择要下载的文件")
    normalized = [sftp.normalize(path) for path in paths]
    if client is not None and _should_tar_download(sftp, normalized):
        try:
            return _materialize_tar(sftp, normalized, progress, client, as_root, sudo_password)
        except _TarUnavailable:
            pass
    seen = {"count": 0}

    def on_seen(name: str) -> None:
        seen["count"] += 1
        if progress and (seen["count"] == 1 or seen["count"] % 20 == 0):
            progress(0, 0, name, f"正在统计大小，已看到 {seen['count']} 个文件")

    if progress:
        progress(0, 0, "", "正在统计要下载的内容…")
    total = sum(_measure(sftp, path, on_seen) for path in normalized)
    done = 0

    def report(name: str, extra: int = 0) -> None:
        if progress:
            progress(done + extra, total, name, f"正在下载 {name}" if name else "正在下载…")

    if len(normalized) == 1 and not _is_real_dir(sftp, normalized[0]):
        temporary = tempfile.NamedTemporaryFile(delete=False, prefix="rf-")
        temporary.close()
        name = posixpath.basename(normalized[0]) or "download"
        try:
            report(name)
            sftp.get(
                normalized[0],
                temporary.name,
                callback=lambda transferred, _total: report(name, transferred),
                max_concurrent_prefetch_requests=_READ_AHEAD,
            )
        except Exception:
            _unlink(temporary.name)
            raise
        return temporary.name, name, "application/octet-stream"

    temporary = tempfile.NamedTemporaryFile(delete=False, prefix="rf-", suffix=".zip")
    temporary.close()
    try:
        with zipfile.ZipFile(temporary.name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in normalized:
                done = _add_zip(sftp, path, "", archive, done, total, progress)
    except Exception:
        _unlink(temporary.name)
        raise
    if len(normalized) == 1:
        base = posixpath.basename(normalized[0].rstrip("/")) or "folder"
        filename = base + ".zip"
    else:
        filename = "download.zip"
    return temporary.name, filename, "application/zip"


def _format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    digits = 0 if index == 0 or value >= 10 else 1
    return f"{value:.{digits}f} {units[index]}"


def _is_real_dir(sftp, path: str) -> bool:
    attr = sftp.lstat(path)
    mode = attr.st_mode or 0
    return stat.S_ISDIR(mode) and not stat.S_ISLNK(mode)


def _measure(sftp, path: str, on_seen=None) -> int:
    if _is_real_dir(sftp, path):
        total = 0
        for child in sftp.listdir_attr(path):
            if child.filename in (".", ".."):
                continue
            total += _measure(sftp, remote_join(path, child.filename), on_seen)
        return total
    try:
        attr = sftp.stat(path)
    except OSError:
        attr = sftp.lstat(path)
    if on_seen:
        on_seen(posixpath.basename(path) or path)
    return int(attr.st_size or 0)


def _add_zip(sftp, path: str, prefix: str, archive: zipfile.ZipFile, done: int, total: int, progress) -> int:
    name = posixpath.basename(path.rstrip("/")) or "root"
    arcname = f"{prefix}/{name}" if prefix else name
    if _is_real_dir(sftp, path):
        children = [item for item in sftp.listdir_attr(path) if item.filename not in (".", "..")]
        if not children:
            archive.writestr(arcname + "/", "")
            return done
        for child in children:
            done = _add_zip(sftp, remote_join(path, child.filename), arcname, archive, done, total, progress)
        return done
    with sftp.open(path, "rb") as source, archive.open(arcname, "w") as target:
        while True:
            chunk = source.read(256 * 1024)
            if not chunk:
                break
            target.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total, name, f"正在下载 {name}")
    return done


def cache_file(sftp, session_id: str, path: str) -> tuple[str, Path, bool]:
    current = sftp.normalize(path)
    if _is_real_dir(sftp, current):
        raise ValueError("这是文件夹，双击即可进入")
    size = int(sftp.stat(current).st_size or 0)
    if size >= LARGE_OPEN_BYTES:
        name = posixpath.basename(current) or current
        raise ValueError(f"「{name}」有 {_format_bytes(size)}。这个文件较大，请使用下载。")
    target = _cache_target(session_id, current)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        sftp.get(current, str(partial), max_concurrent_prefetch_requests=_READ_AHEAD)
    except Exception:
        _unlink(partial)
        raise
    try:
        os.replace(partial, target)
    except OSError:
        _unlink(partial)
        if target.is_file():
            return current, target, False
        raise ValueError("本机副本正被占用，请先关闭已打开的程序再试")
    return current, target, True


_size_cache: dict[tuple[str, str], tuple[float, int, bool]] = {}
_progress: dict[tuple[str, str], dict] = {}
_size_guard = threading.Lock()
_MAX_SIZE_NODES = 4000


def clear_folder_sizes(session_id: str) -> None:
    with _size_guard:
        for key in [item for item in _size_cache if item[0] == session_id]:
            _size_cache.pop(key, None)
        for key in [item for item in _progress if item[0] == session_id]:
            _progress.pop(key, None)


def size_prepare(sftp, session_id: str, path: str) -> dict:
    current = sftp.normalize(path)
    key = (session_id, current)
    now = time.monotonic()
    with _size_guard:
        hit = _size_cache.get(key)
        if hit is not None:
            cached_at, size, partial = hit
            if now - cached_at < (30 if partial else 120):
                return {
                    "ready": True,
                    "result": {"path": current, "size": size, "partial": partial, "limited": True, "counted": True},
                }
        state = _progress.get(key)
        if state is None:
            state = {
                "path": current,
                "stack": [current],
                "seen": set(),
                "total": 0,
                "nodes": 0,
                "blocked": False,
                "capped": False,
                "counted": False,
                "yielded": False,
            }
            _progress[key] = state
            if len(_progress) > 48:
                oldest = next(iter(_progress))
                if oldest != key:
                    _progress.pop(oldest, None)
    return {"ready": False, "state": state}


def size_one(sftp, state: dict, yield_now: bool) -> dict:
    if yield_now:
        state["yielded"] = True
        return state
    state["yielded"] = False
    if not state["stack"] or state.get("capped"):
        return state
    if state["nodes"] >= _MAX_SIZE_NODES:
        state["capped"] = True
        state["stack"] = []
        return state
    folder = state["stack"].pop()
    if folder in state["seen"]:
        return state
    state["seen"].add(folder)
    state["counted"] = True
    try:
        children = sftp.listdir_attr(folder)
    except OSError:
        state["blocked"] = True
        return state
    for child in children:
        if child.filename in (".", ".."):
            continue
        state["nodes"] += 1
        mode = child.st_mode or 0
        if stat.S_ISLNK(mode):
            continue
        if stat.S_ISDIR(mode):
            state["stack"].append(remote_join(folder, child.filename))
        else:
            state["total"] += int(child.st_size or 0)
        if state["nodes"] >= _MAX_SIZE_NODES:
            state["capped"] = True
            state["stack"] = []
            break
    return state


def size_view(state: dict, stale: bool = False) -> dict:
    finished = not state["stack"]
    partial = bool(state["blocked"] or state["capped"] or not finished)
    return {
        "path": state["path"],
        "size": state["total"],
        "partial": partial,
        "limited": finished,
        "counted": bool(state["counted"]),
        "stale": stale,
    }


def size_commit(session_id: str, state: dict) -> dict:
    key = (session_id, state["path"])
    view = size_view(state, stale=False)
    with _size_guard:
        if view["limited"]:
            _progress.pop(key, None)
            _size_cache[key] = (time.monotonic(), state["total"], view["partial"])
        else:
            _progress[key] = state
    return view


def _cache_target(session_id: str, remote_path: str) -> Path:
    parts = []
    for part in remote_path.split("/"):
        if part in ("", ".", ".."):
            continue
        cleaned = part.replace(":", "_").replace("\\", "_")
        parts.append(cleaned)
    if not parts:
        parts = ["download"]
    return get_home() / "cache" / session_id / Path(*parts)


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
