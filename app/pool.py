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
    size_link: TransferLink | None = None
    size_unavailable: bool = False
    closed: bool = False
    waiters: int = 0
    size_gen: int = 0

    def reset_elevation(self) -> None:
        # Invalidate the cached link before exposing the new identity.
        self.size_gen += 1
        link = self.size_link
        self.size_link = None
        if link is not None:
            with link.lock:
                link.close()
        if self.transfer_link is not None:
            self.transfer_link.close()
            self.transfer_link = None
        if self.sftp is not self.user_sftp:
            _close(self.sftp)
        self.sftp = self.user_sftp
        self.elevation_method = None
        self.sudo_password = None

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
    owns_sftp: bool = False
    closed: bool = False

    @property
    def sudo(self) -> bool:
        return self.elevation_method == "sudo"

    def close(self) -> None:
        self.closed = True
        self.sudo_password = None
        if self.owns_sftp or self.client is not None:
            _close(self.sftp)
        if self.client is not None:
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
            elevated = live.elevation_method is not None
        if elevated:
            return self._scan_size(
                session_id, path, generation, lambda: self._shared_turn(session_id), shared=True,
            )
        link = self._ensure_size_link(session_id)
        if link is None:
            return self._scan_size(
                session_id, path, generation, lambda: self._shared_turn(session_id), shared=True,
            )
        try:
            return self._scan_size(
                session_id, path, generation, lambda: self._dedicated_turn(link), shared=False,
            )
        except (paramiko.SSHException, EOFError, ConnectionDropped) as exc:
            self._drop_size_link(session_id)
            transfer.abandon_channel_sizes(session_id, shared=False)
            if not self._same_size_gen(session_id, generation):
                return _stale_size(path, None)
            raise OSError("统计连接已断开") from exc

    def interrupt_size_scan(self, session_id: str, wait: bool = False) -> None:
        with self._guard:
            live = self._lives.get(session_id)
            if live is None or live.closed:
                return
            live.size_gen += 1
            link = live.size_link
        if link is None:
            return
        locked = link.lock.acquire(blocking=wait)
        if not locked:
            return
        try:
            if not link.closed:
                transfer.abandon_channel_sizes(session_id, shared=False)
        finally:
            link.lock.release()

    def _scan_size(self, session_id: str, path: str, generation: int, turn_of, shared: bool) -> dict:
        state = None
        deadline = time.monotonic() + _SIZE_SLICE
        while True:
            if not self._same_size_gen(session_id, generation):
                self._stop_size(session_id, state, generation, turn_of)
                return _stale_size(path, state)
            with turn_of() as (sftp, busy):
                if sftp is None:
                    raise OSError("统计连接已关闭")
                view, state, again = self._size_chunk(
                    session_id, path, generation, state, deadline, sftp, busy, shared,
                )
            if not again:
                return view

    def _size_chunk(self, session_id, path, generation, state, deadline, sftp, busy, shared: bool):
        if not self._same_size_gen(session_id, generation):
            if state is not None:
                transfer.abandon_size_state(session_id, state, generation)
            return _stale_size(path, state), state, False
        if busy and state is None:
            return {
                "path": path, "size": 0, "partial": True, "limited": False, "counted": False, "stale": False,
            }, None, False
        if state is None:
            prepared = transfer.size_prepare(sftp, session_id, path)
            if prepared.get("ready"):
                return prepared["result"], None, False
            state = prepared["state"]
            state["owner"] = generation
            state["shared"] = shared
        if busy:
            transfer.size_one(sftp, state, True)
            return transfer.size_commit(session_id, state), state, False
        if transfer.size_finished(state) or time.monotonic() >= deadline:
            return transfer.size_commit(session_id, state), state, False
        transfer.size_one(sftp, state, False)
        if not self._same_size_gen(session_id, generation):
            transfer.abandon_size_state(session_id, state, generation)
            return _stale_size(path, state), state, False
        if state.get("yielded") or transfer.size_finished(state) or time.monotonic() >= deadline:
            return transfer.size_commit(session_id, state), state, False
        return None, state, True

    def _stop_size(self, session_id: str, state, generation: int, turn_of) -> None:
        if state is None:
            return
        try:
            with turn_of() as (sftp, _busy):
                if sftp is not None:
                    transfer.abandon_size_state(session_id, state, generation)
        except (NotConnected, ConnectionDropped, paramiko.SSHException, EOFError, OSError):
            transfer.abandon_size_state(session_id, state, generation)

    @contextmanager
    def _shared_turn(self, session_id: str):
        with self.session(session_id) as live:
            yield live.sftp, live.waiters > 0

    @contextmanager
    def _dedicated_turn(self, link: TransferLink):
        with link.lock:
            if link.closed:
                yield None, False
                return
            yield link.sftp, False

    def _ensure_size_link(self, session_id: str) -> TransferLink | None:
        with self._guard:
            live = self._lives.get(session_id)
            if live is None or live.closed:
                raise NotConnected()
            if live.elevation_method is not None or live.size_unavailable:
                return None
            if live.size_link is not None and not live.size_link.closed:
                return live.size_link
            client = live.client
        try:
            sftp = client.open_sftp()
        except (paramiko.SSHException, EOFError, OSError):
            with self._guard:
                current = self._lives.get(session_id)
                if current is not None and current.client is client:
                    current.size_unavailable = True
            return None
        try:
            channel = sftp.get_channel()
        except Exception:
            channel = None
        if channel is not None:
            try:
                channel.settimeout(None)
            except Exception:
                pass
        link = TransferLink(client=None, sftp=sftp, lock=threading.Lock(), owns_sftp=True)
        with self._guard:
            live = self._lives.get(session_id)
            if live is None or live.closed or live.elevation_method is not None or live.client is not client:
                link.close()
                if live is None or live.closed:
                    raise NotConnected()
                return None
            if live.size_link is not None and not live.size_link.closed:
                link.close()
                return live.size_link
            live.size_link = link
            return link

    def _drop_size_link(self, session_id: str) -> None:
        with self._guard:
            live = self._lives.get(session_id)
            if live is None:
                return
            link = live.size_link
            live.size_link = None
        if link is not None:
            with link.lock:
                link.close()

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


_SIZE_SLICE = 0.12


def _stale_size(path: str, state: dict | None) -> dict:
    if state is None:
        return {"path": path, "size": 0, "partial": True, "limited": False, "counted": False, "stale": True}
    return transfer.size_view(state, stale=True)


pool = Pool()
