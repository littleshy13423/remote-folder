from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.sftp_stub import SftpStub


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("FAIL: " + message)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        home = Path(temp_name)
        os.environ["RF_HOME"] = str(home / "appdata")
        os.environ["RF_NO_BROWSER"] = "1"
        os.environ["RF_SYNC_DISABLE"] = "1"
        os.environ["RF_OPEN_DRY_RUN"] = str(home / "open.json")

        from app.main import app
        from app.opener import push_pending
        from app.pool import pool
        from app.remote_path import mode_string, safe_relative
        from app.transfer import (
            LARGE_OPEN_BYTES,
            cache_file,
            download_tar_command,
            owner_and_group,
            remove_command,
            size_one,
            upload_tar_command,
        )
        from app.stats import STATS_COMMAND, consume_cpu, forget, interpret, parse_snapshot
        from app.sudo import explain_sudo_failure, match_sftp_server, sudo_password_command
        from app.terminal import remote_command, shell_quote
        from fastapi.testclient import TestClient
        import stat

        expect(STATS_COMMAND == "cat /proc/stat /proc/meminfo", "stats command changed")
        sample = "cpu  100 0 100 800 0 0 0 0\nMemTotal: 8000000 kB\nMemAvailable: 4000000 kB\n"
        counters, memory = parse_snapshot(sample)
        expect(counters == (1000, 800), str(counters))
        expect(memory["total"] == 8000000 * 1024 and memory["used"] == 4000000 * 1024, str(memory))
        expect(memory["percent"] == 50.0, str(memory))
        fallback = "cpu  10 0 10 80\nMemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 50 kB\n"
        _, fallback_memory = parse_snapshot(fallback)
        expect(fallback_memory["used"] == 800 * 1024 and fallback_memory["percent"] == 80.0, str(fallback_memory))
        expect(parse_snapshot("nope")[1] is None, "garbage accepted as memory")
        forget("cpu-test")
        expect(consume_cpu("cpu-test", 1000, 800, now=0) is None, "first cpu sample should be a baseline")
        expect(consume_cpu("cpu-test", 2000, 1600, now=2) == 20.0, "cpu delta")
        expect(consume_cpu("cpu-test", 3000, 1600, now=30) is None, "stale cpu sample was used")
        expect(consume_cpu("cpu-test", 1000, 900, now=31) is None, "cpu counters going backwards were used")
        expect(interpret("cpu-test", None) == {"supported": False}, "empty stats snapshot accepted")
        expect(match_sftp_server("/usr/lib/openssh/sftp-server") == "/usr/lib/openssh/sftp-server", "sftp path rejected")
        expect(match_sftp_server("/tmp/evil") is None, "unexpected sftp path accepted")
        expect(explain_sudo_failure("user is not in the sudoers file", 1) == "这个账号不能使用 sudo", "sudoers message")
        expect("密码不正确" in explain_sudo_failure("Sorry, try again.", 1), "sudo password message")
        expect("没有收下" in explain_sudo_failure("sudo: a password is required", 1), "sudo noninteractive message")
        command = sudo_password_command("/usr/lib/openssh/sftp-server")
        expect("echo RFREADY >&2; exec /usr/lib/openssh/sftp-server" in command, "sudo command")
        expect(command.startswith("sudo -S -p RFSUDO -- /bin/sh -c "), "sudo command prefix")
        expect(shell_quote("/tmp/a'b") == "'/tmp/a'\"'\"'b'", "shell quote")
        expect(remove_command(["/tmp/a'b"]) == "rm -rf -- '/tmp/a'\"'\"'b'", remove_command(["/tmp/a'b"]))
        from app.sudo import exec_su
        from app.transfer import RootPasswordRequired, delete_paths

        class DenyRm:
            def exec_command(self, command, timeout=None):
                channel = type("Channel", (), {"recv_exit_status": lambda self: 1, "shutdown_write": lambda self: None})()
                stream = type("Stream", (), {
                    "channel": channel,
                    "write": lambda self, data: None,
                    "flush": lambda self: None,
                    "read": lambda self: b"rm: cannot remove '/sec': Permission denied\n",
                })()
                return stream, stream, stream

        class NoWalk:
            def normalize(self, path):
                return path if path.startswith("/") else "/" + path

            def lstat(self, path):
                return type("Attr", (), {"st_mode": stat.S_IFREG | 0o644})()

            def listdir_attr(self, path):
                raise SystemExit("delete walked over SFTP")

        try:
            delete_paths(NoWalk(), ["/sec"], client=DenyRm())
            raise SystemExit("root password was not requested")
        except RootPasswordRequired:
            pass

        class SuClient:
            def __init__(self):
                self.password = ""
                self.command = ""

            def get_transport(self):
                return type("Transport", (), {"is_active": lambda self: True})()

            def exec_command(self, command, timeout=None):
                self.command = command

                def token(prefix):
                    start = command.index(prefix)
                    end = start + len(prefix)
                    while end < len(command) and command[end] in "0123456789abcdef":
                        end += 1
                    return command[start:end]

                channel = SuChannel(token("RF_SU_"), token("RF_RM_"), self)
                stdin = type("Stdin", (), {"channel": channel})()
                return stdin, None, None

        class SuChannel:
            def __init__(self, ready, done, client):
                self.ready = ready
                self.done = done
                self.client = client
                self.stderr = [b"Password: "]
                self.stdout = []
                self.sent = False

            def settimeout(self, value):
                return None

            def recv_stderr(self, _size):
                if self.stderr:
                    return self.stderr.pop(0)
                if self.sent:
                    self.sent = False
                    return (self.ready + "\n").encode("ascii")
                return b""

            def recv(self, _size):
                if self.stdout:
                    return self.stdout.pop(0)
                self.stdout.append(b"")
                return (self.done + ":0\n").encode("ascii")

            def sendall(self, payload):
                self.client.password = payload.decode("utf-8")
                self.sent = True

            def close(self):
                return None

        su = SuClient()
        expect(delete_paths(NoWalk(), ["/tmp/a'b"], client=su, root_method="su", root_password="root-secret")["ok"] is True, "su rm failed")
        expect(su.password == "root-secret\n", su.password)
        expect("root-secret" not in su.command, "password was embedded in the su command")
        expect("rm -rf --" in su.command and "/tmp/a" in su.command, su.command)
        expect(remove_command(["/data/box"], as_root=True, sudo_password="secret").startswith("sudo -S -p RFSUDO -- rm -rf -- "), "sudo rm")
        expect(upload_tar_command("/docs") == "tar -xf - -C '/docs'", upload_tar_command("/docs"))
        expect(upload_tar_command("/tmp/a'b") == "tar -xf - -C '/tmp/a'\"'\"'b'", upload_tar_command("/tmp/a'b"))
        expect(
            upload_tar_command("/docs", as_root=True, sudo_password="secret") == "sudo -S -p RFSUDO -- tar -xf - -C '/docs'",
            upload_tar_command("/docs", as_root=True, sudo_password="secret"),
        )
        expect(download_tar_command([("/", "docs")]) == "tar -cf - -C '/' 'docs'", download_tar_command([("/", "docs")]))
        expect(
            remote_command("/docs") == "cd '/docs' || printf '%s\\n' '无法进入这个目录，留在登录目录。'; exec bash",
            remote_command("/docs"),
        )

        try:
            safe_relative("../escape.txt")
            raise SystemExit("path traversal was accepted")
        except ValueError:
            pass
        expect(mode_string(stat.S_IFREG | 0o644) == "-rw-r--r--", "mode string mismatch")

        class LongName:
            def __init__(self, longname, uid=None, gid=None):
                self.longname = longname
                self.st_uid = uid
                self.st_gid = gid

        expect(
            owner_and_group(LongName("drwxr-xr-x    2 root     root         4096 Oct 21 15:17 docs")) == ("root", "root"),
            "owner from longname",
        )
        expect(
            owner_and_group(LongName("-rw-r--r--.   1 alice    staff           0 Oct 21 12:00 file")) == ("alice", "staff"),
            "selinux longname",
        )
        expect(owner_and_group(LongName("", 1000, 10)) == ("1000", "10"), "numeric owner fallback")

        class BigFile:
            def __init__(self, size):
                self.size = size
                self.downloaded = False

            def normalize(self, path):
                return path

            def lstat(self, path):
                return self._attr()

            def stat(self, path):
                return self._attr()

            def get(self, remote, local):
                self.downloaded = True

            def _attr(self):
                class Attr:
                    st_mode = stat.S_IFREG | 0o644
                    st_size = 0

                attr = Attr()
                attr.st_size = self.size
                return attr

        big = BigFile(LARGE_OPEN_BYTES)
        try:
            cache_file(big, "session", "/data.rar")
            raise SystemExit("large file was cached")
        except ValueError as exc:
            expect("下载" in str(exc), str(exc))
        expect(big.downloaded is False, "large file started downloading")

        paused = {
            "stack": ["/data"],
            "seen": set(),
            "total": 0,
            "nodes": 0,
            "blocked": False,
            "capped": False,
            "counted": False,
            "yielded": False,
        }

        class BusySftp:
            def listdir_attr(self, path):
                raise SystemExit("size scan listed while browsing")

        yielded = size_one(BusySftp(), paused, True)
        expect(yielded["yielded"] is True and yielded["counted"] is False, "size scan did not yield")

        class ChunkSftp:
            reads = 0
            closed = False

            def _adjust_cwd(self, path):
                return path

            def _request(self, cmd, _arg=None):
                from paramiko.message import Message
                from paramiko.sftp import CMD_CLOSE, CMD_HANDLE, CMD_NAME, CMD_OPENDIR, CMD_READDIR

                if cmd == CMD_OPENDIR:
                    msg = Message()
                    msg.add_string(b"dir")
                    msg.rewind()
                    return CMD_HANDLE, msg
                if cmd == CMD_CLOSE:
                    self.closed = True
                    msg = Message()
                    msg.rewind()
                    return 0, msg
                if cmd != CMD_READDIR:
                    raise AssertionError(cmd)
                self.reads += 1
                if self.reads > 2:
                    raise EOFError()
                msg = Message()
                msg.add_int(1)
                msg.add_string(f"f{self.reads}.bin")
                msg.add_string("-rw-r--r-- 1 user user 5")
                msg.add_int(0x1 | 0x4)
                msg.add_int64(5)
                msg.add_int(stat.S_IFREG | 0o644)
                msg.rewind()
                return CMD_NAME, msg

        chunk = ChunkSftp()
        partial = {
            "path": "/data",
            "stack": ["/data"],
            "seen": set(),
            "total": 0,
            "nodes": 0,
            "blocked": False,
            "capped": False,
            "counted": False,
            "yielded": False,
        }
        size_one(chunk, partial, False)
        expect(chunk.reads == 1 and partial["total"] == 5 and partial.get("reader") is not None, "size scan read the whole directory")
        expect(chunk.closed is False, "directory handle closed before the listing finished")
        size_one(chunk, partial, True)
        expect(chunk.reads == 1 and partial["yielded"] is True, "size scan kept reading while browsing")
        partial["yielded"] = False
        size_one(chunk, partial, False)
        expect(partial["total"] == 10 and chunk.reads == 2, "second directory packet was skipped")
        size_one(chunk, partial, False)
        expect(chunk.closed is True and partial.get("reader") is None and partial["stack"] == [], "directory listing did not finish")

        root = home / "remote"
        root.mkdir()
        stub = SftpStub(str(root), "user", "s3cret-token")
        try:
            with TestClient(app) as client:
                page = client.get("/")
                expect(page.status_code == 200, page.text)
                expect("remote-folder" in page.text, "homepage missing title")
                expect("浏览远程目录" not in page.text, "browse hint is still on the page")
                expect("打开方式" in page.text, "open-with hint missing")
                expect("用编辑器打开" not in page.text, "editor action is still on the page")
                expect("<textarea" not in page.text.lower(), "page contains a text editor")
                script = client.get("/static/app.js")
                expect(script.status_code == 200, "script missing")
                expect("新建文件夹" in script.text and "新建文件" in script.text, "create actions missing")
                expect("传输中" in script.text and "focusSession" in script.text, "session switch missing")
                expect("/stats" in script.text and "cpu-spark" in script.text, "resource monitor script missing")
                expect("打开 bash" in page.text, "bash action missing")
                expect("监控" in page.text and 'id="monitor-enabled"' in page.text, "resource monitor missing")
                expect("下载记录" in page.text and "choose_save_path" in script.text, "download history entry missing")

                def request(method: str, url: str, **kwargs):
                    headers = {"X-RF-Request": "1"}
                    headers.update(kwargs.pop("headers", {}))
                    return client.request(method, url, headers=headers, **kwargs)

                denied = client.get("/api/sessions")
                expect(denied.status_code == 403, "cross-site request was allowed")

                created = request(
                    "POST",
                    "/api/sessions",
                    json={
                        "name": "lab",
                        "host": "127.0.0.1",
                        "port": stub.port,
                        "username": "user",
                        "auth": "password",
                        "rememberPassword": False,
                    },
                )
                expect(created.status_code == 200, created.text)
                session_id = created.json()["id"]

                sudo_early = request(
                    "POST",
                    f"/api/sessions/{session_id}/sudo",
                    json={"password": "secret", "enable": True},
                )
                expect(sudo_early.status_code == 409, sudo_early.text)

                bad = request(
                    "POST",
                    f"/api/sessions/{session_id}/connect",
                    json={"password": "wrong", "remember": False},
                )
                expect(bad.status_code == 401, bad.text)

                connected = request(
                    "POST",
                    f"/api/sessions/{session_id}/connect",
                    json={"password": "s3cret-token", "remember": False},
                )
                expect(connected.status_code == 200, connected.text)
                expect(connected.json()["home"] == "/", connected.text)

                first_stats = request("GET", f"/api/sessions/{session_id}/stats")
                expect(first_stats.status_code == 200, first_stats.text)
                expect(first_stats.json()["supported"] is True, first_stats.text)
                expect(first_stats.json()["cpu"] is None, first_stats.text)
                expect(first_stats.json()["memory"]["percent"] == 50.0, first_stats.text)
                second_stats = request("GET", f"/api/sessions/{session_id}/stats")
                expect(second_stats.status_code == 200, second_stats.text)
                expect(second_stats.json()["cpu"] == 20.0, second_stats.text)
                expect(second_stats.json()["memory"]["used"] == 4000000 * 1024, second_stats.text)
                for _ in range(8):
                    polled = request("GET", f"/api/sessions/{session_id}/stats")
                    expect(polled.status_code == 200 and polled.json()["cpu"] == 20.0, polled.text)
                    expect(pool.connected(session_id), "resource monitor closed the session")

                os.environ["RF_TERMINAL_DRY_RUN"] = str(home / "terminal.json")
                bash_early = request(
                    "POST",
                    f"/api/sessions/{session_id}/terminal",
                    json={"path": "/"},
                )
                expect(bash_early.status_code == 200, bash_early.text)
                bash_args = json.loads((home / "terminal.json").read_text(encoding="utf-8"))["argv"]
                expect("user@127.0.0.1" in bash_args, str(bash_args))
                expect("PubkeyAuthentication=no" in bash_args, str(bash_args))
                expect(bash_args[-1] == remote_command("/"), str(bash_args))
                expect("-p" in bash_args and str(stub.port) in bash_args, str(bash_args))
                bash_record = json.loads((home / "terminal.json").read_text(encoding="utf-8"))
                expect(bash_record["askpass"] is True, str(bash_record))
                expect("s3cret-token" not in bash_record.__repr__(), "terminal record contains the password")
                expect("StrictHostKeyChecking=accept-new" in bash_args, str(bash_args))

                listed = request("GET", f"/api/sessions/{session_id}/list")
                expect(listed.status_code == 200, listed.text)
                expect(listed.json()["path"] == "/", listed.text)

                installed = request("POST", f"/api/sessions/{session_id}/install-key")
                expect(installed.status_code == 200, installed.text)
                expect(installed.json()["auth"] == "key", installed.text)
                authorized = (root / ".ssh" / "authorized_keys").read_text(encoding="utf-8")
                expect("ssh-ed25519 " in authorized, authorized)
                again = request("POST", f"/api/sessions/{session_id}/install-key")
                expect(again.status_code == 200, again.text)
                expect(again.json()["installed"] is False, again.text)
                expect(
                    (root / ".ssh" / "authorized_keys").read_text(encoding="utf-8").count("ssh-ed25519 ") == 1,
                    "public key was duplicated",
                )

                made = request(
                    "POST",
                    f"/api/sessions/{session_id}/mkdir",
                    json={"parent": "/", "name": "docs"},
                )
                expect(made.status_code == 200, made.text)
                duplicate_dir = request(
                    "POST",
                    f"/api/sessions/{session_id}/mkdir",
                    json={"parent": "/", "name": "docs"},
                )
                expect(duplicate_dir.status_code == 400, duplicate_dir.text)
                bash_docs = request(
                    "POST",
                    f"/api/sessions/{session_id}/terminal",
                    json={"path": "/docs"},
                )
                expect(bash_docs.status_code == 200, bash_docs.text)
                bash_key_args = json.loads((home / "terminal.json").read_text(encoding="utf-8"))["argv"]
                expect("IdentitiesOnly=yes" in bash_key_args, str(bash_key_args))
                expect(any(str(item).endswith("id_ed25519") for item in bash_key_args), str(bash_key_args))
                expect(bash_key_args[-1] == remote_command("/docs"), str(bash_key_args))
                expect(json.loads((home / "terminal.json").read_text(encoding="utf-8"))["askpass"] is False, "key login asked for a password")

                created = request(
                    "POST",
                    f"/api/sessions/{session_id}/mkfile",
                    json={"parent": "/docs", "name": "note.txt"},
                )
                expect(created.status_code == 200, created.text)
                expect((root / "docs" / "note.txt").read_bytes() == b"", "new file was not empty")
                duplicate_file = request(
                    "POST",
                    f"/api/sessions/{session_id}/mkfile",
                    json={"parent": "/docs", "name": "note.txt"},
                )
                expect(duplicate_file.status_code == 400, duplicate_file.text)
                expect((root / "docs" / "note.txt").read_bytes() == b"", "duplicate create overwrote the file")
                escaped = request(
                    "POST",
                    f"/api/sessions/{session_id}/mkfile",
                    json={"parent": "/docs", "name": "../escape.txt"},
                )
                expect(escaped.status_code == 400, escaped.text)
                expect(not (root / "escape.txt").exists(), "new file escaped the folder")

                uploaded = request(
                    "POST",
                    f"/api/sessions/{session_id}/upload",
                    data={"dest": "/docs", "relatives": json.dumps(["hello.txt"])},
                    files=[("files", ("hello.txt", b"hello remote", "text/plain"))],
                )
                expect(uploaded.status_code == 200, uploaded.text)
                uploaded_lines = [json.loads(line) for line in uploaded.text.splitlines() if line.strip()]
                expect(uploaded_lines[-1].get("ok") is True, uploaded.text)
                expect(any(line.get("phase") == "connect" for line in uploaded_lines), uploaded.text)
                expect(any(line.get("message") for line in uploaded_lines), uploaded.text)
                expect(len(stub._transports) >= 2, "upload used the browsing connection")
                expect((root / "docs" / "hello.txt").read_bytes() == b"hello remote", "upload did not land")
                nested = root / "docs" / "nested"
                nested.mkdir()
                (nested / "a.txt").write_bytes(b"xyz")
                sized = request("GET", f"/api/sessions/{session_id}/size?path=/docs")
                expect(sized.status_code == 200, sized.text)
                expect(sized.json()["size"] == len(b"hello remote") + len(b"xyz"), sized.text)
                expect(sized.json()["partial"] is False, sized.text)
                cached = request("GET", f"/api/sessions/{session_id}/size?path=/docs")
                expect(cached.json()["size"] == len(b"hello remote") + len(b"xyz"), cached.text)
                (nested / "a.txt").unlink()
                nested.rmdir()

                blocked = request(
                    "POST",
                    f"/api/sessions/{session_id}/upload",
                    data={"dest": "/docs", "relatives": json.dumps(["../escape.txt"])},
                    files=[("files", ("escape.txt", b"nope", "text/plain"))],
                )
                expect(blocked.status_code == 400, blocked.text)
                expect(not (root / "escape.txt").exists(), "path traversal wrote outside the folder")

                made_bulk = request(
                    "POST",
                    f"/api/sessions/{session_id}/mkdir",
                    json={"parent": "/", "name": "bulk"},
                )
                expect(made_bulk.status_code == 200, made_bulk.text)
                upload_url = f"/api/sessions/{session_id}/upload"
                dirs_only = request(
                    "POST", upload_url, data={"dest": "/bulk", "directories": json.dumps(["nested/empty"])},
                )
                expect(dirs_only.status_code == 200, dirs_only.text)
                expect(json.loads(dirs_only.text.splitlines()[-1]).get("ok") is True, dirs_only.text)
                empty = root / "bulk" / "nested" / "empty"
                expect(empty.is_dir() and not list(empty.iterdir()), "empty directory missing or contains placeholder")
                (empty / "keep.txt").write_bytes(b"keep")
                for invalid in ("{}", "[false]", "not-json", '["../outside"]', json.dumps(["limit"] * 5001)):
                    rejected = request("POST", upload_url, data={"dest": "/bulk", "directories": invalid})
                    expect(rejected.status_code == 400, rejected.text)
                expect(not (root / "outside").exists(), "directory escaped the upload destination")

                payload = b"x" * (600 * 1024)
                progressed = request(
                    "POST",
                    f"/api/sessions/{session_id}/upload",
                    data={"dest": "/bulk", "relatives": json.dumps(["big.bin"])},
                    files=[("files", ("big.bin", payload, "application/octet-stream"))],
                )
                expect(progressed.status_code == 200, progressed.text)
                progress_lines = [json.loads(line) for line in progressed.text.splitlines() if line.strip()]
                expect(progress_lines[-1].get("ok") is True, progressed.text)
                expect(
                    any(line.get("done", 0) < line.get("total", 0) and not line.get("ok") for line in progress_lines),
                    progressed.text,
                )
                expect((root / "bulk" / "big.bin").read_bytes() == payload, "progress upload bytes mismatch")
                collision = request("POST", upload_url, data={"dest": "/bulk", "directories": '["big.bin"]'})
                expect("error" in json.loads(collision.text.splitlines()[-1]), collision.text)
                expect((root / "bulk" / "big.bin").read_bytes() == payload, "directory replaced a file")
                many_names = [f"f{index}.txt" for index in range(1001)]
                many = request(
                    "POST",
                    f"/api/sessions/{session_id}/upload",
                    data={"dest": "/bulk", "relatives": json.dumps(many_names),
                          "directories": json.dumps(["nested/empty", "nested/another"])},
                    files=[("files", (name, b"x", "text/plain")) for name in many_names],
                )
                expect(many.status_code == 200, many.text[:500])
                expect((root / "bulk" / "f1000.txt").read_bytes() == b"x", "upload stopped before the last file")
                finished = json.loads(many.text.splitlines()[-1])
                expect(finished.get("ok") and finished["index"] == finished["count"] == 1003, str(finished))
                expect((empty / "keep.txt").read_bytes() == b"keep", "directory merge discarded existing contents")
                expect((root / "bulk" / "nested" / "another").is_dir(), "mixed upload omitted an empty directory")
                expect(["tar-xf", "/bulk"] in stub.commands, str(stub.commands))
                removed_bulk = request(
                    "POST",
                    f"/api/sessions/{session_id}/delete",
                    json={"paths": ["/bulk"]},
                )
                expect(removed_bulk.status_code == 200, removed_bulk.text)

                folder = request("GET", f"/api/sessions/{session_id}/list?path=/docs")
                expect(folder.status_code == 200, folder.text)
                names = [item["name"] for item in folder.json()["entries"]]
                expect(names == ["hello.txt", "note.txt"], str(names))
                hello = folder.json()["entries"][0]
                hello_stat = (root / "docs" / "hello.txt").stat()
                expect(
                    hello["owner"] == str(hello_stat.st_uid)
                    and hello["group"] == str(hello_stat.st_gid),
                    str(hello),
                )

                downloaded = request(
                    "POST",
                    f"/api/sessions/{session_id}/download",
                    json={"paths": ["/docs/hello.txt"]},
                )
                expect(downloaded.status_code == 200, downloaded.text)
                expect(downloaded.content == b"hello remote", "download bytes mismatch")
                expect("inline" in (downloaded.headers.get("content-disposition") or ""), downloaded.headers.get("content-disposition"))
                from app import downloads as download_history
                sample = home / "saved.txt"
                sample.write_text("ok", encoding="utf-8")
                download_history.remember("saved.txt", str(sample), 2)
                listed = request("GET", "/api/downloads")
                expect(listed.status_code == 200, listed.text)
                expect(any(item.get("path") == str(sample) for item in listed.json()["items"]), listed.text)
                removed = request(
                    "POST",
                    "/api/downloads/delete",
                    json={"path": str(sample), "savedAt": listed.json()["items"][0]["savedAt"]},
                )
                expect(removed.status_code == 200, removed.text)
                expect(not sample.exists(), "saved file should be removed")
                after = request("GET", "/api/downloads")
                expect(all(item.get("path") != str(sample) for item in after.json()["items"]), after.text)
                rejected = request(
                    "POST",
                    f"/api/sessions/{session_id}/download",
                    json={"paths": ["/docs/hello.txt"], "saveToken": "missing-token"},
                )
                expect(rejected.status_code == 400, rejected.text)
                wide = b"w" * (3 * 1024 * 1024)
                (root / "docs" / "wide.bin").write_bytes(wide)
                wide_download = request(
                    "POST",
                    f"/api/sessions/{session_id}/download",
                    json={"paths": ["/docs/wide.bin"]},
                )
                expect(wide_download.status_code == 200, wide_download.text[:200])
                expect(wide_download.content == wide, "large download bytes mismatch")
                packed = request(
                    "POST",
                    f"/api/sessions/{session_id}/download",
                    json={"paths": ["/docs"]},
                )
                expect(packed.status_code == 200, packed.text[:200])
                expect("docs.zip" in (packed.headers.get("content-disposition") or ""), packed.headers.get("content-disposition"))
                with zipfile.ZipFile(io.BytesIO(packed.content)) as bundle:
                    expect(bundle.read("docs/hello.txt") == b"hello remote", str(bundle.namelist()[:8]))
                    expect("docs/note.txt" in bundle.namelist(), str(bundle.namelist()))
                expect(["tar-cf", "/docs"] in stub.commands, str(stub.commands))

                opened_local = request(
                    "POST",
                    f"/api/sessions/{session_id}/open-local",
                    json={"path": "/docs/hello.txt"},
                )
                expect(opened_local.status_code == 200, opened_local.text)
                record = json.loads((home / "open.json").read_text(encoding="utf-8"))
                expect(record["action"] == "open", str(record))
                expect(record["path"].endswith("hello.txt"), str(record))
                Path(record["path"]).write_bytes(b"edited-locally")
                expect(push_pending() == 1, "local edit was not uploaded")
                expect((root / "docs" / "hello.txt").read_bytes() == b"edited-locally", "remote bytes were not updated")
                notes = request("GET", "/api/notices")
                expect(notes.status_code == 200, notes.text)
                expect("已写回远程" in notes.text, notes.text)

                chosen = request(
                    "POST",
                    f"/api/sessions/{session_id}/open-local",
                    json={"path": "/docs/hello.txt", "choose": True},
                )
                expect(chosen.status_code == 200, chosen.text)
                record = json.loads((home / "open.json").read_text(encoding="utf-8"))
                expect(record["action"] == "choose", str(record))
                folder_open = request(
                    "POST",
                    f"/api/sessions/{session_id}/open-local",
                    json={"path": "/docs"},
                )
                expect(folder_open.status_code == 400, folder_open.text)

                renamed = request(
                    "POST",
                    f"/api/sessions/{session_id}/rename",
                    json={"path": "/docs/hello.txt", "newName": "readme.txt"},
                )
                expect(renamed.status_code == 200, renamed.text)
                expect((root / "docs" / "readme.txt").is_file(), "rename failed")

                (root / "docs" / "nested").mkdir()
                (root / "docs" / "nested" / "leaf.txt").write_text("x", encoding="utf-8")
                removed = request(
                    "POST",
                    f"/api/sessions/{session_id}/delete",
                    json={"paths": ["/docs"]},
                )
                expect(removed.status_code == 200, removed.text)
                expect(not (root / "docs").exists(), "delete failed")
                expect(["/docs"] in stub.commands, str(stub.commands))

                root_delete = request(
                    "POST",
                    f"/api/sessions/{session_id}/delete",
                    json={"paths": ["/"]},
                )
                expect(root_delete.status_code == 400, root_delete.text)

                other = request(
                    "POST",
                    "/api/sessions",
                    json={
                        "name": "other",
                        "host": "127.0.0.1",
                        "port": stub.port,
                        "username": "user",
                        "auth": "password",
                        "rememberPassword": False,
                    },
                )
                expect(other.status_code == 200, other.text)
                other_id = other.json()["id"]
                other_connected = request(
                    "POST",
                    f"/api/sessions/{other_id}/connect",
                    json={"password": "s3cret-token", "remember": False},
                )
                expect(other_connected.status_code == 200, other_connected.text)
                expect(pool.connected(session_id), "opening another session closed the first")
                expect(pool.connected(other_id), "second session did not stay open")
                kept = request(
                    "POST",
                    f"/api/sessions/{session_id}/upload",
                    data={"dest": "/", "relatives": json.dumps(["marker.txt"])},
                    files=[("files", ("marker.txt", b"m", "text/plain"))],
                )
                expect(kept.status_code == 200, kept.text)
                expect((root / "marker.txt").read_bytes() == b"m", "upload on the first session stopped")
                pool.disconnect(other_id)
                pool.disconnect(session_id)
        finally:
            stub.close()
    print("OK")


if __name__ == "__main__":
    main()
