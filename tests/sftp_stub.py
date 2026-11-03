from __future__ import annotations

import errno
import io
import os
import shutil
import socket
import tarfile
import threading
import time

import paramiko
from paramiko.sftp import SFTP_OK


class Auth(paramiko.ServerInterface):
    def __init__(self, username: str, password: str, root: str, commands: list[list[str]]) -> None:
        self.username = username
        self.password = password
        self.root = root
        self.commands = commands

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username, password):
        if username == self.username and password == self.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_exec_request(self, channel, command):
        text = command.decode("utf-8", errors="replace") if isinstance(command, bytes) else str(command)
        if text.startswith("tar -xf - -C "):
            try:
                paths = _split_quoted(text[len("tar -xf - -C ") :])
            except ValueError:
                return False
            if len(paths) != 1:
                return False
            threading.Thread(target=self._extract_upload, args=(channel, paths[0]), daemon=True).start()
            return True
        if text.startswith("tar -cf - "):
            try:
                pairs = _parse_tar_cf(text[len("tar -cf - ") :])
            except ValueError:
                return False
            threading.Thread(target=self._pack_download, args=(channel, pairs), daemon=True).start()
            return True
        prefix = "rm -rf -- "
        if not text.startswith(prefix):
            return False
        try:
            paths = _split_quoted(text[len(prefix) :])
        except ValueError:
            return False
        threading.Thread(target=self._remove, args=(channel, paths), daemon=True).start()
        return True

    def _extract_upload(self, channel, dest_remote: str) -> None:
        code = 0
        try:
            chunks = []
            while True:
                data = channel.recv(1024 * 1024)
                if not data:
                    break
                chunks.append(data)
            local_dest = self.root if dest_remote == "/" else _under_root(self.root, dest_remote)
            os.makedirs(local_dest, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(b"".join(chunks)), mode="r:*") as archive:
                for member in archive.getmembers():
                    parts = [part for part in member.name.replace("\\", "/").split("/") if part not in ("", ".")]
                    if not parts or ".." in parts:
                        raise OSError("invalid tar path")
                    target = os.path.abspath(os.path.join(local_dest, *parts))
                    if os.path.commonpath([os.path.abspath(local_dest), target]) != os.path.abspath(local_dest):
                        raise OSError("tar escaped")
                    if member.isdir():
                        os.makedirs(target, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    source = archive.extractfile(member)
                    with open(target, "wb") as handle:
                        if source is not None:
                            shutil.copyfileobj(source, handle)
            self.commands.append(["tar-xf", dest_remote])
        except Exception as exc:
            code = 1
            try:
                channel.send_stderr(str(exc).encode("utf-8", "replace"))
            except Exception:
                pass
        try:
            channel.send_exit_status(code)
        except Exception:
            pass
        try:
            channel.close()
        except Exception:
            pass

    def _pack_download(self, channel, pairs: list[tuple[str, str]]) -> None:
        code = 0
        try:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for parent, name in pairs:
                    remote = name if parent == "/" else parent.rstrip("/") + "/" + name
                    if not remote.startswith("/"):
                        remote = "/" + remote
                    local = _under_root(self.root, remote)
                    archive.add(local, arcname=name, recursive=True)
                    self.commands.append(["tar-cf", remote])
            payload = buffer.getvalue()
            view = memoryview(payload)
            while view:
                sent = channel.send(view[:65536])
                if not sent:
                    break
                view = view[sent:]
        except Exception as exc:
            code = 1
            try:
                channel.send_stderr(str(exc).encode("utf-8", "replace"))
            except Exception:
                pass
        try:
            channel.send_exit_status(code)
        except Exception:
            pass
        try:
            channel.close()
        except Exception:
            pass

    def _remove(self, channel, paths: list[str]) -> None:
        code = 0
        try:
            self.commands.append(paths)
            for path in paths:
                local = _under_root(self.root, path)
                if os.path.islink(local) or not os.path.isdir(local):
                    os.remove(local)
                else:
                    shutil.rmtree(local)
        except OSError as exc:
            code = 1
            try:
                channel.send_stderr(str(exc).encode("utf-8", errors="replace"))
            except Exception:
                pass
        try:
            channel.send_exit_status(code)
        except Exception:
            pass
        try:
            channel.close()
        except Exception:
            pass


class Handle(paramiko.SFTPHandle):
    def close(self):
        closed = []
        for item in (getattr(self, "readfile", None), getattr(self, "writefile", None)):
            if item is not None and item not in closed:
                item.close()
                closed.append(item)
        self.readfile = None
        self.writefile = None
        return SFTP_OK


class StubSFTPServer(paramiko.SFTPServerInterface):
    def __init__(self, server, root: str) -> None:
        super().__init__(server)
        self.root = os.path.abspath(root)

    def canonicalize(self, path):
        return self._remote(path)

    def list_folder(self, path):
        try:
            names = os.listdir(self._local(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)
        result = []
        for name in names:
            try:
                attr = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(self._local(path), name)))
            except OSError:
                continue
            attr.filename = name
            result.append(attr)
        return result

    def stat(self, path):
        return self._stat(path)

    def lstat(self, path):
        return self._stat(path)

    def open(self, path, flags, attr):
        local = self._local(path)
        try:
            handle_file, read, write = _open_local(local, flags)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)
        handle = Handle(flags)
        handle.readfile = handle_file if read else None
        handle.writefile = handle_file if write else None
        return handle

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._local(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)
        return SFTP_OK

    def rmdir(self, path):
        try:
            os.rmdir(self._local(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)
        return SFTP_OK

    def remove(self, path):
        try:
            os.remove(self._local(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)
        return SFTP_OK

    def rename(self, oldpath, newpath):
        return self._rename(oldpath, newpath)

    def posix_rename(self, oldpath, newpath):
        return self._rename(oldpath, newpath)

    def _rename(self, oldpath, newpath):
        try:
            os.replace(self._local(oldpath), self._local(newpath))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)
        return SFTP_OK

    def _stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._local(path)))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)

    def _remote(self, path: str) -> str:
        if not path or path == ".":
            return "/"
        path = path.replace("\\", "/")
        if not path.startswith("/"):
            path = "/" + path
        parts: list[str] = []
        for part in path.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(part)
        return "/" + "/".join(parts) if parts else "/"

    def _local(self, path: str) -> str:
        remote = self._remote(path)
        relative = remote.strip("/")
        local = self.root if not relative else os.path.abspath(os.path.join(self.root, *relative.split("/")))
        root = os.path.abspath(self.root)
        try:
            if os.path.commonpath([root, local]) != root:
                raise OSError(errno.EACCES, "denied")
        except ValueError as exc:
            raise OSError(errno.EACCES, "denied") from exc
        return local


