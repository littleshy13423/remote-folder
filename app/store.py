from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from app.home import get_home

SERVICE = "remote-folder"
_lock = threading.RLock()


def _sessions_path():
    return get_home() / "sessions.json"


def _read_json(path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _keyring():
    try:
        import keyring
    except Exception:
        return None
    return keyring


def _secret_key(session_id: str, kind: str) -> str:
    return f"{session_id}:{kind}"


def get_secret(session_id: str, kind: str) -> str | None:
    keyring = _keyring()
    if keyring is None:
        return None
    try:
        return keyring.get_password(SERVICE, _secret_key(session_id, kind))
    except Exception:
        return None


def set_secret(session_id: str, kind: str, value: str) -> None:
    keyring = _keyring()
    if keyring is None:
        raise RuntimeError("无法写入系统凭据管理器，密码不会保存")
    keyring.set_password(SERVICE, _secret_key(session_id, kind), value)


def delete_secret(session_id: str, kind: str) -> None:
    keyring = _keyring()
    if keyring is None:
        return
    try:
        keyring.delete_password(SERVICE, _secret_key(session_id, kind))
    except Exception:
        pass


def _clean_token(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"请填写{label}")
    if any(char.isspace() or char in "\"'#\\" for char in text):
        raise ValueError(f"{label}包含无效字符")
    return text


def _clean_key_path(value: str) -> str:
    text = value.strip()
    if any(char in text for char in "\r\n"):
        raise ValueError("私钥路径无效")
    return text


def _normalize(data: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    auth = data.get("auth") or "password"
    if auth not in ("password", "key", "agent"):
        raise ValueError("认证方式无效")
    port = int(data.get("port") or 22)
    if port < 1 or port > 65535:
        raise ValueError("端口无效")
    host = _clean_token(str(data.get("host") or ""), "主机")
    username = _clean_token(str(data.get("username") or ""), "用户名")
    name = str(data.get("name") or "").strip() or f"{username}@{host}"
    if any(char in name for char in "\r\n"):
        raise ValueError("名称不能换行")
    session_id = existing["id"] if existing else uuid.uuid4().hex
    return {
        "id": session_id,
        "name": name[:80],
        "host": host,
        "port": port,
        "username": username,
        "auth": auth,
        "keyPath": _clean_key_path(str(data.get("keyPath") or "")),
        "lastPath": existing.get("lastPath", "") if existing else "",
    }


def _apply_secrets(session_id: str, data: dict[str, Any], is_update: bool) -> None:
    _apply_one(
        session_id,
        "password",
        data.get("password"),
        bool(data.get("rememberPassword", True)),
        is_update,
    )
    _apply_one(
        session_id,
        "passphrase",
        data.get("passphrase"),
        bool(data.get("rememberPassphrase", True)),
        is_update,
    )


def _apply_one(session_id: str, kind: str, value: Any, remember: bool, is_update: bool) -> None:
    if not remember:
        delete_secret(session_id, kind)
        return
    if isinstance(value, str) and value:
        set_secret(session_id, kind, value)
    elif not is_update:
        delete_secret(session_id, kind)


def list_sessions() -> list[dict[str, Any]]:
    with _lock:
        data = _read_json(_sessions_path(), [])
    return data if isinstance(data, list) else []


def get_session(session_id: str) -> dict[str, Any] | None:
    for session in list_sessions():
        if session.get("id") == session_id:
            return session
    return None


def save_session(data: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
    with _lock:
        sessions = list(_read_json(_sessions_path(), []))
        existing = None
        if existing_id:
            for session in sessions:
                if session.get("id") == existing_id:
                    existing = session
                    break
            if existing is None:
                raise KeyError(existing_id)
        session = _normalize(data, existing)
        _apply_secrets(session["id"], data, existing is not None)
        if existing is None:
            sessions.append(session)
        else:
            sessions = [session if item.get("id") == session["id"] else item for item in sessions]
        _write_json(_sessions_path(), sessions)
        return session


def delete_session(session_id: str) -> None:
    with _lock:
        sessions = [item for item in _read_json(_sessions_path(), []) if item.get("id") != session_id]
        _write_json(_sessions_path(), sessions)
    delete_secret(session_id, "password")
    delete_secret(session_id, "passphrase")
    delete_secret(session_id, "sudo")


def use_key_auth(session_id: str, key_path: str) -> dict[str, Any]:
    path = _clean_key_path(key_path)
    if not path:
        raise ValueError("私钥路径无效")
    with _lock:
        sessions = _read_json(_sessions_path(), [])
        found = None
        for session in sessions:
            if session.get("id") == session_id:
                session["auth"] = "key"
                session["keyPath"] = path
                found = session
                break
        if found is None:
            raise KeyError(session_id)
        _write_json(_sessions_path(), sessions)
        return found


def set_last_path(session_id: str, path: str) -> None:
    with _lock:
        sessions = _read_json(_sessions_path(), [])
        changed = False
        for session in sessions:
            if session.get("id") == session_id and session.get("lastPath") != path:
                session["lastPath"] = path
                changed = True
        if changed:
            _write_json(_sessions_path(), sessions)


def public_session(session: dict[str, Any], connected: bool) -> dict[str, Any]:
    session_id = session["id"]
    return {
        "id": session_id,
        "name": session.get("name") or "",
        "host": session.get("host") or "",
        "port": session.get("port") or 22,
        "username": session.get("username") or "",
        "auth": session.get("auth") or "password",
        "keyPath": session.get("keyPath") or "",
        "hasPassword": get_secret(session_id, "password") is not None,
        "hasPassphrase": get_secret(session_id, "passphrase") is not None,
        "lastPath": session.get("lastPath") or "",
        "connected": connected,
    }

