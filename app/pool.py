from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import paramiko

from app.home import get_home
from app import sudo as sudo_access
from app import transfer


class NotConnected(Exception):
    pass


class AuthRequired(Exception):
    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


class AuthFailed(Exception):
    pass


class HostKeyChanged(Exception):
    pass


class ConnectionFailed(Exception):
    pass


class ConnectionDropped(Exception):
    pass


@dataclass
class Live:
    client: paramiko.SSHClient
    sftp: paramiko.SFTPClient
    user_sftp: paramiko.SFTPClient
    home: str
    lock: threading.Lock
    login: dict = field(repr=False)
    password: str | None = field(default=None, repr=False)
    passphrase: str | None = field(default=None, repr=False)
    sudo_password: str | None = field(default=None, repr=False)
    elevation_method: str | None = None
    transfer_link: TransferLink | None = None
    closed: bool = False
    waiters: int = 0
    size_gen: int = 0

    def reset_elevation(self) -> None:
        # Invalidate the cached link before exposing the new identity.
        if self.transfer_link is not None:
            self.transfer_link.close()
            self.transfer_link = None
        if self.sftp is not self.user_sftp:
            _close(self.sftp)
        self.sftp = self.user_sftp
        self.elevation_method = None
        self.sudo_password = None
        self.size_gen += 1

    def close(self) -> None:
        self.closed = True
        self.reset_elevation()
        self.password = self.passphrase = None
        _close(self.user_sftp)
        _close(self.client)


@dataclass
class TransferLink:
    # A su transfer borrows the authenticated SFTP channel and has no SSH client.
    client: paramiko.SSHClient | None
    sftp: paramiko.SFTPClient
    lock: threading.Lock
    elevation_method: str | None = None
    sudo_password: str | None = field(default=None, repr=False)
    closed: bool = False

    @property
    def sudo(self) -> bool:
        return self.elevation_method == "sudo"

    def close(self) -> None:
        self.closed = True
        self.sudo_password = None
        if self.client is not None:
            _close(self.sftp)
            _close(self.client)


def _close(resource) -> None:
    try:
        resource.close()
    except Exception:
        pass


def dial(session: dict, password: str | None, passphrase: str | None, trust_host: bool) -> tuple[paramiko.SSHClient, paramiko.SFTPClient, str]:
    auth = session.get("auth") or "password"
    if auth == "password" and not password:
        raise AuthRequired("需要密码", "password")
    key_path = session.get("keyPath") or ""
    if auth == "key":
        if not key_path:
            raise ValueError("请填写私钥路径")
        if not Path(key_path).is_file():
            raise ValueError("找不到私钥文件")

    client = paramiko.SSHClient()
    known_hosts = get_home() / "known_hosts"
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    if trust_host:
        _forget_host(client, session["host"], int(session["port"]))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs = {
            "hostname": session["host"],
            "port": int(session["port"]),
            "username": session["username"],
            "timeout": 20,
            "banner_timeout": 20,
            "auth_timeout": 20,
            "allow_agent": auth == "agent",
            "look_for_keys": auth == "agent",
        }
        if auth == "password":
            kwargs["password"] = password
        elif auth == "key":
            kwargs["key_filename"] = key_path
            if passphrase:
                kwargs["passphrase"] = passphrase
        client.connect(**kwargs)
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
            transport.default_window_size = 4 * 1024 * 1024
            try:
                transport.sock.settimeout(None)
            except Exception:
                pass
        sftp = client.open_sftp()
        channel = sftp.get_channel()
        if channel is not None:
            channel.settimeout(None)
        home = sftp.normalize(".")
        try:
            client.save_host_keys(str(known_hosts))
        except OSError:
            pass
    except paramiko.BadHostKeyException as exc:
        client.close()
        raise HostKeyChanged("远程主机密钥和上次保存的不一致。如果这台机器重装过，可以确认后继续。") from exc
    except paramiko.PasswordRequiredException as exc:
        client.close()
        raise AuthRequired("私钥需要口令", "passphrase") from exc
    except paramiko.AuthenticationException as exc:
        client.close()
        raise AuthFailed("认证失败，请检查用户名、密码或私钥") from exc
    except (socket.timeout, TimeoutError, paramiko.SSHException, OSError) as exc:
        client.close()
        message = str(exc).lower()
        if "passphrase" in message or "encrypted" in message:
            raise AuthRequired("私钥需要口令", "passphrase") from exc
        raise ConnectionFailed(f"连接失败：{exc}") from exc
    return client, sftp, home


