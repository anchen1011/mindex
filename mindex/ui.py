from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
import getpass
import hashlib
import hmac
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Iterable
from urllib.parse import urlparse
import uuid

from mindex.github_workflow import get_current_branch
from mindex.launcher import find_project_root
from mindex.task_queue import AgentManager, AgentRecord, QueueRecord, StateStore, TaskQueueManager, TaskRecord, utc_now


MAX_REQUEST_BYTES = 65536
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_LOGIN_WINDOW_SECONDS = 5 * 60
DEFAULT_LOGIN_ATTEMPTS = 5
PASSWORD_ITERATIONS = 390000
COOKIE_NAME = "mindex_session"
DEV_OVERRIDE_ENV = "MINDEX_UI_EPHEMERAL_OVERRIDES"
DEFAULT_DEV_POLL_SECONDS = 0.5
SESSION_DONE_PREFIX = "__MINDEX_TASK_DONE__"
DEFAULT_SESSION_COMMAND = ("/bin/bash", "--noprofile", "--norc")


@dataclass(frozen=True)
class UiConfig:
    project_root: Path
    host: str
    port: int
    title: str
    username: str
    password_hash: str
    password_salt: str
    password_iterations: int
    session_secret: str
    session_ttl_seconds: int
    login_window_seconds: int
    login_attempts: int
    state_file: Path
    queue_log_dir: Path
    allow_remote: bool
    disable_origin_checks: bool
    disable_csrf_checks: bool
    allowed_origins: tuple[str, ...]
    config_path: Path


@dataclass(frozen=True)
class UiBootstrap:
    config: UiConfig
    generated_password: str | None = None
    migrated_legacy_config: bool = False


