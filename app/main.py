from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles as StarletteStaticFiles

from app import downloads, keys, notices, opener, pick_file, store, sudo, terminal, transfer
from app.remote_path import safe_relative
from app.pool import (
    AuthFailed,
    AuthRequired,
    ConnectionDropped,
    ConnectionFailed,
    HostKeyChanged,
    NotConnected,
    pool,
)

def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[1]


STATIC = resource_root() / "static"
SID_RE = re.compile(r"^[a-f0-9]{32}$")
app = FastAPI(title="remote-folder")


class APIError(Exception):
    def __init__(self, status: int, message: str, **extra) -> None:
        self.status = status
        self.message = message
        self.extra = extra


class SessionBody(BaseModel):
    name: str = ""
    host: str
    port: int = 22
    username: str
    auth: str = "password"
    keyPath: str = ""
    password: str | None = None
    passphrase: str | None = None
    rememberPassword: bool = True
    rememberPassphrase: bool = True


class ConnectBody(BaseModel):
    password: str | None = None
    passphrase: str | None = None
    remember: bool = False
    trustHost: bool = False


class SudoBody(BaseModel):
    method: Literal["sudo", "su"] = "sudo"
    password: str | None = None
    remember: bool = True
    enable: bool = True


class MkdirBody(BaseModel):
    parent: str
    name: str


class RenameBody(BaseModel):
    path: str
    newName: str


class PathsBody(BaseModel):
    paths: list[str]
    saveToken: str = ""


class OpenLocalBody(BaseModel):
    path: str
    choose: bool = False


class TerminalBody(BaseModel):
    path: str = ""


class NoCacheStatic(StarletteStaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@app.middleware("http")
async def reject_cross_site(request, call_next):
    if request.url.path.startswith("/api/") and request.headers.get("x-rf-request") != "1":
        return JSONResponse({"error": "拒绝跨站请求"}, status_code=403)
    return await call_next(request)


@app.exception_handler(APIError)
async def handle_api_error(_, exc: APIError):
    return JSONResponse({"error": exc.message, **exc.extra}, status_code=exc.status)


def _session_or_404(session_id: str) -> dict:
    if not SID_RE.fullmatch(session_id):
        raise APIError(404, "连接不存在")
    session = store.get_session(session_id)
    if session is None:
        raise APIError(404, "连接不存在")
    return session


def _public(session: dict) -> dict:
    data = store.public_session(session, pool.connected(session["id"]))
    data["elevationMethod"] = pool.elevation_method(session["id"])
    data["sudo"] = data["elevationMethod"] is not None
    return data


def _run(session_id: str, fn):
    return _invoke(session_id, lambda: pool.call(session_id, fn))


def _invoke(session_id: str, fn):
    try:
        return fn()
    except NotConnected:
        raise APIError(409, "还没有连接这台主机", needConnect=True)
    except ConnectionDropped as exc:
        raise APIError(409, str(exc), needConnect=True)
    except ValueError as exc:
        raise APIError(400, str(exc))
    except FileNotFoundError:
        raise APIError(404, "远程路径不存在")
    except OSError as exc:
        if sudo.permission_denied(exc) and not pool.sudo_enabled(session_id):
            raise APIError(403, "没有权限打开这个位置", permissionDenied=True)
        raise APIError(400, f"远程操作失败：{exc}")


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _finish_download(path: str, session_id: str) -> None:
    transfer.clear_activity(session_id)
    _unlink(path)


def _copy_download(source: str, dest: str, progress) -> int:
    total = os.path.getsize(source)
    done = 0
    folder = os.path.dirname(dest)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(source, "rb") as incoming, open(dest, "wb") as outgoing:
        while True:
            chunk = incoming.read(1024 * 1024)
            if not chunk:
                break
            outgoing.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return done


@app.get("/api/downloads")
def download_history():
    return {"items": downloads.list_items()}


class RevealBody(BaseModel):
    path: str


class ForgetDownloadBody(BaseModel):
    path: str
    savedAt: str = ""


@app.post("/api/downloads/reveal")
def reveal_download(body: RevealBody):
    path = os.path.abspath(body.path)
    if not os.path.exists(path):
        raise APIError(404, "文件已经不在原来的位置")
    if os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])
    return {"ok": True}


@app.post("/api/downloads/delete")
def delete_download_record(body: ForgetDownloadBody):
    try:
        downloads.forget(body.path, body.savedAt)
    except ValueError as exc:
        raise APIError(404, str(exc))
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/pick-file")
def choose_local_file():
    return {"path": pick_file.pick_file()}


