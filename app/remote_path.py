from __future__ import annotations

import posixpath
import stat


def remote_join(parent: str, name: str) -> str:
    parent = parent.replace("\\", "/")
    name = name.lstrip("/")
    if parent in ("", ".", "/"):
        return "/" + name
    return parent.rstrip("/") + "/" + name


def safe_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or cleaned in (".", "..") or "/" in cleaned or "\\" in cleaned:
        raise ValueError("名称不能包含斜杠，也不能是 . 或 ..")
    if len(cleaned) > 255:
        raise ValueError("名称过长")
    return cleaned


def safe_relative(relative: str) -> str:
    text = relative.replace("\\", "/").strip()
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("上传路径不能包含 ..")
        parts.append(part)
    if not parts:
        raise ValueError("上传路径无效")
    return "/".join(parts)


def parent_path(path: str) -> str:
    if path in ("", "/"):
        return "/"
    parent = posixpath.dirname(path.rstrip("/"))
    return parent or "/"


def mode_string(mode: int | None) -> str:
    if not mode:
        return ""
    if stat.S_ISLNK(mode):
        kind = "l"
    elif stat.S_ISDIR(mode):
        kind = "d"
    else:
        kind = "-"
    flags = ["---", "--x", "-w-", "-wx", "r--", "r-x", "rw-", "rwx"]
    return kind + "".join(flags[(mode >> shift) & 7] for shift in (6, 3, 0))


def is_directory(mode: int | None, follow_dir: bool = False) -> bool:
    value = mode or 0
    if stat.S_ISLNK(value) and not follow_dir:
        return False
    return stat.S_ISDIR(value)
