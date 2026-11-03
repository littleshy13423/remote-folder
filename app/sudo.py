from __future__ import annotations

import errno
import socket
import time

import paramiko

SFTP_SERVERS = (
    "/usr/lib/openssh/sftp-server",
    "/usr/lib/ssh/sftp-server",
    "/usr/libexec/openssh/sftp-server",
    "/usr/libexec/sftp-server",
)


def match_sftp_server(path: str) -> str | None:
    text = path.strip()
    if text in SFTP_SERVERS:
        return text
    return None


def permission_denied(exc: BaseException) -> bool:
    if getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM):
        return True
    text = str(exc).lower()
    return "permission denied" in text or "access denied" in text


def explain_sudo_failure(text: str, code: int) -> str:
    lowered = text.lower()
    if "no tty present" in lowered or "a terminal is required" in lowered or "requiretty" in lowered:
        return "这台主机的 sudo 要求在终端里输入密码，当前连接送不进去"
    if "not in the sudoers" in lowered or "not allowed" in lowered or "not permitted" in lowered:
        return "这个账号不能使用 sudo"
    if "sorry" in lowered or "incorrect password" in lowered or "authentication failure" in lowered:
        return "sudo 密码不正确"
    if "password is required" in lowered or "no password was provided" in lowered or "需要密码" in text:
        return "sudo 没有收下这次密码。请再试一次；若这台主机规定必须在终端里输入，这里无法提权"
    if code != 0:
        detail = text.strip()
        return f"sudo 提权失败：{detail}" if detail else "sudo 提权失败"
    return ""


def _needs_password(text: str) -> bool:
    lowered = text.lower()
    return "password is required" in lowered or "no password was provided" in lowered or "需要密码" in text


def find_sftp_server(client: paramiko.SSHClient) -> str:
    script = "for p in " + " ".join(SFTP_SERVERS) + "; do if [ -x \"$p\" ]; then printf '%s' \"$p\"; exit 0; fi; done; exit 1"
    try:
        _stdin, stdout, _stderr = client.exec_command(script, timeout=20)
        found = stdout.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
    except (socket.timeout, TimeoutError, paramiko.SSHException, OSError) as exc:
        raise ConnectionError(f"无法查找 sftp-server：{exc}") from exc
    if code != 0:
        raise ConnectionError("这台主机上没有找到 sftp-server，无法用 sudo 查看")
    path = match_sftp_server(found)
    if path is None:
        raise ConnectionError("这台主机上没有找到可用的 sftp-server")
    return path


class _SudoNeedsPassword(Exception):
    pass


def _send_all(channel: paramiko.Channel, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        sent = channel.send(payload[offset:])
        if sent <= 0:
            raise ConnectionError("无法把 sudo 密码发给远程主机")
        offset += sent


def _read_available(channel: paramiko.Channel, reader) -> str:
    channel.settimeout(0.5)
    try:
        data = reader(8192)
    except (socket.timeout, TimeoutError, EOFError):
        return ""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def _close_channel(channel: paramiko.Channel) -> None:
    try:
        channel.close()
    except Exception:
        pass


def _read_stderr_until(channel: paramiko.Channel, markers: tuple[str, ...], timeout: float) -> str:
    channel.settimeout(0.2)
    chunks: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = "".join(chunks)
        if any(marker in text for marker in markers):
            return text
        try:
            data = channel.recv_stderr(4096)
        except (socket.timeout, TimeoutError):
            continue
        except EOFError:
            break
        if not data:
            break
        chunks.append(data.decode("utf-8", errors="replace"))
    return "".join(chunks)


def _fail_sudo(text: str, fallback: str) -> None:
    message = explain_sudo_failure(text, 1) or fallback
    if message == "sudo 密码不正确":
        raise PermissionError(message)
    raise ConnectionError(message)


def _prompt_seen(text: str) -> bool:
    lowered = text.lower()
    return "rfsudo" in lowered or "[sudo]" in lowered or "password:" in lowered or "password for" in lowered


def sudo_password_command(server: str) -> str:
    if match_sftp_server(server) is None:
        raise ValueError("sftp-server 路径无效")
    return f"sudo -S -p RFSUDO -- /bin/sh -c 'echo RFREADY >&2; exec {server}'"


def _start_sftp(transport: paramiko.Transport, server: str) -> paramiko.SFTPClient:
    channel = transport.open_session(timeout=20)
    failed = True
    try:
        channel.settimeout(20)
        channel.exec_command(f"sudo -n -- {server}")
        client = paramiko.SFTPClient(channel)
        channel.settimeout(None)
        failed = False
        return client
    except (paramiko.SSHException, EOFError, OSError, socket.timeout, TimeoutError) as exc:
        err = _read_available(channel, channel.recv_stderr)
        if not err:
            err = _read_available(channel, channel.recv)
        if _needs_password(err):
            raise _SudoNeedsPassword() from exc
        message = explain_sudo_failure(err, 1)
        raise ConnectionError(message or f"无法启动 sudo 文件服务：{exc}") from exc
    finally:
        if failed:
            _close_channel(channel)


def _start_sftp_with_password(transport: paramiko.Transport, server: str, password: str) -> paramiko.SFTPClient:
    channel = transport.open_session(timeout=20)
    failed = True
    try:
        channel.settimeout(20)
        channel.exec_command(sudo_password_command(server))
        seen = _read_stderr_until(channel, ("RFSUDO", "RFREADY", "[sudo]", "assword"), 15)
        if "RFREADY" not in seen:
            if not _prompt_seen(seen):
                _fail_sudo(seen, "sudo 没有要求输入密码")
            _send_all(channel, (password + "\n").encode("utf-8"))
            ready = _read_stderr_until(channel, ("RFREADY", "Sorry", "sorry", "incorrect"), 15)
            seen += ready
            if "RFREADY" not in seen:
                _fail_sudo(seen, "sudo 没有启动文件服务")
        client = paramiko.SFTPClient(channel)
        channel.settimeout(None)
        failed = False
        return client
    except (paramiko.SSHException, EOFError, socket.timeout, TimeoutError) as exc:
        err = _read_available(channel, channel.recv_stderr)
        message = explain_sudo_failure(err, 1)
        raise ConnectionError(message or f"无法启动 sudo 文件服务：{exc}") from exc
    finally:
        if failed:
            _close_channel(channel)


def open_sftp(client: paramiko.SSHClient, server: str, password: str) -> paramiko.SFTPClient:
    if match_sftp_server(server) is None:
        raise ValueError("sftp-server 路径无效")
    if not password:
        raise ValueError("sudo 密码无效")
    transport = client.get_transport()
    if transport is None or not transport.is_active():
        raise ConnectionError("连接已断开")
    try:
        return _start_sftp(transport, server)
    except _SudoNeedsPassword:
        return _start_sftp_with_password(transport, server, password)