@app.get("/api/sessions")
def get_sessions():
    return [_public(session) for session in store.list_sessions()]


@app.post("/api/sessions")
def create_session(body: SessionBody):
    try:
        session = store.save_session(body.model_dump())
    except ValueError as exc:
        raise APIError(400, str(exc))
    except RuntimeError as exc:
        raise APIError(400, str(exc))
    return _public(session)


@app.put("/api/sessions/{session_id}")
def update_session(session_id: str, body: SessionBody):
    _session_or_404(session_id)
    try:
        session = store.save_session(body.model_dump(), session_id)
    except KeyError:
        raise APIError(404, "连接不存在")
    except ValueError as exc:
        raise APIError(400, str(exc))
    except RuntimeError as exc:
        raise APIError(400, str(exc))
    opener.flush_session(session_id)
    opener.drop_session(session_id)
    pool.disconnect(session_id)
    return _public(session)


@app.delete("/api/sessions/{session_id}")
def remove_session(session_id: str):
    _session_or_404(session_id)
    opener.flush_session(session_id)
    opener.drop_session(session_id)
    pool.disconnect(session_id)
    store.delete_session(session_id)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/connect")
def connect_session(session_id: str, body: ConnectBody):
    session = _session_or_404(session_id)
    password = body.password or store.get_secret(session_id, "password")
    passphrase = body.passphrase or store.get_secret(session_id, "passphrase")
    try:
        if body.password and body.remember:
            store.set_secret(session_id, "password", body.password)
        if body.passphrase and body.remember:
            store.set_secret(session_id, "passphrase", body.passphrase)
        home = pool.connect(session, password, passphrase, body.trustHost)
    except AuthRequired as exc:
        raise APIError(401, str(exc), authRequired=True, secretKind=exc.kind)
    except AuthFailed as exc:
        raise APIError(401, str(exc), authRequired=True, secretKind="password" if session.get("auth") != "key" else "passphrase")
    except HostKeyChanged as exc:
        raise APIError(409, str(exc), hostKeyChanged=True)
    except ConnectionFailed as exc:
        raise APIError(400, str(exc))
    except ValueError as exc:
        raise APIError(400, str(exc))
    except RuntimeError as exc:
        raise APIError(400, str(exc))
    return {"ok": True, "home": home}


@app.post("/api/sessions/{session_id}/sudo")
def set_sudo(session_id: str, body: SudoBody):
    _session_or_404(session_id)
    if not body.enable:
        try:
            pool.disable_sudo(session_id)
        except (NotConnected, ConnectionDropped):
            raise APIError(409, "还没有连接这台主机", needConnect=True)
        return {"ok": True, "sudo": False, "elevationMethod": None, "message": "已返回登录账号"}
    password = body.password or ""
    if body.method == "su" and not password:
        raise APIError(401, "请输入 root 密码", sudoPassword=True)
    if body.method == "sudo" and not password:
        password = store.get_secret(session_id, "sudo") or ""
    try:
        pool.enable_sudo(session_id, password, method=body.method)
    except (NotConnected, ConnectionDropped):
        raise APIError(409, "还没有连接这台主机", needConnect=True)
    except AuthFailed as exc:
        raise APIError(401, str(exc), sudoPassword=True)
    except ConnectionFailed as exc:
        raise APIError(400, str(exc))
    except ValueError as exc:
        raise APIError(400, str(exc))
    if body.method == "sudo" and body.password:
        if body.remember:
            store.set_secret(session_id, "sudo", body.password)
        else:
            store.delete_secret(session_id, "sudo")
    return {"ok": True, "sudo": True, "elevationMethod": body.method, "message": "已启用管理员文件操作"}


@app.post("/api/sessions/{session_id}/install-key")
def install_key(session_id: str):
    _session_or_404(session_id)
    try:
        private, line = keys.ensure_key()
        installed = _run(session_id, lambda live: keys.install_public_key(live.user_sftp, live.home, line))
        session = store.use_key_auth(session_id, str(private))
    except KeyError:
        raise APIError(404, "连接不存在")
    except ValueError as exc:
        raise APIError(400, str(exc))
    except OSError as exc:
        raise APIError(400, f"无法配置密钥：{exc}")
    note = "已把公钥写到这台主机。" if installed else "公钥已经在这台主机上。"
    return {
        **_public(session),
        "installed": installed,
        "message": note + "这个连接之后会用密钥登录。",
    }


