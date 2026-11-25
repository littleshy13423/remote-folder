from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
from pathlib import Path

from app.home import get_home
from app.pool import pool
from app import store


def open_bash(session_id: str, session: dict, path: str) -> dict:
    remote = pool.call(session_id, lambda live: _directory(live, path))
    password = _terminal_password(session_id, session)
    argv = command_args(session, remote, auto_password=password is not None)
    record = os.environ.get("RF_TERMINAL_DRY_RUN")
    if record:
        Path(record).write_text(
            json.dumps({"argv": argv, "path": remote, "askpass": password is not None}, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"ok": True, "path": remote, "message": _message(session, password is not None)}
    env = None
    askpass_dir = None
    if password is not None:
        askpass_dir, program = _write_askpass()
        env = os.environ.copy()
        env["SSH_ASKPASS"] = str(program)
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env.setdefault("DISPLAY", "localhost:0")
        env["RF_SSH_PASSWORD"] = password
    try:
        process = subprocess.Popen(
            argv,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except Exception:
        if askpass_dir is not None:
            shutil.rmtree(askpass_dir, ignore_errors=True)
        raise
    if askpass_dir is not None:
        threading.Thread(target=_remove_askpass, args=(process, askpass_dir), daemon=True).start()
    return {"ok": True, "path": remote, "message": _message(session, password is not None)}


def command_args(session: dict, path: str, auto_password: bool = False) -> list[str]:
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
        if auto_password:
            argv.extend(["-o", "StrictHostKeyChecking=accept-new", "-o", "NumberOfPasswordPrompts=1"])
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


def _terminal_password(session_id: str, session: dict) -> str | None:
    if (session.get("auth") or "password") != "password":
        return None
    secret = pool.login_password(session_id) or store.get_secret(session_id, "password")
    if not secret or any(character in secret for character in "\r\n\0"):
        return None
    return secret


def _write_askpass() -> tuple[Path, Path]:
    directory = Path(tempfile.mkdtemp(prefix="rf-askpass-"))
    if os.name == "nt":
        program = directory / "askpass.cmd"
        program.write_text(
            "@echo off\r\n"
            "powershell.exe -NoProfile -NonInteractive -Command "
            "\"$b=[Text.Encoding]::UTF8.GetBytes($env:RF_SSH_PASSWORD+[char]10);"
            "[Console]::OpenStandardOutput().Write($b,0,$b.Length)\"\r\n",
            encoding="ascii",
        )
    else:
        program = directory / "askpass.sh"
        program.write_text("#!/bin/sh\nprintf '%s\\n' \"$RF_SSH_PASSWORD\"\n", encoding="utf-8")
        program.chmod(0o700)
    return directory, program


def _remove_askpass(process: subprocess.Popen, directory: Path) -> None:
    try:
        process.wait()
    except Exception:
        pass
    shutil.rmtree(directory, ignore_errors=True)


def _message(session: dict, auto_password: bool = False) -> str:
    auth = session.get("auth") or "password"
    if auth == "password" and auto_password:
        return "已打开 bash，并进入当前目录。"
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