@dataclass(frozen=True)
class SessionRecord:
    token: str
    username: str
    csrf_token: str
    expires_at: float


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_private_directory(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _hash_password(password: str, *, salt: bytes, iterations: int = PASSWORD_ITERATIONS) -> str:
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return derived.hex()


def _build_config_payload(
    *,
    project_root: Path,
    config_path: Path,
    username: str,
    password: str,
    host: str,
    port: int,
    title: str,
    state_file: Path,
    queue_log_dir: Path,
    allow_remote: bool,
    disable_origin_checks: bool = False,
    disable_csrf_checks: bool = False,
    allowed_origins: list[str] | None = None,
) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    session_secret = secrets.token_hex(32)
    return {
        "project_root": str(project_root),
        "auth": {
            "username": username,
            "password_hash": _hash_password(password, salt=salt),
            "password_salt": salt.hex(),
            "password_iterations": PASSWORD_ITERATIONS,
            "session_secret": session_secret,
            "session_ttl_seconds": DEFAULT_SESSION_TTL_SECONDS,
            "login_attempts": DEFAULT_LOGIN_ATTEMPTS,
            "login_window_seconds": DEFAULT_LOGIN_WINDOW_SECONDS,
        },
        "server": {
            "host": host,
            "port": port,
            "allow_remote": allow_remote,
            "disable_origin_checks": disable_origin_checks,
            "disable_csrf_checks": disable_csrf_checks,
            "allowed_origins": allowed_origins or [],
        },
        "storage": {
            "state_file": str(state_file),
            "queue_log_dir": str(queue_log_dir),
        },
        "ui": {
            "title": title,
        },
        "_meta": {
            "config_path": str(config_path),
        },
    }


def _default_ui_paths(project_root: Path) -> tuple[Path, Path, Path]:
    return (
        project_root / ".mindex" / "ui_config.json",
        project_root / ".mindex" / "task_queues.json",
        project_root / ".mindex" / "queue_logs",
    )


def _normalize_origin_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if "%" in normalized:
        normalized = normalized.split("%", 1)[0]
    return normalized


def _origin_for_host(host: str, port: int) -> str | None:
    normalized = _normalize_origin_host(host)
    if not normalized:
        return None
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return f"http://{normalized}:{port}"
    if ip.version == 6:
        return f"http://[{normalized}]:{port}"
    return f"http://{normalized}:{port}"


def _discover_origin_hosts(host: str, *, allow_remote: bool) -> tuple[str, ...]:
    candidates = {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    normalized_host = _normalize_origin_host(host)
    if normalized_host and normalized_host not in {"0.0.0.0", "::"}:
        candidates.add(normalized_host)
    if allow_remote or normalized_host in {"0.0.0.0", "::"}:
        for name in (socket.gethostname(), socket.getfqdn()):
            normalized_name = _normalize_origin_host(name)
            if normalized_name:
                candidates.add(normalized_name)
        for name in tuple(candidates):
            try:
                entries = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
            except OSError:
                continue
            for entry in entries:
                address = entry[4][0]
                normalized_address = _normalize_origin_host(address)
                if normalized_address:
                    candidates.add(normalized_address)
    return tuple(sorted(candidates))


def _normalize_allowed_origins(host: str, port: int, explicit: list[str], *, allow_remote: bool) -> tuple[str, ...]:
    defaults = []
    for candidate in _discover_origin_hosts(host, allow_remote=allow_remote):
        origin = _origin_for_host(candidate, port)
        if origin:
            defaults.append(origin)
    return tuple(dict.fromkeys([*defaults, *[value.strip() for value in explicit if value.strip()]]))


def load_or_create_ui_config(
    *,
    project_root: Path | str,
    config_path: Path | str | None = None,
    username: str = "admin",
    password: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    title: str = "Mindex Control Deck",
    allow_remote: bool = False,
    disable_origin_checks: bool = False,
    disable_csrf_checks: bool = False,
) -> UiBootstrap:
    resolved_root = Path(project_root).resolve()
    default_config_path, default_state_file, default_queue_log_dir = _default_ui_paths(resolved_root)
    resolved_config_path = Path(config_path).resolve() if config_path else default_config_path
    if not resolved_config_path.exists():
        configured_password = password or secrets.token_urlsafe(16)
        payload = _build_config_payload(
            project_root=resolved_root,
            config_path=resolved_config_path,
            username=username,
            password=configured_password,
            host=host,
            port=port,
            title=title,
            state_file=default_state_file,
            queue_log_dir=default_queue_log_dir,
            allow_remote=allow_remote,
            disable_origin_checks=disable_origin_checks,
            disable_csrf_checks=disable_csrf_checks,
        )
        _write_private_json(resolved_config_path, payload)
        return UiBootstrap(
            config=_parse_ui_config(payload, resolved_config_path),
            generated_password=None if password else configured_password,
        )

    payload = json.loads(resolved_config_path.read_text(encoding="utf-8"))
    migrated = False
    auth_payload = payload.setdefault("auth", {})
    server_payload = payload.setdefault("server", {})
    storage_payload = payload.setdefault("storage", {})
    ui_payload = payload.setdefault("ui", {})

    if "password_hash" not in auth_payload:
        legacy_password = auth_payload.pop("password", None)
        if not legacy_password:
            legacy_password = password or secrets.token_urlsafe(16)
        salt = secrets.token_bytes(16)
        auth_payload["password_hash"] = _hash_password(str(legacy_password), salt=salt)
        auth_payload["password_salt"] = salt.hex()
        auth_payload["password_iterations"] = PASSWORD_ITERATIONS
        auth_payload.setdefault("username", username)
        migrated = True

    if "session_secret" not in auth_payload:
        auth_payload["session_secret"] = secrets.token_hex(32)
        migrated = True
    if "session_ttl_seconds" not in auth_payload:
        auth_payload["session_ttl_seconds"] = DEFAULT_SESSION_TTL_SECONDS
        migrated = True
    if "login_attempts" not in auth_payload:
        auth_payload["login_attempts"] = DEFAULT_LOGIN_ATTEMPTS
        migrated = True
    if "login_window_seconds" not in auth_payload:
        auth_payload["login_window_seconds"] = DEFAULT_LOGIN_WINDOW_SECONDS
        migrated = True
    if "password_iterations" not in auth_payload:
        auth_payload["password_iterations"] = PASSWORD_ITERATIONS
        migrated = True

    if "project_root" not in payload:
        payload["project_root"] = str(resolved_root)
        migrated = True
    storage_payload.setdefault("state_file", str(default_state_file))
    storage_payload.setdefault("queue_log_dir", str(default_queue_log_dir))
    ui_payload.setdefault("title", title)

    host_value = str(server_payload.get("host", host))
    if host_value == "0.0.0.0" and "allow_remote" not in server_payload:
        server_payload["host"] = "127.0.0.1"
        migrated = True
    if "host" not in server_payload:
        server_payload["host"] = host
        migrated = True
    if "port" not in server_payload:
        server_payload["port"] = port
        migrated = True
    if "allow_remote" not in server_payload:
        server_payload["allow_remote"] = allow_remote
        migrated = True
    if "disable_origin_checks" not in server_payload:
        server_payload["disable_origin_checks"] = disable_origin_checks
        migrated = True
    if "disable_csrf_checks" not in server_payload:
        server_payload["disable_csrf_checks"] = disable_csrf_checks
        migrated = True
    if "allowed_origins" not in server_payload:
        server_payload["allowed_origins"] = []
        migrated = True

    if migrated:
        _write_private_json(resolved_config_path, payload)
    return UiBootstrap(config=_parse_ui_config(payload, resolved_config_path), migrated_legacy_config=migrated)


def _resolve_ui_config_path(project_root: Path, config_path: str | None) -> Path:
    if config_path:
        return Path(config_path).resolve()
    default_config_path, _, _ = _default_ui_paths(project_root)
    return default_config_path


def _password_prompt_needed(config_path: Path) -> bool:
    if not config_path.exists():
        return True
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    auth_payload = payload.get("auth", {})
    return "password_hash" not in auth_payload and not auth_payload.get("password")


def _prompt_for_password() -> str:
    while True:
        password = getpass.getpass("Admin password: ")
        if not password:
            print("Password cannot be empty.", file=sys.stderr)
            continue
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords did not match. Try again.", file=sys.stderr)
            continue
        return password


def reset_ui_config(
    *,
    project_root: Path | str,
    config_path: Path | str | None = None,
    username: str = "admin",
    password: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    title: str = "Mindex Control Deck",
    allow_remote: bool = False,
    disable_origin_checks: bool = False,
    disable_csrf_checks: bool = False,
) -> UiBootstrap:
    resolved_root = Path(project_root).resolve()
    default_config_path, default_state_file, default_queue_log_dir = _default_ui_paths(resolved_root)
    resolved_config_path = Path(config_path).resolve() if config_path else default_config_path
    configured_password = password or secrets.token_urlsafe(16)
    payload = _build_config_payload(
        project_root=resolved_root,
        config_path=resolved_config_path,
        username=username,
        password=configured_password,
        host=host,
        port=port,
        title=title,
        state_file=default_state_file,
        queue_log_dir=default_queue_log_dir,
        allow_remote=allow_remote,
        disable_origin_checks=disable_origin_checks,
        disable_csrf_checks=disable_csrf_checks,
    )
    _write_private_json(resolved_config_path, payload)
    return UiBootstrap(
        config=_parse_ui_config(payload, resolved_config_path),
        generated_password=None if password else configured_password,
    )


def _config_as_payload(config: UiConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key, value in list(payload.items()):
        if isinstance(value, Path):
            payload[key] = str(value)
    return payload


def _add_ui_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", help="Project root to manage; defaults to the detected workspace")
    parser.add_argument("--config", help="Override the UI config path")
    parser.add_argument("--username", default="admin", help="Admin username")
    parser.add_argument("--password", help="Admin password; if omitted, prompt interactively for init-config")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind; defaults to localhost")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind")
    parser.add_argument("--title", default="Mindex Control Deck", help="Browser title for the UI")
    parser.add_argument("--allow-remote", action="store_true", help="Allow non-localhost binding")
    parser.add_argument(
        "--disable-origin-checks",
        action="store_true",
        help="Disable Origin/Referer enforcement for authenticated UI requests",
    )
    parser.add_argument(
        "--disable-csrf-checks",
        action="store_true",
        help="Disable CSRF-token enforcement for authenticated state-changing UI requests",
    )


def _parse_ui_config(payload: dict[str, Any], config_path: Path) -> UiConfig:
    auth_payload = payload["auth"]
    server_payload = payload["server"]
    storage_payload = payload["storage"]
    project_root = Path(payload["project_root"]).resolve()
    host = str(server_payload.get("host", "127.0.0.1"))
    port = int(server_payload.get("port", 8765))
    allow_remote = bool(server_payload.get("allow_remote", False))
    disable_origin_checks = bool(server_payload.get("disable_origin_checks", False))
    disable_csrf_checks = bool(server_payload.get("disable_csrf_checks", False))
    if not allow_remote and host not in {"127.0.0.1", "localhost"}:
        raise ValueError("remote UI binding is disabled; set allow_remote=true explicitly to use a non-local host")
    return UiConfig(
        project_root=project_root,
        host=host,
        port=port,
        title=str(payload.get("ui", {}).get("title", "Mindex Control Deck")),
        username=str(auth_payload["username"]),
        password_hash=str(auth_payload["password_hash"]),
        password_salt=str(auth_payload["password_salt"]),
        password_iterations=int(auth_payload.get("password_iterations", PASSWORD_ITERATIONS)),
        session_secret=str(auth_payload["session_secret"]),
        session_ttl_seconds=int(auth_payload.get("session_ttl_seconds", DEFAULT_SESSION_TTL_SECONDS)),
        login_window_seconds=int(auth_payload.get("login_window_seconds", DEFAULT_LOGIN_WINDOW_SECONDS)),
        login_attempts=int(auth_payload.get("login_attempts", DEFAULT_LOGIN_ATTEMPTS)),
        state_file=Path(storage_payload["state_file"]).resolve(),
        queue_log_dir=Path(storage_payload["queue_log_dir"]).resolve(),
        allow_remote=allow_remote,
        disable_origin_checks=disable_origin_checks,
        disable_csrf_checks=disable_csrf_checks,
        allowed_origins=_normalize_allowed_origins(
            host,
            port,
            list(server_payload.get("allowed_origins", [])),
            allow_remote=allow_remote,
        ),
        config_path=config_path,
    )


class LoginProtector:
    def __init__(self, *, attempts: int, window_seconds: int) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, list[float]] = {}

    def register_failure(self, key: str) -> None:
        with self._lock:
            now = time.time()
            self._entries.setdefault(key, []).append(now)
            self._entries[key] = [value for value in self._entries[key] if value >= now - self.window_seconds]

    def clear(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def blocked(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            values = [value for value in self._entries.get(key, []) if value >= now - self.window_seconds]
            self._entries[key] = values
            return len(values) >= self.attempts


class SessionStore:
    def __init__(self, *, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRecord] = {}

    def create(self, username: str) -> SessionRecord:
        record = SessionRecord(
            token=secrets.token_urlsafe(32),
            username=username,
            csrf_token=secrets.token_urlsafe(24),
            expires_at=time.time() + self.ttl_seconds,
        )
        with self._lock:
            self._sessions[record.token] = record
        return record

    def get(self, token: str | None) -> SessionRecord | None:
        if not token:
            return None
        with self._lock:
            record = self._sessions.get(token)
            if record is None:
                return None
            if record.expires_at < time.time():
                self._sessions.pop(token, None)
                return None
            return record

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)


@dataclass
class ManagedSessionRecord:
    agent_id: str
    name: str
    workdir: str
    queue_id: str
    status: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    stopped_at: str | None = None
    current_task_id: str = ""
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ManagedSessionRecord":
        timestamp = str(payload.get("updated_at") or payload.get("created_at") or utc_now())
        status = str(payload.get("status", "stopped")).strip().lower() or "stopped"
        if status not in {"running", "stopped"}:
            status = "stopped"
        return cls(
            agent_id=str(payload.get("agent_id") or f"session-{uuid.uuid4().hex[:12]}"),
            name=str(payload.get("name", "Untitled session")),
            workdir=str(payload.get("workdir", "")),
            queue_id=str(payload.get("queue_id", "")),
            status=status,
            created_at=str(payload.get("created_at", timestamp)),
            updated_at=timestamp,
            started_at=payload.get("started_at"),
            stopped_at=payload.get("stopped_at"),
            current_task_id=str(payload.get("current_task_id", "")),
            last_error=payload.get("last_error"),
        )


@dataclass
class SessionMessageRecord:
    message_id: str
    agent_id: str
    task_id: str
    kind: str
    text: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, agent_id: str) -> "SessionMessageRecord":
        timestamp = str(payload.get("updated_at") or payload.get("created_at") or utc_now())
        kind = str(payload.get("kind", "output")).strip().lower() or "output"
        if kind not in {"input", "output", "system", "error"}:
            kind = "output"
        return cls(
            message_id=str(payload.get("message_id") or f"message-{uuid.uuid4().hex[:12]}"),
            agent_id=agent_id,
            task_id=str(payload.get("task_id", "")),
            kind=kind,
            text=str(payload.get("text", "")),
            created_at=str(payload.get("created_at", timestamp)),
            updated_at=timestamp,
        )


class ManagedSessionState:
    def __init__(self, store: StateStore) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._recover()

    def _load_state(self) -> dict[str, Any]:
        payload = self.store.load()
        payload.setdefault("managed_sessions", [])
        payload.setdefault("session_messages", {})
        return payload

    def _write_state(
        self,
        payload: dict[str, Any],
        *,
        sessions: list[ManagedSessionRecord] | None = None,
        messages: dict[str, list[SessionMessageRecord]] | None = None,
    ) -> None:
        if sessions is not None:
            payload["managed_sessions"] = [session.to_dict() for session in sessions]
        if messages is not None:
            payload["session_messages"] = {
                agent_id: [message.to_dict() for message in items]
                for agent_id, items in messages.items()
            }
        self.store.save(payload)

    def _read_sessions(self) -> list[ManagedSessionRecord]:
        payload = self._load_state()
        return [ManagedSessionRecord.from_dict(item) for item in payload.get("managed_sessions", [])]

    def _read_messages(self) -> dict[str, list[SessionMessageRecord]]:
        payload = self._load_state()
        raw = payload.get("session_messages", {})
        if not isinstance(raw, dict):
            return {}
        messages: dict[str, list[SessionMessageRecord]] = {}
        for agent_id, items in raw.items():
            if not isinstance(items, list):
                continue
            messages[str(agent_id)] = [SessionMessageRecord.from_dict(item, agent_id=str(agent_id)) for item in items]
        return messages

    def _recover(self) -> None:
        with self._lock:
            payload = self._load_state()
            sessions = [ManagedSessionRecord.from_dict(item) for item in payload.get("managed_sessions", [])]
            changed = False
            for session in sessions:
                if session.status != "running":
                    continue
                session.status = "stopped"
                session.last_error = "The UI restarted and detached from this live session."
                session.stopped_at = utc_now()
                session.updated_at = session.stopped_at
                changed = True
            if changed:
                self._write_state(payload, sessions=sessions)

    def list_sessions(self) -> list[ManagedSessionRecord]:
        with self._lock:
            return self._read_sessions()

    def get_session(self, agent_id: str) -> ManagedSessionRecord | None:
        with self._lock:
            for session in self._read_sessions():
                if session.agent_id == agent_id:
                    return session
        return None

    def create_session(self, *, name: str, workdir: str, queue_id: str) -> ManagedSessionRecord:
        timestamp = utc_now()
        session = ManagedSessionRecord(
            agent_id=f"session-{uuid.uuid4().hex[:12]}",
            name=name.strip() or "Untitled session",
            workdir=workdir,
            queue_id=queue_id,
            status="stopped",
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._lock:
            payload = self._load_state()
            sessions = [ManagedSessionRecord.from_dict(item) for item in payload.get("managed_sessions", [])]
            sessions.append(session)
            self._write_state(payload, sessions=sessions)
        return session

    def update_session(self, agent_id: str, **updates: Any) -> ManagedSessionRecord:
        with self._lock:
            payload = self._load_state()
            sessions = [ManagedSessionRecord.from_dict(item) for item in payload.get("managed_sessions", [])]
            for session in sessions:
                if session.agent_id != agent_id:
                    continue
                for key, value in updates.items():
                    if not hasattr(session, key):
                        continue
                    setattr(session, key, value)
                session.updated_at = utc_now()
                self._write_state(payload, sessions=sessions)
                return session
        raise KeyError(agent_id)

    def delete_session(self, agent_id: str) -> None:
        with self._lock:
            payload = self._load_state()
            sessions = [ManagedSessionRecord.from_dict(item) for item in payload.get("managed_sessions", [])]
            retained = [session for session in sessions if session.agent_id != agent_id]
            if len(retained) == len(sessions):
                raise KeyError(agent_id)
            messages = self._read_messages()
            messages.pop(agent_id, None)
            self._write_state(payload, sessions=retained, messages=messages)

    def list_messages(self, agent_id: str) -> list[SessionMessageRecord]:
        with self._lock:
            return list(self._read_messages().get(agent_id, []))

    def append_message(
        self,
        agent_id: str,
        *,
        kind: str,
        text: str,
        task_id: str = "",
        merge_output: bool = False,
    ) -> SessionMessageRecord:
        timestamp = utc_now()
        with self._lock:
            payload = self._load_state()
            messages = self._read_messages()
            session_messages = messages.setdefault(agent_id, [])
            if merge_output and session_messages:
                previous = session_messages[-1]
                if previous.kind == kind and previous.task_id == task_id:
                    previous.text += text
                    previous.updated_at = timestamp
                    self._write_state(payload, messages=messages)
                    return previous
            message = SessionMessageRecord(
                message_id=f"message-{uuid.uuid4().hex[:12]}",
                agent_id=agent_id,
                task_id=task_id,
                kind=kind,
                text=text,
                created_at=timestamp,
                updated_at=timestamp,
            )
            session_messages.append(message)
            self._write_state(payload, messages=messages)
            return message


@dataclass
class SessionRuntime:
    process: subprocess.Popen[str]
    reader_thread: threading.Thread
    stdin_lock: threading.Lock = field(default_factory=threading.Lock)
    current_task_id: str = ""
    current_marker: str = ""
    stop_requested: bool = False


class MindexUiApp:
    def __init__(self, config: UiConfig) -> None:
        self.config = config
        self.store = StateStore(config.state_file)
        self.task_queue_manager = TaskQueueManager(project_root=config.project_root, store=self.store)
        self.agent_manager = AgentManager(project_root=config.project_root, queue_log_dir=config.queue_log_dir, store=self.store)
        self.login_protector = LoginProtector(
            attempts=config.login_attempts,
            window_seconds=config.login_window_seconds,
        )
        self.sessions = SessionStore(ttl_seconds=config.session_ttl_seconds)
        self.managed_session_state = ManagedSessionState(self.store)
        self._runtime_lock = threading.RLock()
        self._runtimes: dict[str, SessionRuntime] = {}
        self._recover_detached_session_tasks()

    def _recover_detached_session_tasks(self) -> None:
        for session in self.managed_session_state.list_sessions():
            if not session.current_task_id or not session.queue_id:
                continue
            try:
                self.task_queue_manager.requeue_task_to_front(session.queue_id, session.current_task_id)
            except KeyError:
                pass
            self.managed_session_state.update_session(session.agent_id, current_task_id="")

    def verify_password(self, password: str) -> bool:
        salt = bytes.fromhex(self.config.password_salt)
        supplied = _hash_password(password, salt=salt, iterations=self.config.password_iterations)
        return hmac.compare_digest(self.config.password_hash, supplied)

    def create_session(self) -> SessionRecord:
        return self.sessions.create(self.config.username)

    def _validate_session_workdir(self, workdir: Path | str) -> Path:
        resolved_workdir = Path(workdir).resolve()
        if resolved_workdir == self.config.project_root:
            return resolved_workdir
        if self.config.project_root not in resolved_workdir.parents:
            raise ValueError("workdir must stay within the configured project root")
        return resolved_workdir

    def create_managed_session(
        self,
        *,
        name: str,
        workdir: Path | str,
    ) -> dict[str, Any]:
        session_name = name.strip() or "Untitled session"
        resolved_workdir = self._validate_session_workdir(workdir)
        queue = self.task_queue_manager.create_queue(name=session_name)
        session = self.managed_session_state.create_session(
            name=session_name,
            workdir=str(resolved_workdir),
            queue_id=queue.queue_id,
        )
        try:
            session = self._start_session_runtime(session.agent_id)
        except Exception:
            self.managed_session_state.delete_session(session.agent_id)
            self.task_queue_manager.delete_queue(queue.queue_id)
            raise
        return self._session_payload(session, queue)

    def _session_for_queue(self, queue_id: str) -> ManagedSessionRecord | None:
        for session in self.managed_session_state.list_sessions():
            if session.queue_id == queue_id:
                return session
        return None

    def _runtime_for_session(self, agent_id: str) -> SessionRuntime | None:
        with self._runtime_lock:
            return self._runtimes.get(agent_id)

    def _build_runtime_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("TERM", "dumb")
        env["MINDEX_UI_SESSION"] = "1"
        return env

    def _start_session_runtime(self, agent_id: str) -> ManagedSessionRecord:
        session = self.managed_session_state.get_session(agent_id)
        if session is None:
            raise KeyError(agent_id)
        runtime = self._runtime_for_session(agent_id)
        if runtime is not None and runtime.process.poll() is None:
            return self.managed_session_state.update_session(
                agent_id,
                status="running",
                started_at=session.started_at or utc_now(),
                stopped_at=None,
                last_error=None,
            )
        process = subprocess.Popen(
            list(DEFAULT_SESSION_COMMAND),
            cwd=session.workdir,
            env=self._build_runtime_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        reader_thread = threading.Thread(target=self._reader_loop, args=(agent_id,), daemon=True)
        runtime = SessionRuntime(process=process, reader_thread=reader_thread)
        with self._runtime_lock:
            self._runtimes[agent_id] = runtime
        session = self.managed_session_state.update_session(
            agent_id,
            status="running",
            started_at=utc_now(),
            stopped_at=None,
            last_error=None,
        )
        self.managed_session_state.append_message(
            agent_id,
            kind="system",
            text=f"Session started in {session.workdir}.\n",
            merge_output=True,
        )
        reader_thread.start()
        self._start_next_queued_task(agent_id)
        return session

    def _reader_loop(self, agent_id: str) -> None:
        runtime = self._runtime_for_session(agent_id)
        if runtime is None or runtime.process.stdout is None:
            return
        try:
            for chunk in runtime.process.stdout:
                active_runtime = self._runtime_for_session(agent_id)
                if active_runtime is None:
                    break
                stripped = chunk.strip()
                marker_prefix = f"{SESSION_DONE_PREFIX} "
                if stripped.startswith(marker_prefix):
                    parts = stripped.split()
                    if len(parts) >= 3 and parts[1] == active_runtime.current_marker:
                        try:
                            returncode = int(parts[2])
                        except ValueError:
                            returncode = 1
                        self._handle_task_finished(agent_id, active_runtime.current_task_id, returncode)
                        continue
                kind = "output" if active_runtime.current_task_id else "system"
                task_id = active_runtime.current_task_id
                self.managed_session_state.append_message(
                    agent_id,
                    kind=kind,
                    text=chunk,
                    task_id=task_id,
                    merge_output=True,
                )
        finally:
            returncode = runtime.process.wait()
            self._handle_runtime_exit(agent_id, returncode)

    def _handle_runtime_exit(self, agent_id: str, returncode: int) -> None:
        with self._runtime_lock:
            runtime = self._runtimes.get(agent_id)
            if runtime is None:
                return
            self._runtimes.pop(agent_id, None)
        if runtime.process.stdin is not None:
            runtime.process.stdin.close()
        if runtime.process.stdout is not None:
            runtime.process.stdout.close()
        session = self.managed_session_state.get_session(agent_id)
        if session is None:
            return
        current_task_id = runtime.current_task_id or session.current_task_id
        session = self.managed_session_state.update_session(
            agent_id,
            status="stopped",
            current_task_id="",
            stopped_at=utc_now(),
            last_error=None if runtime.stop_requested or returncode == 0 else f"Session exited with status {returncode}.",
        )
        if current_task_id and session.queue_id:
            try:
                if runtime.stop_requested:
                    self.task_queue_manager.requeue_task_to_front(session.queue_id, current_task_id)
                else:
                    self.task_queue_manager.update_task(session.queue_id, current_task_id, status="failed")
            except KeyError:
                pass
        note = "Session stopped.\n" if runtime.stop_requested else f"Session exited with status {returncode}.\n"
        self.managed_session_state.append_message(
            agent_id,
            kind="system" if runtime.stop_requested else "error",
            text=note,
            merge_output=True,
        )

    def _task_input_text(self, task: TaskRecord) -> str:
        if task.details.strip():
            return f"{task.title}\n\n{task.details.strip()}"
        return task.title

    def _command_chunk(self, command_text: str, marker: str) -> str:
        return (
            f"{command_text}\n"
            "__mindex_status=$?\n"
            f"printf '\\n{SESSION_DONE_PREFIX} {marker} %s\\n' \"$__mindex_status\"\n"
        )

    def _start_next_queued_task(self, agent_id: str) -> dict[str, Any] | None:
        session = self.managed_session_state.get_session(agent_id)
        runtime = self._runtime_for_session(agent_id)
        if session is None or runtime is None or session.status != "running" or runtime.stop_requested or runtime.current_task_id:
            return None
        if not session.queue_id:
            return None
        try:
            queue = self.task_queue_manager.get_queue(session.queue_id)
        except KeyError:
            return None
        next_task = next((task for task in queue.tasks if task.status == "queued"), None)
        if next_task is None:
            return None
        running_task = self.task_queue_manager.update_task(queue.queue_id, next_task.task_id, status="running")
        marker = uuid.uuid4().hex
        runtime.current_task_id = running_task.task_id
        runtime.current_marker = marker
        self.managed_session_state.update_session(agent_id, current_task_id=running_task.task_id)
        self.managed_session_state.append_message(
            agent_id,
            kind="input",
            text=self._task_input_text(running_task),
            task_id=running_task.task_id,
        )
        command_chunk = self._command_chunk(running_task.title, marker)
        try:
            if runtime.process.stdin is None:
                raise RuntimeError("session stdin is unavailable")
            with runtime.stdin_lock:
                runtime.process.stdin.write(command_chunk)
                runtime.process.stdin.flush()
        except Exception as exc:
            runtime.current_task_id = ""
            runtime.current_marker = ""
            self.managed_session_state.update_session(agent_id, current_task_id="")
            self.task_queue_manager.update_task(queue.queue_id, running_task.task_id, status="failed")
            self.managed_session_state.append_message(
                agent_id,
                kind="error",
                text=f"Failed to send queue item: {exc}\n",
                task_id=running_task.task_id,
            )
            raise
        return self.task_queue_manager.get_task(queue.queue_id, running_task.task_id).to_dict()

    def _handle_task_finished(self, agent_id: str, task_id: str, returncode: int) -> None:
        if not task_id:
            return
        runtime = self._runtime_for_session(agent_id)
        if runtime is not None:
            runtime.current_task_id = ""
            runtime.current_marker = ""
        session = self.managed_session_state.get_session(agent_id)
        if session is None:
            return
        self.managed_session_state.update_session(agent_id, current_task_id="")
        if session.queue_id:
            try:
                self.task_queue_manager.update_task(
                    session.queue_id,
                    task_id,
                    status="completed" if returncode == 0 else "failed",
                )
            except KeyError:
                pass
        if returncode != 0:
            self.managed_session_state.append_message(
                agent_id,
                kind="error",
                text=f"Command exited with status {returncode}.\n",
                task_id=task_id,
                merge_output=True,
            )
        refreshed = self.managed_session_state.get_session(agent_id)
        if refreshed is not None and refreshed.status == "running":
            self._start_next_queued_task(agent_id)

    def add_session_task(self, queue_id: str, *, title: str, details: str = "") -> dict[str, Any]:
        created = self.task_queue_manager.add_task(queue_id, title=title, details=details, status="queued")
        session = self._session_for_queue(queue_id)
        if session is not None and session.status == "running":
            self._start_next_queued_task(session.agent_id)
        return self.task_queue_manager.get_task(queue_id, created.task_id).to_dict()

    def send_to_session(self, agent_id: str, *, text: str, details: str = "") -> dict[str, Any]:
        session = self.managed_session_state.get_session(agent_id)
        if session is None:
            raise KeyError(agent_id)
        if not session.queue_id:
            raise ValueError("session queue is unavailable")
        return self.add_session_task(session.queue_id, title=text, details=details)

    def update_session_task(
        self,
        queue_id: str,
        task_id: str,
        *,
        title: str | None = None,
        details: str | None = None,
    ) -> TaskRecord:
        task = self.task_queue_manager.get_task(queue_id, task_id)
        if task.status == "running":
            raise ValueError("stop the session before editing the running queue item")
        return self.task_queue_manager.update_task(queue_id, task_id, title=title, details=details)

    def delete_session_task(self, queue_id: str, task_id: str) -> None:
        task = self.task_queue_manager.get_task(queue_id, task_id)
        if task.status == "running":
            raise ValueError("stop the session before deleting the running queue item")
        self.task_queue_manager.delete_task(queue_id, task_id)

    def reorder_session_tasks(self, queue_id: str, ordered_task_ids: list[str]) -> QueueRecord:
        queue = self.task_queue_manager.get_queue(queue_id)
        running_task = next((task for task in queue.tasks if task.status == "running"), None)
        if running_task is not None and ordered_task_ids[:1] != [running_task.task_id]:
            raise ValueError("the running queue item must stay at the front")
        return self.task_queue_manager.reorder_tasks(queue_id, ordered_task_ids)

    def list_session_messages(self, agent_id: str) -> list[dict[str, Any]]:
        session = self.managed_session_state.get_session(agent_id)
        if session is None:
            raise KeyError(agent_id)
        return [message.to_dict() for message in self.managed_session_state.list_messages(agent_id)]

    def start_managed_session(self, agent_id: str) -> dict[str, Any]:
        session = self._start_session_runtime(agent_id)
        queue = None
        if session.queue_id:
            try:
                queue = self.task_queue_manager.get_queue(session.queue_id)
            except KeyError:
                queue = None
        return self._session_payload(session, queue)

    def stop_managed_session(self, agent_id: str, *, settle_timeout: float = 2.0) -> dict[str, Any]:
        session = self.managed_session_state.get_session(agent_id)
        if session is None:
            raise KeyError(agent_id)
        runtime = self._runtime_for_session(agent_id)
        if runtime is None or runtime.process.poll() is not None:
            session = self.managed_session_state.update_session(
                agent_id,
                status="stopped",
                current_task_id="",
                stopped_at=utc_now(),
            )
            queue = None
            if session.queue_id:
                try:
                    queue = self.task_queue_manager.get_queue(session.queue_id)
                except KeyError:
                    queue = None
            return self._session_payload(session, queue)
        runtime.stop_requested = True
        runtime.process.terminate()
        deadline = time.time() + settle_timeout
        while time.time() < deadline:
            refreshed = self.managed_session_state.get_session(agent_id)
            if refreshed is None:
                raise KeyError(agent_id)
            if refreshed.status == "stopped" and not refreshed.current_task_id:
                break
            time.sleep(0.05)
        if runtime.process.poll() is None:
            runtime.process.kill()
            runtime.process.wait(timeout=settle_timeout)
        refreshed_runtime = self._runtime_for_session(agent_id)
        if refreshed_runtime is not None and refreshed_runtime.process.poll() is not None:
            self._handle_runtime_exit(agent_id, refreshed_runtime.process.returncode or 0)
        session = self.managed_session_state.get_session(agent_id)
        if session is None:
            raise KeyError(agent_id)
        queue = None
        if session.queue_id:
            try:
                queue = self.task_queue_manager.get_queue(session.queue_id)
            except KeyError:
                queue = None
        return self._session_payload(session, queue)

    def delete_managed_session(self, agent_id: str) -> None:
        session = self.managed_session_state.get_session(agent_id)
        if session is None:
            raise KeyError(agent_id)
        if session.status == "running":
            raise ValueError("stop the session before deleting it")
        self.managed_session_state.delete_session(agent_id)
        if session.queue_id:
            try:
                self.task_queue_manager.delete_queue(session.queue_id)
            except KeyError:
                pass

    def _visible_output(self, agent_id: str, *, max_bytes: int = 6000) -> str:
        fragments: list[str] = []
        for message in self.managed_session_state.list_messages(agent_id):
            if message.kind == "input":
                fragments.append(f"$ {message.text}\n")
                continue
            fragments.append(message.text)
        text = "".join(fragments)
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) <= max_bytes:
            return text.strip()
        truncated = encoded[-max_bytes:].decode("utf-8", errors="replace")
        if "\n" in truncated:
            truncated = truncated.split("\n", 1)[1]
        return truncated.strip()

    def _session_payload(self, session: ManagedSessionRecord, queue: QueueRecord | None) -> dict[str, Any]:
        payload = session.to_dict()
        payload["agent_status"] = session.status
        payload["status"] = session.status
        payload["queue"] = queue.to_dict() if queue is not None else {}
        payload["messages"] = [message.to_dict() for message in self.managed_session_state.list_messages(session.agent_id)]
        payload["output"] = self._visible_output(session.agent_id)
        return payload

    def list_session_payloads(self) -> list[dict[str, Any]]:
        queues_by_id = {queue.queue_id: queue for queue in self.task_queue_manager.list_queues()}
        sessions = [
            self._session_payload(session, queues_by_id.get(session.queue_id))
            for session in self.managed_session_state.list_sessions()
        ]
        sessions.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return sessions

    def recent_runs(self, limit: int = 8) -> list[dict[str, Any]]:
        logs_root = self.config.project_root / "logs"
        if not logs_root.exists():
            return []
        entries: list[dict[str, Any]] = []
        for status_path in logs_root.glob("session-*/*/status.json"):
            run_dir = status_path.parent
            prompt_path = run_dir / "prompt.txt"
            prompt = prompt_path.read_text(encoding="utf-8").strip() if prompt_path.exists() else run_dir.name
            try:
                status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            entries.append(
                {
                    "run_dir": str(run_dir),
                    "prompt": prompt,
                    "status": status_payload.get("status", "unknown"),
                    "updated_at": status_payload.get("updated_at"),
                    "returncode": status_payload.get("returncode"),
                }
            )
        entries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return entries[:limit]

    def system_status(self) -> dict[str, Any]:
        branch = "unknown"
        completed = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(self.config.project_root),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0:
            branch = completed.stdout.strip() or branch
        sessions = self.list_session_payloads()
        return {
            "project_root": str(self.config.project_root),
            "branch": branch,
            "config_path": str(self.config.config_path),
            "title": self.config.title,
            "session_count": len(sessions),
            "running_count": sum(1 for session in sessions if session["status"] == "running"),
            "state_file": str(self.config.state_file),
            "queue_log_dir": str(self.config.queue_log_dir),
            "allowed_origins": list(self.config.allowed_origins),
            "allow_remote": self.config.allow_remote,
            "disable_origin_checks": self.config.disable_origin_checks,
            "disable_csrf_checks": self.config.disable_csrf_checks,
            "security": {
                "localhost_only": not self.config.allow_remote,
                "csrf_protected": not self.config.disable_csrf_checks,
                "origin_checks": not self.config.disable_origin_checks,
                "rate_limited_logins": True,
                "hashed_password_store": True,
            },
            "sessions": sessions,
        }


INDEX_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{title}</title>
  <link rel=\"stylesheet\" href=\"/static/app.css\">
</head>
<body>
  <div class=\"shell\">
    <header class=\"hero\">
      <p class=\"eyebrow\">Secure Mindex operations</p>
      <h1>{title}</h1>
      <p class=\"lede\">A minimal browser view for live sessions, their queue order, and the visible transcript for each run.</p>
    </header>
    <main id=\"app\" class=\"app\"></main>
  </div>
  <script src=\"/static/app.js\"></script>
</body>
</html>
"""


APP_CSS = """
:root {
  --paper: #f6f1e8;
  --ink: #191611;
  --muted: #6a6258;
  --panel: rgba(255, 251, 245, 0.96);
  --line: rgba(35, 28, 18, 0.12);
  --accent: #a84d2d;
  --accent-soft: rgba(168, 77, 45, 0.12);
  --sage: #295244;
  --danger: #8b2f3d;
  --shadow: 0 18px 50px rgba(35, 24, 10, 0.08);
  --heading: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  --body: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--body);
  color: var(--ink);
  background:
    radial-gradient(circle at top left, rgba(168, 77, 45, 0.12), transparent 32%),
    linear-gradient(180deg, #efe7d8 0%, #f8f4ed 46%, #f1eadf 100%);
}
.shell {
  width: min(1120px, calc(100vw - 28px));
  margin: 0 auto;
  padding: 24px 0 36px;
}
.hero,
.panel,
.session-card,
.task-item,
.output-card {
  background: var(--panel);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
}
.hero {
  padding: 24px;
  border-radius: 28px;
}
.hero h1 {
  margin: 0;
  font-family: var(--heading);
  font-size: clamp(2.2rem, 5vw, 4rem);
  line-height: 0.94;
}
.eyebrow {
  margin: 0 0 10px;
  color: var(--accent);
  letter-spacing: 0.16em;
  text-transform: uppercase;
  font-size: 0.72rem;
}
.lede,
.muted,
label {
  color: var(--muted);
}
.lede {
  margin: 14px 0 0;
  max-width: 44rem;
}
.app {
  margin-top: 20px;
  display: grid;
  gap: 18px;
}
.panel,
.session-card {
  border-radius: 24px;
  padding: 20px;
}
.stack {
  display: grid;
  gap: 14px;
}
.row-between {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.button-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
button {
  border: 0;
  border-radius: 999px;
  padding: 10px 16px;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
button:disabled {
  opacity: 0.45;
  cursor: wait;
}
.primary { background: var(--accent); color: #fff7f2; }
.secondary { background: rgba(41, 82, 68, 0.12); color: var(--sage); }
.ghost { background: rgba(25, 22, 17, 0.06); color: var(--ink); }
.danger { background: var(--danger); color: #fff4f6; }
input,
textarea,
select {
  width: 100%;
  border-radius: 14px;
  border: 1px solid rgba(35, 28, 18, 0.14);
  background: rgba(255, 255, 255, 0.74);
  color: var(--ink);
  padding: 12px 14px;
  font: inherit;
}
textarea { min-height: 96px; resize: vertical; }
label {
  display: grid;
  gap: 7px;
  font-size: 0.92rem;
}
code,
.output-text {
  font-family: var(--mono);
}
.kicker {
  margin: 0 0 5px;
  color: var(--muted);
  font-size: 0.75rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}
.metric {
  margin: 0;
  font-family: var(--heading);
  font-size: 2rem;
}
.summary-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}
.summary-tile {
  padding: 16px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.58);
  border: 1px solid rgba(35, 28, 18, 0.08);
}
.notice {
  border-radius: 16px;
  padding: 12px 14px;
  background: rgba(168, 77, 45, 0.1);
  border: 1px solid rgba(168, 77, 45, 0.18);
  color: var(--accent);
}
.hidden { display: none !important; }
.status-pill {
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  background: rgba(25, 22, 17, 0.08);
  color: var(--muted);
}
.status-running, .status-in-progress { background: rgba(206, 161, 71, 0.22); color: #875b00; }
.status-stopped { background: rgba(25, 22, 17, 0.08); color: var(--muted); }
.status-completed, .status-done { background: rgba(41, 82, 68, 0.14); color: var(--sage); }
.status-failed { background: rgba(139, 47, 61, 0.12); color: var(--danger); }
.status-blocked, .status-disconnected { background: rgba(90, 70, 42, 0.14); color: #70552c; }
.session-list {
  display: grid;
  gap: 16px;
}
.session-card {
  display: grid;
  gap: 18px;
}
.session-meta {
  display: grid;
  gap: 6px;
}
.session-meta p {
  margin: 0;
  color: var(--muted);
}
.queue-shell {
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(0, 1.05fr) minmax(280px, 0.95fr);
}
.queue-column,
.output-card {
  display: grid;
  gap: 12px;
}
.output-card {
  border-radius: 18px;
  padding: 16px;
  background: rgba(25, 22, 17, 0.94);
  color: #f9f4ea;
}
.output-card h3,
.queue-column h3 {
  margin: 0;
}
.output-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  max-height: 360px;
  overflow: auto;
  font-size: 0.84rem;
  line-height: 1.5;
}
.message-feed {
  display: grid;
  gap: 10px;
  max-height: 360px;
  overflow: auto;
}
.message-item {
  border-radius: 14px;
  padding: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.04);
}
.message-input {
  border-color: rgba(168, 77, 45, 0.4);
  background: rgba(168, 77, 45, 0.12);
}
.message-error {
  border-color: rgba(139, 47, 61, 0.42);
  background: rgba(139, 47, 61, 0.14);
}
.message-meta {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
}
.message-text {
  margin: 8px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  font-family: var(--mono);
  font-size: 0.82rem;
  line-height: 1.5;
}
.task-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}
.task-item {
  border-radius: 16px;
  padding: 14px;
  cursor: grab;
  display: grid;
  gap: 10px;
}
.task-item-front-running {
  border-color: rgba(139, 47, 61, 0.56);
  box-shadow: 0 0 0 2px rgba(139, 47, 61, 0.18), var(--shadow);
}
.task-item.dragging {
  opacity: 0.5;
}
.task-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}
.task-title {
  margin: 0;
  font-weight: 700;
}
.task-details {
  margin: 4px 0 0;
  color: var(--muted);
  white-space: pre-wrap;
}
.empty-state {
  text-align: center;
  padding: 30px 18px;
  border-radius: 20px;
  border: 1px dashed rgba(35, 28, 18, 0.18);
  color: var(--muted);
  background: rgba(255, 255, 255, 0.48);
}
.login-card {
  max-width: 460px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .queue-shell {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 720px) {
  .shell {
    width: min(100vw - 18px, 1120px);
    padding-top: 18px;
  }
  .hero,
  .panel,
  .session-card {
    border-radius: 22px;
  }
}
"""



APP_JS = """
const state = { csrfToken: null, refreshTimer: null, loading: false };

async function api(path, options = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
  if (state.csrfToken && options.method && options.method !== 'GET') {
    headers['X-Mindex-CSRF-Token'] = state.csrfToken;
  }
  const response = await fetch(path, Object.assign({ credentials: 'same-origin' }, options, { headers }));
  if (response.status === 204) {
    return null;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed with ${response.status}`);
  }
  return payload;
}

function escapeHtml(value) {
  const node = document.createElement('div');
  node.textContent = value || '';
  return node.innerHTML;
}

function statusClass(status) {
  return `status-pill status-${String(status || 'queued').replace(/_/g, '-').toLowerCase()}`;
}

function resolveFormTarget(event) {
  const candidate = event && (event.currentTarget || event.target);
  if (!candidate) {
    return null;
  }
  if (typeof candidate.closest === 'function') {
    const form = candidate.closest('form');
    if (form) {
      return form;
    }
  }
  return candidate;
}

function setNotice(node, message = '') {
  if (!node) {
    return;
  }
  node.textContent = message;
  node.classList[message ? 'remove' : 'add']('hidden');
}

function renderLogin(message = '') {
  if (state.refreshTimer) {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = null;
  }
  const app = document.getElementById('app');
  app.innerHTML = `
    <section class="panel login-card stack">
      <p class="kicker">Authenticate</p>
      <h2>Open the session view</h2>
      <p class="muted">Use the credentials stored in <code>.mindex/ui_config.json</code>.</p>
      <form id="login-form" class="stack">
        <label>Username<input name="username" autocomplete="username" required value="admin"></label>
        <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
        <div class="button-row"><button class="primary" type="submit">Sign in</button></div>
        <p id="login-error" class="notice ${message ? '' : 'hidden'}">${escapeHtml(message)}</p>
      </form>
    </section>`;
  document.getElementById('login-form').addEventListener('submit', submitLogin);
}

async function submitLogin(event) {
  event.preventDefault();
  const formElement = resolveFormTarget(event);
  if (!formElement) {
    renderLogin('Unable to read the login form. Refresh and try again.');
    return;
  }
  const form = new FormData(formElement);
  try {
    const payload = await api('/api/login', {
      method: 'POST',
      body: JSON.stringify({
        username: form.get('username'),
        password: form.get('password'),
      }),
    });
    state.csrfToken = payload.csrf_token;
    await loadDashboard();
  } catch (error) {
    renderLogin(error.message);
  }
}

function renderTaskCard(queueId, task, isFrontRunning = false) {
  const taskClassName = isFrontRunning ? 'task-item task-item-front-running' : 'task-item';
  return `
    <li class="${taskClassName}" draggable="true" data-task-id="${escapeHtml(task.task_id)}" data-queue-id="${escapeHtml(queueId)}">
      <div class="task-head">
        <div>
          <p class="task-title">${escapeHtml(task.title)}</p>
          <p class="task-details">${escapeHtml(task.details || 'No extra details.')}</p>
        </div>
        <div class="${statusClass(task.status)}">${escapeHtml(String(task.status || 'queued').replace('_', ' '))}</div>
      </div>
      <div class="button-row">
        <button class="ghost" type="button" data-queue-id="${escapeHtml(queueId)}" data-edit-task="${escapeHtml(task.task_id)}" data-task-title="${escapeHtml(task.title)}" data-task-details="${escapeHtml(task.details || '')}">Edit task</button>
        <button class="danger" type="button" data-queue-id="${escapeHtml(queueId)}" data-delete-task="${escapeHtml(task.task_id)}">Delete</button>
      </div>
    </li>`;
}

function renderMessage(message) {
  const kind = String(message.kind || 'output');
  const label = {
    input: 'Queued item',
    output: 'Output',
    system: 'Session',
    error: 'Error',
  }[kind] || 'Output';
  return `
    <article class="message-item message-${escapeHtml(kind)}">
      <div class="message-meta">
        <span class="kicker">${escapeHtml(label)}</span>
        <span class="muted">${escapeHtml(message.updated_at || message.created_at || '')}</span>
      </div>
      <pre class="message-text">${escapeHtml(message.text || '')}</pre>
    </article>`;
}

function renderSessionCard(session) {
  const queue = session.queue || {};
  const tasks = queue.tasks || [];
  const messages = session.messages || [];
  const running = session.status === 'running';
  return `
    <article class="session-card">
      <div class="row-between">
        <div class="stack session-meta">
          <div>
            <p class="kicker">Session</p>
            <h2>${escapeHtml(session.name)}</h2>
          </div>
          <p>Workdir: <code>${escapeHtml(session.workdir || '')}</code></p>
        </div>
        <div class="stack" style="justify-items:end;">
          <div class="${statusClass(session.status)}">${escapeHtml(session.status)}</div>
          <div class="button-row">
            <button class="primary" ${running ? 'disabled' : ''} data-start-session="${escapeHtml(session.agent_id)}">Start</button>
            <button class="ghost" ${!running ? 'disabled' : ''} data-stop-session="${escapeHtml(session.agent_id)}">Stop</button>
            <button class="danger" ${running ? 'disabled' : ''} data-delete-session="${escapeHtml(session.agent_id)}">Delete</button>
          </div>
        </div>
      </div>
      <div class="queue-shell">
        <section class="queue-column">
          <div class="row-between">
            <div>
              <p class="kicker">Queue</p>
              <h3>${escapeHtml(queue.name || 'Session queue')}</h3>
              <p class="muted">Adjust the order, edit queued items, and run them in this live session.</p>
            </div>
          </div>
          ${queue.queue_id ? `
            <ul class="task-list" data-task-list="${escapeHtml(queue.queue_id)}">
              ${tasks.length ? tasks.map((task, index) => renderTaskCard(queue.queue_id, task, index === 0 && task.status === 'running')).join('') : '<li class="empty-state">No queue items yet.</li>'}
            </ul>
            <form class="stack" data-task-form="${escapeHtml(queue.queue_id)}" data-session-id="${escapeHtml(session.agent_id)}">
              <label>Queue item<input name="title" placeholder="ls" required></label>
              <label>Notes<textarea name="details" placeholder="Optional notes for this queued item."></textarea></label>
              <div class="button-row"><button class="secondary" type="submit">Add queue item</button></div>
            </form>
          ` : '<div class="empty-state">This legacy session does not have a queue attached.</div>'}
        </section>
        <section class="output-card">
          <div>
            <p class="kicker">Transcript</p>
            <h3>Visible session output</h3>
          </div>
          <div class="message-feed">${messages.length ? messages.map(renderMessage).join('') : '<div class="empty-state">No session output yet.</div>'}</div>
        </section>
      </div>
    </article>`;
}

function renderDashboard(payload) {
  const app = document.getElementById('app');
  const sessions = payload.sessions || [];
  app.innerHTML = `
    <section class="panel stack">
      <div class="row-between">
        <div>
          <p class="kicker">Sessions</p>
          <h2>Simple session manager</h2>
          <p class="muted">Each session stays running until you stop it, and its queue drains from the front.</p>
        </div>
        <div class="button-row">
          <button id="refresh-button" class="ghost">Refresh</button>
          <button id="logout-button" class="secondary">Logout</button>
        </div>
      </div>
      <div class="summary-grid">
        <article class="summary-tile"><p class="kicker">Workspace</p><p class="metric">${sessions.length}</p><p class="muted">Sessions in <code>${escapeHtml(payload.project_root)}</code></p></article>
        <article class="summary-tile"><p class="kicker">Running</p><p class="metric">${payload.running_count}</p><p class="muted">Branch <code>${escapeHtml(payload.branch)}</code></p></article>
      </div>
      <form id="session-form" class="stack">
        <label>Session name<input name="name" placeholder="Triage flaky tests" required></label>
        <label>Working directory<input name="workdir" value="${escapeHtml(payload.project_root)}" required></label>
        <div class="button-row"><button class="primary" type="submit">Create session</button></div>
        <p id="session-form-error" class="notice hidden"></p>
      </form>
    </section>
    <section class="session-list">
      ${sessions.length ? sessions.map(renderSessionCard).join('') : '<div class="empty-state">No sessions yet. Create one to start tracking queue order and output.</div>'}
    </section>`;

  document.getElementById('session-form').addEventListener('submit', submitSession);
  document.getElementById('refresh-button').addEventListener('click', loadDashboard);
  document.getElementById('logout-button').addEventListener('click', logout);
  document.querySelectorAll('[data-edit-queue]').forEach(node => node.addEventListener('click', () => renameQueue(node.dataset.editQueue, node.dataset.queueName || '', node.dataset.queueDescription || '')));
  document.querySelectorAll('[data-task-form]').forEach(node => node.addEventListener('submit', submitTask));
  document.querySelectorAll('[data-edit-task]').forEach(node => node.addEventListener('click', () => editTask(node.dataset.queueId, node.dataset.editTask, node.dataset.taskTitle || '', node.dataset.taskDetails || '')));
  document.querySelectorAll('[data-delete-task]').forEach(node => node.addEventListener('click', () => removeTask(node.dataset.queueId, node.dataset.deleteTask)));
  document.querySelectorAll('[data-start-session]').forEach(node => node.addEventListener('click', () => changeSession(node.dataset.startSession, 'start')));
  document.querySelectorAll('[data-stop-session]').forEach(node => node.addEventListener('click', () => changeSession(node.dataset.stopSession, 'stop')));
  document.querySelectorAll('[data-delete-session]').forEach(node => node.addEventListener('click', () => changeSession(node.dataset.deleteSession, 'delete')));
  bindTaskDragAndDrop();
}

async function submitSession(event) {
  event.preventDefault();
  const errorNode = document.getElementById('session-form-error');
  const formElement = resolveFormTarget(event);
  if (!formElement) {
    setNotice(errorNode, 'Unable to read the session form. Refresh and try again.');
    return;
  }
  const form = new FormData(formElement);
  const workdirValue = String(form.get('workdir') || '');
  setNotice(errorNode);
  try {
    await api('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({
        name: form.get('name'),
        workdir: form.get('workdir'),
      }),
    });
    if (typeof formElement.reset === 'function') {
      formElement.reset();
    }
    if (formElement.elements && formElement.elements.workdir) {
      formElement.elements.workdir.value = workdirValue;
    }
    await loadDashboard();
  } catch (error) {
    setNotice(errorNode, error.message);
  }
}

async function renameQueue(queueId, currentName, currentDescription) {
  const name = window.prompt('Queue name', currentName);
  if (name === null) {
    return;
  }
  const description = window.prompt('Queue description', currentDescription);
  if (description === null) {
    return;
  }
  try {
    await api(`/api/queues/${queueId}`, {
      method: 'PATCH',
      body: JSON.stringify({ name, description }),
    });
    await loadDashboard();
  } catch (error) {
    alert(error.message);
  }
}

async function submitTask(event) {
  event.preventDefault();
  const formElement = resolveFormTarget(event);
  if (!formElement) {
    alert('Unable to read the queue form. Refresh and try again.');
    return;
  }
  const queueId = formElement.dataset ? formElement.dataset.taskForm : '';
  if (!queueId) {
    alert('Unable to find the queue for this form. Refresh and try again.');
    return;
  }
  const sessionId = formElement.dataset ? formElement.dataset.sessionId : '';
  if (!sessionId) {
    alert('Unable to find the session for this queue form. Refresh and try again.');
    return;
  }
  const form = new FormData(formElement);
  try {
    await api(`/api/sessions/${sessionId}/send`, {
      method: 'POST',
      body: JSON.stringify({
        text: form.get('title'),
        details: form.get('details'),
      }),
    });
    if (typeof formElement.reset === 'function') {
      formElement.reset();
    }
    await loadDashboard();
  } catch (error) {
    alert(error.message);
  }
}

async function editTask(queueId, taskId, currentTitle, currentDetails) {
  const title = window.prompt('Task title', currentTitle);
  if (title === null) {
    return;
  }
  const details = window.prompt('Task details', currentDetails);
  if (details === null) {
    return;
  }
  try {
    await api(`/api/queues/${queueId}/tasks/${taskId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title, details }),
    });
    await loadDashboard();
  } catch (error) {
    alert(error.message);
  }
}

