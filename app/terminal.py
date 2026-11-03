from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from app.home import get_home
from app.pool import pool


def open_bash(session_id: str, session: dict, path: str) -> dict:
    remote = pool.call(session_id, lambda live: _directory(live, path))
    argv = command_args(session, remote)
    record = os.environ.get("RF_TERMINAL_DRY_RUN")
    if record:
        Path(record).write_text(json.dumps({"argv": argv, "path": remote}, ensure_ascii=False), encoding="utf-8")
    else:
        subprocess.Popen(argv, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return {"ok": True, "path": remote, "message": _message(session)}


def command_args(session: dict, path: str) -> list[str]:
    host = str(session.get("host") or "").strip()
    username = str(session.get("username") or "").strip()
    if not host or not username or any(_bad_token(item) for item in (host, username)):
        raise ValueError("主机名或用户名无法用于终端")
    try:
        port = int(session.get("port") or 22)
    except (TypeError, ValueError):
        raise ValueError("端口无效")
    if port < 1 or port > 65535:
        raise ValueError("端口无效")
    auth = session.get("auth") or "password"
    argv = [
        str(_ssh_executable()),
        "-t",
        "-p",
        str(port),
        "-o",
        _option("UserKnownHostsFile", str(get_home() / "known_hosts")),
        "-o",
        "GlobalKnownHostsFile=NUL",
    ]
    if auth == "password":
        argv.extend(["-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive"])
    elif auth == "key":
        key_path = str(session.get("keyPath") or "")
        if not key_path or not Path(key_path).is_file():
            raise ValueError("找不到这个连接的私钥")
        argv.extend(["-i", key_path, "-o", "IdentitiesOnly=yes"])
    argv.extend([f"{username}@{host}", remote_command(path)])
    return argv


def remote_command(path: str) -> str:
    notice = shell_quote("无法进入这个目录，留在登录目录。")
    return f"cd {shell_quote(path)} || printf '%s\\n' {notice}; exec bash"


def shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _directory(live, path: str) -> str:
    remote = live.sftp.normalize(path or live.home or ".")
    attr = live.sftp.stat(remote)
    if not stat.S_ISDIR(attr.st_mode or 0):
        raise ValueError("当前位置不是文件夹")
    return remote


def _message(session: dict) -> str:
    auth = session.get("auth") or "password"
    if auth == "password":
        return "已打开 bash，并进入当前目录。终端里会再问一次密码。"
    if auth == "key":
        return "已打开 bash，并进入当前目录。如果私钥有口令，在终端里输入一次。"
    return "已打开 bash，并进入当前目录。"


def _ssh_executable() -> Path:
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
    if system.is_file():
        return system
    found = shutil.which("ssh")
    if found:
        return Path(found)
    raise OSError("找不到系统里的 ssh。请先安装 OpenSSH 客户端。")


def _option(name: str, value: str) -> str:
    if any(character.isspace() for character in value):
        escaped = value.replace('"', '\\"')
        return f'{name}="{escaped}"'
    return f"{name}={value}"


def _bad_token(value: str) -> bool:
    return value.startswith("-") or any(character.isspace() or ord(character) < 32 for character in value)