class SftpStub:
    def __init__(self, root: str, username: str = "user", password: str = "pass") -> None:
        self.root = root
        self.username = username
        self.password = password
        self.commands: list[list[str]] = []
        self.host_key = paramiko.RSAKey.generate(2048)
        self._stop = threading.Event()
        self._transports: list[paramiko.Transport] = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
        for transport in self._transports:
            try:
                transport.close()
            except Exception:
                pass
        self.thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client) -> None:
        transport = paramiko.Transport(client)
        transport.add_server_key(self.host_key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, StubSFTPServer, self.root)
        self._transports.append(transport)
        try:
            transport.start_server(server=Auth(self.username, self.password, self.root, self.commands))
        except Exception:
            return
        while transport.is_active() and not self._stop.is_set():
            time.sleep(0.2)


def _shell_tokens(payload: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(payload):
        if payload[index] == " ":
            index += 1
            continue
        if payload[index] != "'":
            start = index
            while index < len(payload) and payload[index] != " ":
                index += 1
            tokens.append(payload[start:index])
            continue
        index += 1
        chars: list[str] = []
        while index < len(payload):
            if payload.startswith("'\"'\"'", index):
                chars.append("'")
                index += 5
                continue
            if payload[index] == "'":
                index += 1
                break
            chars.append(payload[index])
            index += 1
        else:
            raise ValueError("引号没有结束")
        tokens.append("".join(chars))
    return tokens


def _parse_tar_cf(payload: str) -> list[tuple[str, str]]:
    tokens = _shell_tokens(payload)
    pairs: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        if tokens[index] != "-C" or index + 2 >= len(tokens):
            raise ValueError("bad tar")
        pairs.append((tokens[index + 1], tokens[index + 2]))
        index += 3
    if not pairs:
        raise ValueError("没有路径")
    return pairs


def _split_quoted(payload: str) -> list[str]:
    paths: list[str] = []
    index = 0
    while index < len(payload):
        if payload[index] == " ":
            index += 1
            continue
        if payload[index] != "'":
            raise ValueError("未加引号的路径")
        index += 1
        chars: list[str] = []
        while index < len(payload):
            if payload.startswith("'\"'\"'", index):
                chars.append("'")
                index += 5
                continue
            if payload[index] == "'":
                index += 1
                break
            chars.append(payload[index])
            index += 1
        else:
            raise ValueError("引号没有结束")
        paths.append("".join(chars))
    if not paths:
        raise ValueError("没有路径")
    return paths


def _under_root(root: str, remote: str) -> str:
    if not remote.startswith("/") or remote == "/" or "\x00" in remote:
        raise OSError(errno.EACCES, "refused")
    parts = [part for part in remote.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise OSError(errno.EACCES, "refused")
    local = os.path.abspath(os.path.join(root, *parts))
    root_abs = os.path.abspath(root)
    if os.path.commonpath([root_abs, local]) != root_abs:
        raise OSError(errno.EACCES, "refused")
    return local


def _open_local(path: str, flags: int):
    read = False
    write = False
    if flags & os.O_RDWR:
        read = True
        write = True
    elif flags & os.O_WRONLY:
        write = True
    else:
        read = True
    if write and not read:
        mode = "ab" if flags & os.O_APPEND else "wb"
    elif write and read:
        if flags & os.O_APPEND:
            mode = "a+b"
        elif flags & os.O_TRUNC or not os.path.exists(path):
            mode = "w+b"
        else:
            mode = "r+b"
    else:
        mode = "rb"
    return open(path, mode), read, write