async function removeTask(queueId, taskId) {
  if (!window.confirm('Delete this queue item?')) {
    return;
  }
  try {
    await api(`/api/queues/${queueId}/tasks/${taskId}`, { method: 'DELETE' });
    await loadDashboard();
  } catch (error) {
    alert(error.message);
  }
}

function bindTaskDragAndDrop() {
  document.querySelectorAll('[data-task-list]').forEach(list => {
    list.addEventListener('dragover', event => {
      event.preventDefault();
      const dragging = document.querySelector('.task-item.dragging');
      if (!dragging) {
        return;
      }
      const afterElement = getDragAfterElement(list, event.clientY);
      if (afterElement === null) {
        list.appendChild(dragging);
      } else {
        list.insertBefore(dragging, afterElement);
      }
    });
    list.addEventListener('drop', async event => {
      event.preventDefault();
      const queueId = list.dataset.taskList;
      const orderedTaskIds = Array.from(list.querySelectorAll('[data-task-id]')).map(node => node.dataset.taskId);
      if (!orderedTaskIds.length) {
        return;
      }
      try {
        await api(`/api/queues/${queueId}/reorder`, {
          method: 'POST',
          body: JSON.stringify({ ordered_task_ids: orderedTaskIds }),
        });
        await loadDashboard();
      } catch (error) {
        alert(error.message);
      }
    });
  });

  document.querySelectorAll('.task-item').forEach(item => {
    item.addEventListener('dragstart', () => item.classList.add('dragging'));
    item.addEventListener('dragend', () => item.classList.remove('dragging'));
  });
}