class Pool:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._lives: dict[str, Live] = {}

    def connected(self, session_id: str) -> bool:
        with self._guard:
            live = self._lives.get(session_id)
            return live is not None and not live.closed

    def disconnect(self, session_id: str) -> None:
        with self._guard:
            live = self._lives.pop(session_id, None)
        if live is not None:
            with live.lock:
                live.close()

    def connect(self, session: dict, password: str | None, passphrase: str | None, trust_host: bool) -> str:
        auth = session.get("auth") or "password"
        client, sftp, home = dial(session, password, passphrase, trust_host)
        live = Live(
            client=client,
            sftp=sftp,
            user_sftp=sftp,
            home=home,
            lock=threading.Lock(),
            login={key: session[key] for key in ("host", "port", "username", "keyPath", "auth") if key in session},
            password=password if auth == "password" else None,
            passphrase=passphrase if auth == "key" else None,
        )
        with self._guard:
            old = self._lives.pop(session["id"], None)
            self._lives[session["id"]] = live
        if old is not None:
            with old.lock:
                old.close()
        return home

    @contextmanager
    def session(self, session_id: str):
        with self._guard:
            live = self._lives.get(session_id)
        if live is None or live.closed:
            raise NotConnected()
        live.waiters += 1
        acquired = False
        dropped: BaseException | None = None
        try:
            with live.lock:
                acquired = True
                live.waiters -= 1
                with self._guard:
                    if self._lives.get(session_id) is not live or live.closed:
                        raise NotConnected()
                try:
                    transport = live.client.get_transport()
                    if transport is None or not transport.is_active():
                        raise EOFError("SSH 连接已断开")
                    yield live
                except (paramiko.SSHException, EOFError, OSError) as exc:
                    transport = live.client.get_transport()
                    active = transport is not None and transport.is_active()
                    if isinstance(exc, OSError) and active and not live.sftp.get_channel().closed:
                        raise
                    if active and live.elevation_method is not None:
                        live.reset_elevation()
                        transfer.clear_folder_sizes(session_id)
                        raise ConnectionError("管理员文件通道已关闭，已返回登录账号") from exc
                    live.close()
                    dropped = exc
        finally:
            if not acquired:
                live.waiters -= 1
        if dropped is not None:
            with self._guard:
                if self._lives.get(session_id) is live:
                    self._lives.pop(session_id, None)
            raise ConnectionDropped(f"连接已断开：{dropped}") from dropped

    def call(self, session_id: str, fn):
        with self.session(session_id) as live:
            return fn(live)

    def scan_size(self, session_id: str, path: str) -> dict:
        with self._guard:
            live = self._lives.get(session_id)
            if live is None or live.closed:
                raise NotConnected()
            live.size_gen += 1
            generation = live.size_gen
        prepared = self.call(session_id, lambda current: transfer.size_prepare(current.sftp, session_id, path))
        if prepared.get("ready"):
            return prepared["result"]
        state = prepared["state"]
        deadline = time.monotonic() + 0.12

        def step(current, pending=state):
            return transfer.size_one(current.sftp, pending, current.waiters > 0)

        while state["stack"] and not state.get("capped") and time.monotonic() < deadline:
            if not self._same_size_gen(session_id, generation):
                return transfer.size_view(state, stale=True)
            state = self.call(session_id, step)
            if state.get("yielded"):
                break
        if not self._same_size_gen(session_id, generation):
            return transfer.size_view(state, stale=True)
        return transfer.size_commit(session_id, state)

    def _same_size_gen(self, session_id: str, generation: int) -> bool:
        with self._guard:
            live = self._lives.get(session_id)
            return live is not None and not live.closed and live.size_gen == generation

    def elevation_method(self, session_id: str) -> str | None:
        with self._guard:
            live = self._lives.get(session_id)
            if live is not None and not live.closed:
                transport = live.client.get_transport()
                if transport is not None and transport.is_active() and not live.sftp.get_channel().closed:
                    return live.elevation_method
        return None

    def sudo_enabled(self, session_id: str) -> bool:
        return self.elevation_method(session_id) is not None

    def enable_sudo(self, session_id: str, password: str, method: str = "sudo") -> None:
        if method not in ("sudo", "su"):
            raise ValueError("未知的管理员认证方式")
        if method == "su" and not password:
            raise ValueError("请输入 root 密码")
        with self.session(session_id) as live:
            try:
                server = sudo_access.find_sftp_server(live.client)
                if method == "su":
                    opened = sudo_access.open_su_sftp(live.client, server, password)
                else:
                    opened = sudo_access.open_sftp(live.client, server, password)
            except PermissionError as exc:
                raise AuthFailed(str(exc)) from exc
            except ConnectionError as exc:
                raise ConnectionFailed(str(exc)) from exc
            live.reset_elevation()
            live.sftp = opened
            live.sudo_password = password if method == "sudo" else None
            live.elevation_method = method
            transfer.clear_folder_sizes(session_id)

    def disable_sudo(self, session_id: str) -> None:
        with self.session(session_id) as live:
            live.reset_elevation()
            transfer.clear_folder_sizes(session_id)

    @contextmanager
    def transfer(self, session: dict):
        with self.session(session["id"]) as live:
            if live.elevation_method == "su":
                # Serialize with file operations and deactivation. A root password
                # is never retained or replayed to authenticate a second channel.
                yield TransferLink(None, live.sftp, live.lock, elevation_method="su")
                return
            link = self._ensure_transfer(live)

        with link.lock:
            if link.closed:
                raise ConnectionDropped("传输连接已关闭")
            try:
                yield link
            except (paramiko.SSHException, EOFError) as exc:
                link.close()
                raise ConnectionDropped(f"传输连接已断开：{exc}") from exc

    def _ensure_transfer(self, live: Live) -> TransferLink:
        # Only setup runs under live.lock. Uploads/downloads on their own link
        # can run concurrently with browsing, and switching identity closes it.
        if live.transfer_link is not None and not live.transfer_link.closed:
            return live.transfer_link
        client, sftp, _home = dial(live.login, live.password, live.passphrase, False)
        if live.elevation_method == "sudo":
            try:
                server = sudo_access.find_sftp_server(client)
                root = sudo_access.open_sftp(client, server, live.sudo_password or "")
            except Exception:
                _close(sftp)
                _close(client)
                raise
            _close(sftp)
            sftp = root
        link = TransferLink(
            client=client, sftp=sftp, lock=threading.Lock(),
            elevation_method=live.elevation_method, sudo_password=live.sudo_password,
        )
        live.transfer_link = link
        return link


def _forget_host(client: paramiko.SSHClient, host: str, port: int) -> None:
    keys = client.get_host_keys()
    for name in (host, f"[{host}]:{port}"):
        if name in keys:
            del keys[name]


pool = Pool()
