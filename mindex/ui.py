from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import hashlib
import hmac
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, Iterable
from urllib.parse import urlparse

from mindex.launcher import find_project_root
from mindex.task_queue import AgentManager, AgentRecord, QueueRecord, StateStore, TaskQueueManager, TaskRecord


MAX_REQUEST_BYTES = 65536
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_LOGIN_WINDOW_SECONDS = 5 * 60
DEFAULT_LOGIN_ATTEMPTS = 5
PASSWORD_ITERATIONS = 390000
COOKIE_NAME = "mindex_session"
DEV_OVERRIDE_ENV = "MINDEX_UI_EPHEMERAL_OVERRIDES"
DEFAULT_DEV_POLL_SECONDS = 0.5


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


def _normalize_allowed_origins(host: str, port: int, explicit: list[str]) -> tuple[str, ...]:
    defaults = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if host not in {"127.0.0.1", "localhost"}:
        defaults.append(f"http://{host}:{port}")
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
        allowed_origins=_normalize_allowed_origins(host, port, list(server_payload.get("allowed_origins", []))),
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

    def verify_password(self, password: str) -> bool:
        salt = bytes.fromhex(self.config.password_salt)
        supplied = _hash_password(password, salt=salt, iterations=self.config.password_iterations)
        return hmac.compare_digest(self.config.password_hash, supplied)

    def create_session(self) -> SessionRecord:
        return self.sessions.create(self.config.username)

    def create_managed_session(
        self,
        *,
        name: str,
        command_args: list[str] | None = None,
        workdir: Path | str,
        queue_description: str = "",
    ) -> dict[str, Any]:
        session_name = name.strip() or "Untitled session"
        resolved_command_args = command_args or ["exec", session_name]
        queue = self.task_queue_manager.create_queue(
            name=session_name,
            description=queue_description or f"Queue for {session_name}.",
        )
        try:
            agent = self.agent_manager.create_agent(
                name=session_name,
                description=queue_description,
                command_args=resolved_command_args,
                workdir=workdir,
                queue_id=queue.queue_id,
            )
        except Exception:
            self.task_queue_manager.delete_queue(queue.queue_id)
            raise
        return self._session_payload(agent, queue)

    def _agent_for_queue(self, queue_id: str) -> AgentRecord | None:
        for agent in self.agent_manager.list_agents():
            if agent.queue_id == queue_id:
                return agent
        return None

    def _task_command_args(self, agent: AgentRecord, task: TaskRecord) -> list[str]:
        if any("{task}" in argument for argument in agent.command_args):
            return self._normalize_exec_task_args([argument.replace("{task}", task.title) for argument in agent.command_args])
        if agent.command_args[:1] == ["exec"]:
            if len(agent.command_args) == 1:
                return self._normalize_exec_task_args([*agent.command_args, task.title])
            if len(agent.command_args) == 2 and agent.command_args[1] == agent.name:
                return self._normalize_exec_task_args(["exec", task.title])
        return self._normalize_exec_task_args(list(agent.command_args))

    def _normalize_exec_task_args(self, command_args: list[str]) -> list[str]:
        if command_args[:1] != ["exec"]:
            return command_args
        if "--skip-git-repo-check" in command_args[1:]:
            return command_args
        return ["exec", "--skip-git-repo-check", *command_args[1:]]

    def _handle_session_run_finished(self, agent: AgentRecord) -> None:
        if agent.current_task_id and agent.queue_id:
            try:
                if agent.stop_requested:
                    self.task_queue_manager.requeue_task_to_front(agent.queue_id, agent.current_task_id)
                else:
                    final_status = "completed" if agent.status == "completed" else "failed"
                    self.task_queue_manager.update_task(agent.queue_id, agent.current_task_id, status=final_status)
            except KeyError:
                pass
        try:
            self.agent_manager.clear_current_task(agent.agent_id)
        except KeyError:
            return
        if agent.stop_requested:
            return
        self._start_next_queued_task(agent.agent_id)

    def _start_next_queued_task(self, agent_id: str) -> dict[str, Any] | None:
        agent = self.agent_manager.get_agent(agent_id)
        if agent is None or agent.status == "running" or not agent.queue_id:
            return None
        try:
            queue = self.task_queue_manager.get_queue(agent.queue_id)
        except KeyError:
            return None
        if any(task.status == "running" for task in queue.tasks):
            return None
        next_task = next((task for task in queue.tasks if task.status == "queued"), None)
        if next_task is None:
            return None
        running_task = self.task_queue_manager.update_task(queue.queue_id, next_task.task_id, status="running")
        command_args = self._task_command_args(agent, running_task)
        self.agent_manager._start_agent(
            agent_id,
            run_command_args=command_args,
            current_task_id=running_task.task_id,
            on_exit=self._handle_session_run_finished,
        )
        return self.task_queue_manager.get_task(queue.queue_id, running_task.task_id).to_dict()

    def add_session_task(self, queue_id: str, *, title: str, details: str = "") -> dict[str, Any]:
        created = self.task_queue_manager.add_task(queue_id, title=title, details=details, status="queued")
        agent = self._agent_for_queue(queue_id)
        if agent is not None and agent.status != "running":
            self._start_next_queued_task(agent.agent_id)
        return self.task_queue_manager.get_task(queue_id, created.task_id).to_dict()

    def start_managed_session(self, agent_id: str) -> dict[str, Any]:
        started_task = self._start_next_queued_task(agent_id)
        agent = self.agent_manager.get_agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        if started_task is None and agent.status != "running":
            raise ValueError("queue is empty")
        queue: QueueRecord | None = None
        if agent.queue_id:
            try:
                queue = self.task_queue_manager.get_queue(agent.queue_id)
            except KeyError:
                queue = None
        return self._session_payload(agent, queue)

    def stop_managed_session(self, agent_id: str, *, settle_timeout: float = 1.0) -> dict[str, Any]:
        self.agent_manager.stop_agent(agent_id)
        deadline = time.time() + settle_timeout
        while time.time() < deadline:
            agent = self.agent_manager.get_agent(agent_id)
            if agent is None:
                raise KeyError(agent_id)
            if not agent.current_task_id and agent.status != "running":
                break
            time.sleep(0.05)
        agent = self.agent_manager.get_agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        queue: QueueRecord | None = None
        if agent.queue_id:
            try:
                queue = self.task_queue_manager.get_queue(agent.queue_id)
            except KeyError:
                queue = None
        return self._session_payload(agent, queue)

    def delete_managed_session(self, agent_id: str) -> None:
        agent = self.agent_manager.get_agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        if agent.status == "running":
            raise ValueError("stop the session before deleting it")
        self.agent_manager.delete_agent(agent_id)
        if agent.queue_id:
            try:
                self.task_queue_manager.delete_queue(agent.queue_id)
            except KeyError:
                pass

    def _read_output(self, log_path: str | None, *, max_bytes: int = 6000) -> str:
        if not log_path:
            return ""
        path = Path(log_path)
        if not path.exists():
            return ""
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(size - max_bytes, 0))
                data = handle.read()
        except OSError:
            return ""
        text = data.decode("utf-8", errors="replace")
        if len(text.encode("utf-8", errors="replace")) >= max_bytes and "\n" in text:
            text = text.split("\n", 1)[1]
        return text.strip()

    def _session_payload(self, agent: Any, queue: Any | None) -> dict[str, Any]:
        payload = agent.to_dict() if hasattr(agent, "to_dict") else dict(agent)
        payload["agent_status"] = payload.get("status", "stopped")
        payload["status"] = "running" if payload.get("agent_status") == "running" else "stopped"
        queue_payload = queue.to_dict() if queue is not None and hasattr(queue, "to_dict") else (queue or {})
        payload["queue"] = queue_payload
        payload["output"] = self._read_output(payload.get("log_path"))
        return payload

    def list_session_payloads(self) -> list[dict[str, Any]]:
        agents = self.agent_manager.list_agents()
        queues_by_id = {queue.queue_id: queue for queue in self.task_queue_manager.list_queues()}
        sessions = [self._session_payload(agent, queues_by_id.get(agent.queue_id)) for agent in agents]
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
      <p class=\"lede\">A minimal browser view for Mindex sessions, their queue order, and their visible output.</p>
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
const state = { csrfToken: null };

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