function getDragAfterElement(container, y) {
  const draggableElements = [...container.querySelectorAll('.task-item:not(.dragging)')];
  return draggableElements.reduce((closest, child) => {
    const box = child.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > closest.offset) {
      return { offset, element: child };
    }
    return closest;
  }, { offset: Number.NEGATIVE_INFINITY, element: null }).element;
}

async function changeSession(sessionId, action) {
  try {
    if (action === 'delete') {
      await api(`/api/sessions/${sessionId}`, { method: 'DELETE' });
    } else {
      await api(`/api/sessions/${sessionId}/${action}`, { method: 'POST', body: '{}' });
    }
    await loadDashboard();
  } catch (error) {
    alert(error.message);
  }
}

async function logout() {
  await api('/api/logout', { method: 'POST', body: '{}' }).catch(() => null);
  state.csrfToken = null;
  renderLogin();
}

function scheduleRefresh() {
  if (state.refreshTimer) {
    clearTimeout(state.refreshTimer);
  }
  state.refreshTimer = setTimeout(() => {
    loadDashboard();
  }, 1500);
}

async function loadDashboard() {
  if (state.loading) {
    return;
  }
  state.loading = true;
  try {
    const payload = await api('/api/status');
    if (payload.csrf_token) {
      state.csrfToken = payload.csrf_token;
    }
    const sessions = await Promise.all((payload.sessions || []).map(async session => {
      try {
        const messagePayload = await api(`/api/sessions/${session.agent_id}/messages`);
        return Object.assign({}, session, { messages: messagePayload.messages || [] });
      } catch (error) {
        return session;
      }
    }));
    renderDashboard(Object.assign({}, payload, { sessions }));
    scheduleRefresh();
  } catch (error) {
    renderLogin(error.message === 'authentication required' ? '' : error.message);
  } finally {
    state.loading = false;
  }
}

