from __future__ import annotations

import errno
import os
import re
import stat
import subprocess
from pathlib import Path

import paramiko

from app.home import get_home
from app.remote_path import remote_join

_BLOB = re.compile(r"[A-Za-z0-9+/]+=*")


def ensure_key() -> tuple[Path, str]:
    private = get_home() / "keys" / "id_ed25519"
    private.parent.mkdir(parents=True, exist_ok=True)
    if private.is_file():
        try:
            key = paramiko.Ed25519Key.from_private_key_file(str(private))
        except paramiko.PasswordRequiredException as exc:
            raise ValueError("本机专用密钥设了口令。一键配置要用没有口令的密钥。") from exc
        except (OSError, paramiko.SSHException) as exc:
            raise ValueError("本机专用密钥无法读取。") from exc
    else:
        key = _generate(private)
    line = f"{key.get_name()} {key.get_base64()} remote-folder"
    _blob(line)
    public = private.with_suffix(".pub")
    public.write_text(line + "\n", encoding="ascii")
    return private, line


def install_public_key(sftp, home: str, line: str) -> bool:
    blob = _blob(line)
    ssh_dir = remote_join(home or "/", ".ssh")
    _ensure_dir(sftp, ssh_dir)
    target = remote_join(ssh_dir, "authorized_keys")
    try:
        with sftp.open(target, "r") as handle:
            current = handle.read()
    except OSError as exc:
        if not _missing(exc):
            raise
        current = b""
    text = current.decode("utf-8", errors="replace")
    if _contains(text, blob):
        _chmod(sftp, target, 0o600)
        return False
    payload = (line.strip() + "\n").encode("utf-8")
    if text:
        extra = b"" if text.endswith("\n") else b"\n"
        with sftp.open(target, "a") as handle:
            handle.write(extra + payload)
    else:
        with sftp.open(target, "w") as handle:
            handle.write(payload)
    _chmod(sftp, target, 0o600)
    return True


def _generate(private: Path) -> paramiko.Ed25519Key:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = Ed25519PrivateKey.generate()
    pem = raw.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private.write_bytes(pem)
    _lock_down(private)
    return paramiko.Ed25519Key.from_private_key_file(str(private))


def _blob(line: str) -> str:
    if any(char in line for char in "\r\n"):
        raise ValueError("公钥无效")
    parts = line.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519" or not _BLOB.fullmatch(parts[1]):
        raise ValueError("公钥无效")
    return parts[1]


def _contains(text: str, blob: str) -> bool:
    for row in text.splitlines():
        parts = row.split()
        if len(parts) >= 2 and parts[1] == blob:
            return True
    return False


def _ensure_dir(sftp, path: str) -> None:
    try:
        attr = sftp.stat(path)
    except OSError as exc:
        if not _missing(exc):
            raise
        sftp.mkdir(path)
    else:
        if not stat.S_ISDIR(attr.st_mode or 0):
            raise ValueError("远程主目录里的 .ssh 不是文件夹")
    _chmod(sftp, path, 0o700)


def _chmod(sftp, path: str, mode: int) -> None:
    try:
        sftp.chmod(path, mode)
    except OSError:
        pass


def _missing(exc: OSError) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    if exc.errno in (errno.ENOENT,):
        return True
    text = str(exc).lower()
    return "no such file" in text or "not found" in text


def _lock_down(path: Path) -> None:
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
            return
        user = os.environ.get("USERNAME") or ""
        if not user:
            return
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W)"],
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        pass