@app.post("/api/sessions/{session_id}/disconnect")
def disconnect_session(session_id: str):
    _session_or_404(session_id)
    opener.flush_session(session_id)
    transfer.clear_folder_sizes(session_id)
    pool.disconnect(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/list")
def list_directory(session_id: str, path: str = ""):
    session = _session_or_404(session_id)

    def work(live):
        return transfer.list_directory(live.sftp, path or live.home)

    data = _run(session_id, work)
    store.set_last_path(session["id"], data["path"])
    return data


@app.get("/api/sessions/{session_id}/size")
def folder_size(session_id: str, path: str):
    _session_or_404(session_id)
    return _invoke(session_id, lambda: pool.scan_size(session_id, path))


@app.post("/api/sessions/{session_id}/mkdir")
def mkdir(session_id: str, body: MkdirBody):
    _session_or_404(session_id)
    return _run(session_id, lambda live: transfer.make_directory(live.sftp, body.parent, body.name))


@app.post("/api/sessions/{session_id}/mkfile")
def mkfile(session_id: str, body: MkdirBody):
    _session_or_404(session_id)
    return _run(session_id, lambda live: transfer.make_file(live.sftp, body.parent, body.name))


@app.post("/api/sessions/{session_id}/rename")
def rename(session_id: str, body: RenameBody):
    _session_or_404(session_id)
    return _run(session_id, lambda live: transfer.rename_path(live.sftp, body.path, body.newName))


@app.post("/api/sessions/{session_id}/delete")
def delete(session_id: str, body: PathsBody):
    _session_or_404(session_id)
    def run(live):
        return transfer.delete_paths(
            live.sftp,
            body.paths,
            client=None if live.elevation_method == "su" else live.client,
            as_root=live.elevation_method == "sudo",
            sudo_password=live.sudo_password,
        )

    return _run(session_id, run)


def _upload_limit_message(detail: object) -> str:
    text = detail if isinstance(detail, str) else ""
    if "Too many files" in text:
        return "一次提交的文件过多。"
    if "maximum size" in text.lower():
        return "文件路径列表过长。"
    return text or "上传失败"


def _upload_paths(raw: object) -> list[str]:
    if not isinstance(raw, str):
        raise APIError(400, "上传路径无效")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise APIError(400, "上传路径无效")
    if not isinstance(parsed, list) or not all(isinstance(path, str) for path in parsed):
        raise APIError(400, "上传路径无效")
    if len(parsed) > transfer.MAX_UPLOAD_ITEMS:
        raise APIError(400, f"一次最多上传 {transfer.MAX_UPLOAD_ITEMS} 个项目")
    try:
        return [safe_relative(path) for path in parsed]
    except ValueError as exc:
        raise APIError(400, str(exc))


@app.post("/api/sessions/{session_id}/upload")
async def upload(session_id: str, request: Request):
    session = _session_or_404(session_id)
    try:
        form = await request.form(max_files=transfer.MAX_UPLOAD_ITEMS, max_fields=32, max_part_size=32 * 1024 * 1024)
    except StarletteHTTPException as exc:
        raise APIError(400, _upload_limit_message(exc.detail))
    dest = form.get("dest")
    if not isinstance(dest, str):
        raise APIError(400, "上传路径无效")
    files = [item for item in form.getlist("files") if hasattr(item, "file")]
    safe_paths = _upload_paths(form.get("relatives", "[]"))
    directories = _upload_paths(form.get("directories", "[]"))
    if len(files) != len(safe_paths):
        raise APIError(400, "上传文件和路径数量不一致")
    if not files and not directories:
        raise APIError(400, "没有选择文件或目录")
    if len(files) + len(directories) > transfer.MAX_UPLOAD_ITEMS:
        raise APIError(400, f"一次最多上传 {transfer.MAX_UPLOAD_ITEMS} 个项目")

    def generate():
        events: queue.Queue = queue.Queue()

        def worker() -> None:
            try:
                events.put({"phase": "connect", "message": "正在建立传输连接…"})
                with pool.transfer(session) as link:
                    for event in transfer.iter_upload(
                        link.sftp,
                        dest,
                        files,
                        safe_paths,
                        directories=directories,
                        client=link.client,
                        as_root=link.sudo,
                        sudo_password=link.sudo_password,
                    ):
                        events.put(event)
            except NotConnected:
                events.put({"error": "还没有连接这台主机", "needConnect": True})
            except ConnectionDropped as exc:
                events.put({"error": str(exc)})
            except (AuthFailed, AuthRequired, ConnectionFailed, HostKeyChanged, ValueError) as exc:
                events.put({"error": str(exc)})
            except FileNotFoundError:
                events.put({"error": "远程路径不存在"})
            except OSError as exc:
                if sudo.permission_denied(exc) and not pool.sudo_enabled(session_id):
                    events.put({"error": "没有权限打开这个位置", "permissionDenied": True})
                else:
                    events.put({"error": f"远程操作失败：{exc}"})
            except Exception as exc:
                events.put({"error": str(exc) or "上传失败"})
            finally:
                events.put(None)

        threading.Thread(target=worker, name="remote-folder-upload", daemon=True).start()
        while True:
            event = events.get()
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/api/sessions/{session_id}/activity")
def transfer_activity(session_id: str):
    _session_or_404(session_id)
    item = transfer.read_activity(session_id)
    if not item:
        return {"active": False}
    return {"active": True, **item}


@app.post("/api/sessions/{session_id}/download")
def download(session_id: str, body: PathsBody):
    session = _session_or_404(session_id)

    def on_progress(done: int, total: int, name: str, message: str = "") -> None:
        if not message:
            message = f"正在下载 {name}" if name else "正在下载…"
        transfer.note_activity(session_id, done, total, name, message)

    try:
        destination = downloads.take(body.saveToken) if body.saveToken else ""
    except ValueError as exc:
        raise APIError(400, str(exc))
    try:
        transfer.note_activity(session_id, 0, 0, "", "正在建立传输连接…")
        with pool.transfer(session) as link:
            local_path, filename, media = transfer.materialize(
                link.sftp,
                body.paths,
                on_progress,
                client=link.client,
                as_root=link.sudo,
                sudo_password=link.sudo_password,
            )
    except NotConnected:
        transfer.clear_activity(session_id)
        raise APIError(409, "还没有连接这台主机", needConnect=True)
    except ConnectionDropped as exc:
        transfer.clear_activity(session_id)
        raise APIError(400, str(exc))
    except (AuthFailed, AuthRequired, ConnectionFailed, HostKeyChanged, ValueError) as exc:
        transfer.clear_activity(session_id)
        raise APIError(400, str(exc))
    except FileNotFoundError:
        transfer.clear_activity(session_id)
        raise APIError(404, "远程路径不存在")
    except OSError as exc:
        transfer.clear_activity(session_id)
        if sudo.permission_denied(exc) and not pool.sudo_enabled(session_id):
            raise APIError(403, "没有权限打开这个位置", permissionDenied=True)
        raise APIError(400, f"远程操作失败：{exc}")
    if destination:
        try:
            def on_copy(done: int, total: int) -> None:
                transfer.note_activity(session_id, done, total, filename, "正在保存到本机…")

            size = _copy_download(local_path, destination, on_copy)
        except OSError as exc:
            _unlink(local_path)
            _unlink(destination)
            transfer.clear_activity(session_id)
            raise APIError(400, f"保存到本机失败：{exc}")
        _unlink(local_path)
        transfer.clear_activity(session_id)
        saved_name = os.path.basename(destination) or filename
        downloads.remember(saved_name, destination, size)
        return {"ok": True, "path": destination, "name": saved_name, "bytes": size}
    return FileResponse(
        local_path,
        filename=filename,
        media_type=media,
        content_disposition_type="inline",
        background=BackgroundTask(_finish_download, local_path, session_id),
    )


@app.get("/api/notices")
def list_notices():
    return {"notices": notices.drain()}


@app.post("/api/sessions/{session_id}/terminal")
def open_terminal(session_id: str, body: TerminalBody):
    session = _session_or_404(session_id)
    try:
        return terminal.open_bash(session_id, session, body.path)
    except NotConnected:
        raise APIError(409, "还没有连接这台主机", needConnect=True)
    except ConnectionDropped as exc:
        raise APIError(409, str(exc), needConnect=True)
    except ValueError as exc:
        raise APIError(400, str(exc))
    except FileNotFoundError:
        raise APIError(404, "远程路径不存在")
    except OSError as exc:
        raise APIError(400, str(exc))


@app.post("/api/sessions/{session_id}/open-local")
def open_local(session_id: str, body: OpenLocalBody):
    _session_or_404(session_id)
    try:
        return opener.open_cached(session_id, body.path, body.choose)
    except NotConnected:
        raise APIError(409, "还没有连接这台主机", needConnect=True)
    except ConnectionDropped as exc:
        raise APIError(409, str(exc), needConnect=True)
    except ValueError as exc:
        raise APIError(400, str(exc))
    except FileNotFoundError:
        raise APIError(404, "远程路径不存在")
    except OSError as exc:
        raise APIError(400, str(exc))


app.mount("/static", NoCacheStatic(directory=STATIC), name="static")
