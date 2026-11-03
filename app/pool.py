from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
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
    auth: str = "password"
    password: str | None = None
    passphrase: str | None = None
    key_path: str = ""
    sudo_password: str | None = None
    sudo_sftp: paramiko.SFTPClient | None = None
    closed: bool = False
    waiters: int = 0
    size_gen: int = 0

    def close(self) -> None:
        self.closed = True
        seen: set[int] = set()
        for item in (self.sudo_sftp, self.user_sftp, self.sftp):
            if item is None or id(item) in seen:
                continue
            seen.add(id(item))
            try:
                item.close()
            except Exception:
                pass
        try:
            self.client.close()
        except Exception:
            pass


@dataclass
class TransferLink:
    client: paramiko.SSHClient
    sftp: paramiko.SFTPClient
    lock: threading.Lock
    sudo: bool = False
    sudo_password: str | None = None
    closed: bool = False

    def close(self) -> None:
        self.closed = True
        try:
            self.sftp.close()
        except Exception:
            pass
        try:
            self.client.close()
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
        self._transfers: dict[str, TransferLink] = {}
        self._transfer_setup = threading.Lock()

    def connected(self, session_id: str) -> bool:
        with self._guard:
            live = self._lives.get(session_id)
            return live is not None and not live.closed

    def disconnect(self, session_id: str) -> None:
        self._discard_transfer(session_id)
        with self._guard:
            live = self._lives.pop(session_id, None)
        if live is not None:
            with live.lock:
                live.close()

    def connect(self, session: dict, password: str | None, passphrase: str | None, trust_host: bool) -> str:
        self._discard_transfer(session["id"])
        auth = session.get("auth") or "password"
        client, sftp, home = dial(session, password, passphrase, trust_host)
        live = Live(
            client=client,
            sftp=sftp,
            user_sftp=sftp,
            home=home,
            lock=threading.Lock(),
            auth=auth,
            password=password if auth == "password" else None,
            passphrase=passphrase if auth == "key" else None,
            key_path=session.get("keyPath") or "",
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
                if live.closed:
                    raise NotConnected()
                try:
                    yield live
                except (paramiko.SSHException, EOFError) as exc:
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

    def sudo_enabled(self, session_id: str) -> bool:
        with self._guard:
            live = self._lives.get(session_id)
            return live is not None and not live.closed and live.sudo_sftp is not None

    def enable_sudo(self, session_id: str, password: str) -> None:
        with self._guard:
            live = self._lives.get(session_id)
        if live is None or live.closed:
            raise NotConnected()
        with live.lock:
            if live.closed:
                raise NotConnected()
            try:
                server = sudo_access.find_sftp_server(live.client)
                opened = sudo_access.open_sftp(live.client, server, password)
            except ValueError:
                raise
            except PermissionError as exc:
                raise AuthFailed(str(exc)) from exc
            except ConnectionError as exc:
                raise ConnectionFailed(str(exc)) from exc
            if live.sudo_sftp is not None:
                try:
                    live.sudo_sftp.close()
                except Exception:
                    pass
            live.sudo_sftp = opened
            live.sftp = opened
            live.sudo_password = password
        self._discard_transfer(session_id)

    def disable_sudo(self, session_id: str) -> None:
        with self._guard:
            live = self._lives.get(session_id)
        if live is None or live.closed:
            raise NotConnected()
        with live.lock:
            if live.sudo_sftp is not None:
                try:
                    live.sudo_sftp.close()
                except Exception:
                    pass
                live.sudo_sftp = None
                live.sudo_password = None
            live.sftp = live.user_sftp
        self._discard_transfer(session_id)

    @contextmanager
    def transfer(self, session: dict):
        link = self._ensure_transfer(session)
        dropped: BaseException | None = None
        try:
            with link.lock:
                if link.closed:
                    raise ConnectionDropped("上传连接已断开")
                try:
                    yield link
                except (paramiko.SSHException, EOFError) as exc:
                    link.close()
                    dropped = exc
        finally:
            pass
        if dropped is not None:
            self._discard_transfer(session["id"], link)
            raise ConnectionDropped(f"上传连接已断开：{dropped}") from dropped

    def _ensure_transfer(self, session: dict) -> TransferLink:
        with self._transfer_setup:
            session_id = session["id"]
            with self._guard:
                live = self._lives.get(session_id)
                if live is None or live.closed:
                    raise NotConnected()
                snapshot = {
                    "host": session["host"],
                    "port": session["port"],
                    "username": session["username"],
                    "auth": live.auth,
                    "keyPath": live.key_path,
                    "password": live.password,
                    "passphrase": live.passphrase,
                    "sudo": live.sudo_sftp is not None,
                    "sudo_password": live.sudo_password,
                }
                current = self._transfers.get(session_id)
            if current is not None and not current.closed and current.sudo == snapshot["sudo"]:
                return current
            self._discard_transfer(session_id, current)
            client, sftp, _home = dial(snapshot, snapshot["password"], snapshot["passphrase"], False)
            try:
                if snapshot["sudo"]:
                    if not snapshot["sudo_password"]:
                        raise ValueError("sudo 查看开着，但没有可用的 sudo 密码来建立上传连接")
                    server = sudo_access.find_sftp_server(client)
                    root = sudo_access.open_sftp(client, server, snapshot["sudo_password"])
                    try:
                        sftp.close()
                    except Exception:
                        pass
                    sftp = root
            except Exception:
                try:
                    sftp.close()
                except Exception:
                    pass
                client.close()
                raise
            link = TransferLink(
                client=client,
                sftp=sftp,
                lock=threading.Lock(),
                sudo=bool(snapshot["sudo"]),
                sudo_password=snapshot["sudo_password"] if snapshot["sudo"] else None,
            )
            with self._guard:
                live = self._lives.get(session_id)
                if live is None or live.closed:
                    link.close()
                    raise NotConnected()
                previous = self._transfers.pop(session_id, None)
                self._transfers[session_id] = link
            if previous is not None and previous is not current:
                previous.close()
            return link

    def _discard_transfer(self, session_id: str, expected: TransferLink | None = None) -> None:
        with self._guard:
            current = self._transfers.get(session_id)
            if expected is not None and current is not expected:
                link = expected if expected is not current else None
            else:
                link = self._transfers.pop(session_id, None)
        if link is not None and not link.closed:
            link.close()


def _forget_host(client: paramiko.SSHClient, host: str, port: int) -> None:
    keys = client.get_host_keys()
    for name in (host, f"[{host}]:{port}"):
        if name in keys:
            del keys[name]


pool = Pool()