loadDashboard();
"""



def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    _write_common_headers(handler, content_type="application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def _text_response(handler: BaseHTTPRequestHandler, status: int, text: str, *, content_type: str) -> None:
    encoded = text.encode("utf-8")
    handler.send_response(status)
    _write_common_headers(handler, content_type=content_type)
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def _write_common_headers(handler: BaseHTTPRequestHandler, *, content_type: str) -> None:
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Cross-Origin-Opener-Policy", "same-origin")
    handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    )


class UiRequestHandler(BaseHTTPRequestHandler):
    server_version = "MindexUI/0.1"
    app: MindexUiApp

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _dispatch(self) -> None:
        path = urlparse(self.path).path
        if self.command == "GET" and path == "/":
            return _text_response(self, HTTPStatus.OK, INDEX_HTML.format(title=html.escape(self.app.config.title)), content_type="text/html; charset=utf-8")
        if self.command == "GET" and path == "/static/app.css":
            return _text_response(self, HTTPStatus.OK, APP_CSS, content_type="text/css; charset=utf-8")
        if self.command == "GET" and path == "/static/app.js":
            return _text_response(self, HTTPStatus.OK, APP_JS, content_type="application/javascript; charset=utf-8")
        if path == "/api/login" and self.command == "POST":
            return self._handle_login()
        if path == "/api/logout" and self.command == "POST":
            return self._handle_logout()
        if path == "/api/status" and self.command == "GET":
            return self._handle_status()
        if path == "/api/sessions" and self.command == "POST":
            return self._handle_create_managed_session()
        if path.startswith("/api/sessions/"):
            return self._handle_session_route(path)
        if path == "/api/agents" and self.command == "GET":
            return self._handle_list_agents()
        if path == "/api/agents" and self.command == "POST":
            return self._handle_create_agent()
        if path.startswith("/api/agents/"):
            return self._handle_agent_route(path)
        if path == "/api/queues" and self.command == "POST":
            return self._handle_create_queue()
        if path.startswith("/api/queues/"):
            return self._handle_queue_route(path)
        return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _handle_login(self) -> None:
        origin_error = self._check_origin(require_auth=False)
        if origin_error is not None:
            return _json_response(self, HTTPStatus.FORBIDDEN, {"error": origin_error})
        client_key = self.client_address[0]
        if self.app.login_protector.blocked(client_key):
            return _json_response(self, HTTPStatus.TOO_MANY_REQUESTS, {"error": "too many login attempts"})
        payload = self._read_json_body()
        if payload is None:
            return
        username = str(payload.get("username", ""))
        password = str(payload.get("password", ""))
        if username != self.app.config.username or not self.app.verify_password(password):
            self.app.login_protector.register_failure(client_key)
            return _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "invalid credentials"})
        self.app.login_protector.clear(client_key)
        session = self.app.create_session()
        self.send_response(HTTPStatus.OK)
        _write_common_headers(self, content_type="application/json; charset=utf-8")
        cookie = SimpleCookie()
        cookie[COOKIE_NAME] = session.token
        cookie[COOKIE_NAME]["path"] = "/"
        cookie[COOKIE_NAME]["httponly"] = True
        cookie[COOKIE_NAME]["samesite"] = "Strict"
        self.send_header("Set-Cookie", cookie.output(header="").strip())
        encoded = json.dumps({"ok": True, "csrf_token": session.csrf_token}).encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _handle_logout(self) -> None:
        session = self._require_session(require_csrf=True)
        if session is None:
            return
        self.app.sessions.delete(session.token)
        self.send_response(HTTPStatus.NO_CONTENT)
        _write_common_headers(self, content_type="application/json; charset=utf-8")
        cookie = SimpleCookie()
        cookie[COOKIE_NAME] = ""
        cookie[COOKIE_NAME]["path"] = "/"
        cookie[COOKIE_NAME]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        self.send_header("Set-Cookie", cookie.output(header="").strip())
        self.end_headers()

    def _handle_status(self) -> None:
        session = self._require_session(require_csrf=False)
        if session is None:
            return
        payload = self.app.system_status()
        payload["csrf_token"] = session.csrf_token
        _json_response(self, HTTPStatus.OK, payload)

    def _handle_list_agents(self) -> None:
        if self._require_session(require_csrf=False) is None:
            return
        _json_response(self, HTTPStatus.OK, {"agents": [agent.to_dict() for agent in self.app.agent_manager.list_agents()]})

    def _handle_create_managed_session(self) -> None:
        if self._require_session(require_csrf=True) is None:
            return
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            session_payload = self.app.create_managed_session(
                name=str(payload.get("name", "")),
                workdir=str(payload.get("workdir", self.app.config.project_root)),
            )
        except ValueError as exc:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        _json_response(self, HTTPStatus.CREATED, {"session": session_payload})

    def _handle_session_route(self, path: str) -> None:
        if self._require_session(require_csrf=self.command != "GET") is None:
            return
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 3:
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
        agent_id = segments[2]
        if len(segments) == 3 and self.command == "DELETE":
            try:
                self.app.delete_managed_session(agent_id)
            except KeyError:
                return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "session not found"})
            except ValueError as exc:
                return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return _json_response(self, HTTPStatus.OK, {"ok": True})
        if len(segments) == 4 and segments[3] == "messages" and self.command == "GET":
            try:
                messages = self.app.list_session_messages(agent_id)
            except KeyError:
                return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "session not found"})
            return _json_response(self, HTTPStatus.OK, {"messages": messages})
        if len(segments) == 4 and segments[3] == "send" and self.command == "POST":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                task = self.app.send_to_session(
                    agent_id,
                    text=str(payload.get("text", "")),
                    details=str(payload.get("details", "")),
                )
            except KeyError:
                return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "session not found"})
            except ValueError as exc:
                return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return _json_response(self, HTTPStatus.CREATED, {"task": task})
        if len(segments) != 4 or self.command != "POST":
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
        action = segments[3]
        try:
            if action == "start":
                session_payload = self.app.start_managed_session(agent_id)
                return _json_response(self, HTTPStatus.OK, {"session": session_payload})
            elif action == "stop":
                session_payload = self.app.stop_managed_session(agent_id)
                return _json_response(self, HTTPStatus.OK, {"session": session_payload})
            else:
                return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
        except KeyError:
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "session not found"})
        except ValueError as exc:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _handle_create_agent(self) -> None:
        if self._require_session(require_csrf=True) is None:
            return
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            command_args = shlex.split(str(payload.get("command_args", "")))
            agent = self.app.agent_manager.create_agent(
                name=str(payload.get("name", "")),
                description=str(payload.get("description", "")),
                command_args=command_args,
                workdir=str(payload.get("workdir", self.app.config.project_root)),
                feature_branch=str(payload.get("feature_branch", "")),
                auto_publish=bool(payload.get("auto_publish", True)),
            )
        except ValueError as exc:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        _json_response(self, HTTPStatus.CREATED, {"agent": agent.to_dict()})

    def _handle_agent_route(self, path: str) -> None:
        if self._require_session(require_csrf=self.command != "GET") is None:
            return
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 3:
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
        agent_id = segments[2]
        if len(segments) == 3 and self.command == "DELETE":
            try:
                self.app.agent_manager.delete_agent(agent_id)
            except KeyError:
                return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "agent not found"})
            except ValueError as exc:
                return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return _json_response(self, HTTPStatus.OK, {"ok": True})
        if len(segments) != 4 or self.command != "POST":
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
        action = segments[3]
        try:
            if action == "start":
                agent = self.app.agent_manager.start_agent(agent_id)
            elif action == "stop":
                agent = self.app.agent_manager.stop_agent(agent_id)
            else:
                return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
        except KeyError:
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "agent not found"})
        _json_response(self, HTTPStatus.OK, {"agent": agent.to_dict()})

    def _handle_create_queue(self) -> None:
        if self._require_session(require_csrf=True) is None:
            return
        payload = self._read_json_body()
        if payload is None:
            return
        queue = self.app.task_queue_manager.create_queue(
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
        )
        _json_response(self, HTTPStatus.CREATED, {"queue": queue.to_dict()})

    def _handle_queue_route(self, path: str) -> None:
        if self._require_session(require_csrf=self.command != "GET") is None:
            return
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 3:
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
        queue_id = segments[2]
        try:
            if len(segments) == 3:
                if self.command == "PATCH":
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    queue = self.app.task_queue_manager.update_queue(
                        queue_id,
                        name=payload.get("name"),
                        description=payload.get("description"),
                    )
                    return _json_response(self, HTTPStatus.OK, {"queue": queue.to_dict()})
                if self.command == "DELETE":
                    self.app.task_queue_manager.delete_queue(queue_id)
                    return _json_response(self, HTTPStatus.OK, {"ok": True})
            if len(segments) == 4 and segments[3] == "reorder" and self.command == "POST":
                payload = self._read_json_body()
                if payload is None:
                    return
                ordered_task_ids = [str(value) for value in payload.get("ordered_task_ids", [])]
                queue = self.app.reorder_session_tasks(queue_id, ordered_task_ids)
                return _json_response(self, HTTPStatus.OK, {"queue": queue.to_dict()})
            if len(segments) == 4 and segments[3] == "tasks" and self.command == "POST":
                payload = self._read_json_body()
                if payload is None:
                    return
                task = self.app.add_session_task(
                    queue_id,
                    title=str(payload.get("title", "")),
                    details=str(payload.get("details", "")),
                )
                return _json_response(self, HTTPStatus.CREATED, {"task": task})
            if len(segments) == 5 and segments[3] == "tasks":
                task_id = segments[4]
                if self.command == "PATCH":
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    task = self.app.update_session_task(
                        queue_id,
                        task_id,
                        title=payload.get("title"),
                        details=payload.get("details"),
                    )
                    return _json_response(self, HTTPStatus.OK, {"task": task.to_dict()})
                if self.command == "DELETE":
                    self.app.delete_session_task(queue_id, task_id)
                    return _json_response(self, HTTPStatus.OK, {"ok": True})
        except KeyError:
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "queue or task not found"})
        except ValueError as exc:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _read_json_body(self) -> dict[str, Any] | None:
        content_length = self.headers.get("Content-Length", "0")
        try:
            byte_count = int(content_length)
        except ValueError:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
            return None
        if byte_count > MAX_REQUEST_BYTES:
            _json_response(self, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
            return None
        raw = self.rfile.read(byte_count)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return None

    def _session_from_cookie(self) -> SessionRecord | None:
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get(COOKIE_NAME)
        if morsel is None:
            return None
        return self.app.sessions.get(morsel.value)

    def _require_session(self, *, require_csrf: bool) -> SessionRecord | None:
        origin_error = self._check_origin(require_auth=True)
        if origin_error is not None:
            _json_response(self, HTTPStatus.FORBIDDEN, {"error": origin_error})
            return None
        session = self._session_from_cookie()
        if session is None:
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            return None
        if require_csrf:
            if self.app.config.disable_csrf_checks:
                return session
            supplied = self.headers.get("X-Mindex-CSRF-Token", "")
            if not hmac.compare_digest(supplied, session.csrf_token):
                _json_response(self, HTTPStatus.FORBIDDEN, {"error": "invalid csrf token"})
                return None
        return session

    def _check_origin(self, *, require_auth: bool) -> str | None:
        if self.command in {"GET", "HEAD", "OPTIONS"}:
            return None
        if self.app.config.disable_origin_checks:
            return None
        origin = self.headers.get("Origin")
        if not origin:
            referer = self.headers.get("Referer")
            if not referer:
                return None if not require_auth else "missing request origin"
            parsed = urlparse(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.app.config.allowed_origins:
            return "origin is not allowed"
        return None


class ConfiguredUiServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], app: MindexUiApp) -> None:
        handler = self._make_handler(app)
        super().__init__(server_address, handler)
        self.app = app

    @staticmethod
    def _make_handler(app: MindexUiApp) -> type[UiRequestHandler]:
        class Handler(UiRequestHandler):
            pass

        Handler.app = app
        return Handler


def create_ui_server(config: UiConfig) -> ConfiguredUiServer:
    server = ConfiguredUiServer((config.host, config.port), MindexUiApp(config))
    bound_host, bound_port = server.server_address
    if bound_port != config.port:
        # Port 0 picks an ephemeral port at bind time, so refresh the in-memory
        # allowlist to match the real listener before any login request arrives.
        updated_config = replace(
            config,
            port=bound_port,
            allowed_origins=_normalize_allowed_origins(
                config.host,
                bound_port,
                list(config.allowed_origins),
                allow_remote=config.allow_remote,
            ),
        )
        server.app.config = updated_config
    return server


def serve_ui(config: UiConfig) -> int:
    server = create_ui_server(config)
    bound_host, bound_port = server.server_address
    print(f"Mindex UI listening on http://{bound_host}:{bound_port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping Mindex UI...", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def _apply_runtime_overrides(
    config: UiConfig,
    *,
    host: str | None = None,
    port: int | None = None,
    disable_origin_checks: bool = False,
    disable_csrf_checks: bool = False,
) -> UiConfig:
    next_host = host or config.host
    next_port = config.port if port is None else port
    next_disable_origin = config.disable_origin_checks or disable_origin_checks
    next_disable_csrf = config.disable_csrf_checks or disable_csrf_checks
    explicit_origins: list[str] = []
    try:
        payload = json.loads(config.config_path.read_text(encoding="utf-8"))
        explicit_origins = [str(value) for value in payload.get("server", {}).get("allowed_origins", [])]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        explicit_origins = []
    return replace(
        config,
        host=next_host,
        port=next_port,
        disable_origin_checks=next_disable_origin,
        disable_csrf_checks=next_disable_csrf,
        allowed_origins=_normalize_allowed_origins(
            next_host,
            next_port,
            explicit_origins,
            allow_remote=config.allow_remote,
        ),
    )


def _build_dev_child_command(config: UiConfig) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mindex",
        "ui",
        "serve",
        "--project-root",
        str(config.project_root),
        "--config",
        str(config.config_path),
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--disable-origin-checks",
        "--disable-csrf-checks",
    ]


def _build_dev_child_env() -> dict[str, str]:
    env = os.environ.copy()
    source_root = Path(__file__).resolve().parents[1]
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{source_root}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(source_root)
    )
    env[DEV_OVERRIDE_ENV] = "1"
    return env


def _watch_state(paths: Iterable[Path]) -> dict[Path, int | None]:
    state: dict[Path, int | None] = {}
    for path in paths:
        try:
            state[path] = path.stat().st_mtime_ns
        except FileNotFoundError:
            state[path] = None
    return state


def _changed_watch_paths(before: dict[Path, int | None], after: dict[Path, int | None]) -> list[Path]:
    changed: list[Path] = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            changed.append(path)
    return changed


def _default_dev_watch_paths(config: UiConfig) -> tuple[Path, ...]:
    source_root = Path(__file__).resolve().parent
    paths = {config.config_path.resolve()}
    paths.update(sorted(source_root.glob("*.py")))
    return tuple(sorted(paths))


def _stop_dev_child(process: subprocess.Popen[str], *, timeout: float = 2.0) -> int:
    if process.poll() is not None:
        return process.returncode or 0
    process.terminate()
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=timeout)


def serve_ui_dev(
    config: UiConfig,
    *,
    watch_paths: Iterable[Path] | None = None,
    poll_interval: float = DEFAULT_DEV_POLL_SECONDS,
    popen_factory: Any = subprocess.Popen,
    watch_state_loader: Any = _watch_state,
) -> int:
    watched_paths = tuple(watch_paths or _default_dev_watch_paths(config))
    previous_state = watch_state_loader(watched_paths)
    child_env = _build_dev_child_env()
    command = _build_dev_child_command(config)
    start_new_session = os.name != "nt"
    print(
        f"Starting Mindex UI dev mode with auto-restart; watching {len(watched_paths)} files and disabling origin/CSRF checks in the child server.",
        file=sys.stderr,
    )
    child: subprocess.Popen[str] | None = None
    try:
        while True:
            child = popen_factory(
                command,
                cwd=str(config.project_root),
                env=child_env,
                start_new_session=start_new_session,
            )
            while True:
                current_state = watch_state_loader(watched_paths)
                changed_paths = _changed_watch_paths(previous_state, current_state)
                if changed_paths:
                    previous_state = current_state
                    print(f"Detected UI code change in {changed_paths[0]}; restarting dev server.", file=sys.stderr)
                    _stop_dev_child(child)
                    child = None
                    break
                returncode = child.poll()
                if returncode is not None:
                    return returncode
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("Stopping Mindex UI dev mode...", file=sys.stderr)
        return 0
    finally:
        if child is not None:
            _stop_dev_child(child)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mindex web UI controls")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-config", help="Create or migrate the local Mindex UI config")
    _add_ui_config_arguments(init_parser)

    reset_parser = subparsers.add_parser("reset-config", help="Rewrite the local Mindex UI config from scratch")
    _add_ui_config_arguments(reset_parser)

    serve_parser = subparsers.add_parser("serve", help="Serve the local Mindex UI")
    serve_parser.add_argument("--project-root", help="Project root to manage; defaults to the detected workspace")
    serve_parser.add_argument("--config", help="Override the UI config path")
    serve_parser.add_argument("--host", help="Override the configured host")
    serve_parser.add_argument("--port", type=int, help="Override the configured port")
    serve_parser.add_argument(
        "--dev",
        action="store_true",
        help="Run a watched development server that auto-restarts on Mindex UI code changes and disables origin/CSRF checks in the child process",
    )
    serve_parser.add_argument(
        "--disable-origin-checks",
        action="store_true",
        help="Disable Origin/Referer enforcement for authenticated UI requests",
    )
    serve_parser.add_argument(
        "--disable-csrf-checks",
        action="store_true",
        help="Disable CSRF-token enforcement for authenticated state-changing UI requests",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = Path(args.project_root).resolve() if getattr(args, "project_root", None) else find_project_root()

    if args.command == "init-config":
        resolved_config_path = _resolve_ui_config_path(project_root, args.config)
        if args.password is None and _password_prompt_needed(resolved_config_path):
            args.password = _prompt_for_password()
        bootstrap = load_or_create_ui_config(
            project_root=project_root,
            config_path=resolved_config_path,
            username=args.username,
            password=args.password,
            host=args.host,
            port=args.port,
            title=args.title,
            allow_remote=args.allow_remote,
            disable_origin_checks=args.disable_origin_checks,
            disable_csrf_checks=args.disable_csrf_checks,
        )
        payload = _config_as_payload(bootstrap.config)
        if bootstrap.generated_password:
            print(f"Generated UI password: {bootstrap.generated_password}", file=sys.stderr)
        elif bootstrap.migrated_legacy_config:
            print("Migrated the UI config to the secure password-hash format.", file=sys.stderr)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "reset-config":
        bootstrap = reset_ui_config(
            project_root=project_root,
            config_path=args.config,
            username=args.username,
            password=args.password,
            host=args.host,
            port=args.port,
            title=args.title,
            allow_remote=args.allow_remote,
            disable_origin_checks=args.disable_origin_checks,
            disable_csrf_checks=args.disable_csrf_checks,
        )
        payload = _config_as_payload(bootstrap.config)
        if bootstrap.generated_password:
            print(f"Generated UI password: {bootstrap.generated_password}", file=sys.stderr)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "serve":
        bootstrap = load_or_create_ui_config(project_root=project_root, config_path=args.config)
        config = _apply_runtime_overrides(
            bootstrap.config,
            host=args.host,
            port=args.port,
            disable_origin_checks=args.disable_origin_checks,
            disable_csrf_checks=args.disable_csrf_checks,
        )
        if os.environ.get(DEV_OVERRIDE_ENV) != "1" and (
            args.host or args.port is not None or args.disable_origin_checks or args.disable_csrf_checks
        ):
            payload = json.loads(config.config_path.read_text(encoding="utf-8"))
            server_payload = payload.setdefault("server", {})
            server_payload["host"] = config.host
            server_payload["port"] = config.port
            server_payload["disable_origin_checks"] = config.disable_origin_checks
            server_payload["disable_csrf_checks"] = config.disable_csrf_checks
            _write_private_json(config.config_path, payload)
        if bootstrap.generated_password:
            print(f"Generated UI password: {bootstrap.generated_password}", file=sys.stderr)
        if args.dev and os.environ.get(DEV_OVERRIDE_ENV) != "1":
            return serve_ui_dev(config)
        return serve_ui(config)

    parser.error(f"unsupported command: {args.command}")
    return 2


__all__ = [
    "COOKIE_NAME",
    "ConfiguredUiServer",
    "MindexUiApp",
    "UiBootstrap",
    "UiConfig",
    "create_ui_server",
    "load_or_create_ui_config",
    "main",
    "reset_ui_config",
    "serve_ui",
]