function renderSessionCard(session) {
  const queue = session.queue || {};
  const tasks = queue.tasks || [];
  const running = session.status === 'running';
  const output = session.output || 'No output yet.';
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
              <p class="muted">${escapeHtml(queue.description || 'Edit this queue and drag tasks into the right order.')}</p>
            </div>
            ${queue.queue_id ? `<button class="ghost" type="button" data-edit-queue="${escapeHtml(queue.queue_id)}" data-queue-name="${escapeHtml(queue.name || '')}" data-queue-description="${escapeHtml(queue.description || '')}">Edit queue</button>` : ''}
          </div>
          ${queue.queue_id ? `
            <ul class="task-list" data-task-list="${escapeHtml(queue.queue_id)}">
              ${tasks.length ? tasks.map((task, index) => renderTaskCard(queue.queue_id, task, index === 0 && task.status === 'running')).join('') : '<li class="empty-state">No queue items yet.</li>'}
            </ul>
            <form class="stack" data-task-form="${escapeHtml(queue.queue_id)}">
              <label>Task title<input name="title" placeholder="Review failing output" required></label>
              <label>Task details<textarea name="details" placeholder="Acceptance notes or extra context."></textarea></label>
              <div class="button-row"><button class="secondary" type="submit">Add queue item</button></div>
            </form>
          ` : '<div class="empty-state">This legacy session does not have a queue attached.</div>'}
        </section>
        <section class="output-card">
          <div>
            <p class="kicker">Output</p>
            <h3>Visible session output</h3>
          </div>
          <pre class="output-text">${escapeHtml(output)}</pre>
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
          <p class="muted">Each session owns a queue and shows its output in place.</p>
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
  const form = new FormData(formElement);
  try {
    await api(`/api/queues/${queueId}/tasks`, {
      method: 'POST',
      body: JSON.stringify({
        title: form.get('title'),
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

async function loadDashboard() {
  try {
    const payload = await api('/api/status');
    if (payload.csrf_token) {
      state.csrfToken = payload.csrf_token;
    }
    renderDashboard(payload);
  } catch (error) {
    renderLogin(error.message === 'authentication required' ? '' : error.message);
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
            command_args = shlex.split(str(payload.get("command_args", ""))) if "command_args" in payload else []
            session_payload = self.app.create_managed_session(
                name=str(payload.get("name", "")),
                command_args=command_args,
                workdir=str(payload.get("workdir", self.app.config.project_root)),
                queue_description=str(payload.get("queue_description", "")),
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
                queue = self.app.task_queue_manager.reorder_tasks(queue_id, ordered_task_ids)
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
                    task = self.app.task_queue_manager.update_task(
                        queue_id,
                        task_id,
                        title=payload.get("title"),
                        details=payload.get("details"),
                    )
                    return _json_response(self, HTTPStatus.OK, {"task": task.to_dict()})
                if self.command == "DELETE":
                    self.app.task_queue_manager.delete_task(queue_id, task_id)
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
            allowed_origins=_normalize_allowed_origins(config.host, bound_port, list(config.allowed_origins)),
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
        allowed_origins=_normalize_allowed_origins(next_host, next_port, explicit_origins),
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
    init_parser.add_argument("--project-root", help="Project root to manage; defaults to the detected workspace")
    init_parser.add_argument("--config", help="Override the UI config path")
    init_parser.add_argument("--username", default="admin", help="Admin username")
    init_parser.add_argument("--password", help="Admin password; if omitted, a random password is generated")
    init_parser.add_argument("--host", default="127.0.0.1", help="Host to bind; defaults to localhost")
    init_parser.add_argument("--port", type=int, default=8765, help="Port to bind")
    init_parser.add_argument("--title", default="Mindex Control Deck", help="Browser title for the UI")
    init_parser.add_argument("--allow-remote", action="store_true", help="Allow non-localhost binding")
    init_parser.add_argument(
        "--disable-origin-checks",
        action="store_true",
        help="Disable Origin/Referer enforcement for authenticated UI requests",
    )
    init_parser.add_argument(
        "--disable-csrf-checks",
        action="store_true",
        help="Disable CSRF-token enforcement for authenticated state-changing UI requests",
    )

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
        bootstrap = load_or_create_ui_config(
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
        payload = asdict(bootstrap.config)
        for key, value in list(payload.items()):
            if isinstance(value, Path):
                payload[key] = str(value)
        if bootstrap.generated_password:
            print(f"Generated UI password: {bootstrap.generated_password}", file=sys.stderr)
        elif bootstrap.migrated_legacy_config:
            print("Migrated the UI config to the secure password-hash format.", file=sys.stderr)
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
    "serve_ui",
]
