from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
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
from typing import Any
from urllib.parse import urlparse

from mindex.launcher import find_project_root
from mindex.task_queue import AgentManager, StateStore, TaskQueueManager


MAX_REQUEST_BYTES = 65536
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_LOGIN_WINDOW_SECONDS = 5 * 60
DEFAULT_LOGIN_ATTEMPTS = 5
PASSWORD_ITERATIONS = 390000
COOKIE_NAME = "mindex_session"


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
        self.task_queue_manager.ensure_default_queue()
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
        agents = [agent.to_dict() for agent in self.agent_manager.list_agents()]
        queues = [queue.to_dict() for queue in self.task_queue_manager.list_queues()]
        return {
            "project_root": str(self.config.project_root),
            "branch": branch,
            "config_path": str(self.config.config_path),
            "title": self.config.title,
            "agent_count": len(agents),
            "running_count": sum(1 for agent in agents if agent["status"] == "running"),
            "state_file": str(self.config.state_file),
            "queue_log_dir": str(self.config.queue_log_dir),
            "allowed_origins": list(self.config.allowed_origins),
            "allow_remote": self.config.allow_remote,
            "security": {
                "localhost_only": not self.config.allow_remote,
                "csrf_protected": True,
                "rate_limited_logins": True,
                "hashed_password_store": True,
            },
            "recent_runs": self.recent_runs(),
            "queues": queues,
            "agents": agents,
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
      <p class=\"lede\">A local control room for Mindex, queued coding agents, and session task queues.</p>
    </header>
    <main id=\"app\" class=\"app\"></main>
  </div>
  <script src=\"/static/app.js\"></script>
</body>
</html>
"""


APP_CSS = """
:root {
  --paper: #f4efe3;
  --ink: #16130f;
  --muted: #61584f;
  --panel: rgba(250, 245, 234, 0.9);
  --panel-strong: rgba(255, 251, 243, 0.96);
  --line: rgba(38, 26, 12, 0.12);
  --accent: #b3522f;
  --accent-strong: #7e2e17;
  --sage: #27423a;
  --danger: #8e2430;
  --shadow: 0 18px 60px rgba(29, 18, 10, 0.12);
  --heading: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
  --body: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--body);
  color: var(--ink);
  background:
    radial-gradient(circle at top left, rgba(203, 159, 64, 0.28), transparent 36%),
    radial-gradient(circle at top right, rgba(179, 82, 47, 0.18), transparent 34%),
    linear-gradient(180deg, #efe8da 0%, #f8f3eb 45%, #efe6d7 100%);
  min-height: 100vh;
}
.shell {
  width: min(1200px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 32px 0 40px;
}
.hero {
  padding: 28px;
  border: 1px solid var(--line);
  background: linear-gradient(140deg, rgba(255,255,255,0.66), rgba(244, 235, 218, 0.9));
  border-radius: 28px;
  box-shadow: var(--shadow);
  position: relative;
  overflow: hidden;
}
.hero::after {
  content: "";
  position: absolute;
  inset: auto -10% -25% 45%;
  height: 180px;
  background: linear-gradient(135deg, rgba(39, 66, 58, 0.15), rgba(203, 159, 64, 0));
  transform: rotate(-4deg);
}
.eyebrow {
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--accent-strong);
  font-size: 0.72rem;
  margin: 0 0 10px;
}
.hero h1 {
  font-family: var(--heading);
  margin: 0;
  font-size: clamp(2.4rem, 5vw, 4.5rem);
  line-height: 0.92;
  max-width: 8ch;
}
.lede {
  max-width: 48rem;
  color: var(--muted);
  margin: 16px 0 0;
  font-size: 1.03rem;
}
.app {
  margin-top: 24px;
  display: grid;
  gap: 20px;
}
.panel, .queue-card, .agent-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 22px;
  box-shadow: var(--shadow);
}
.panel {
  animation: rise 380ms ease both;
}
.panel:nth-child(2) { animation-delay: 60ms; }
.panel:nth-child(3) { animation-delay: 120ms; }
.panel:nth-child(4) { animation-delay: 180ms; }
@keyframes rise {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}
.stack {
  display: grid;
  gap: 16px;
}
.grid {
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}
.kicker {
  margin: 0 0 6px;
  color: var(--muted);
  font-size: 0.78rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}
.metric {
  font-family: var(--heading);
  font-size: 2.3rem;
  margin: 0;
}
label {
  display: grid;
  gap: 8px;
  font-size: 0.92rem;
  color: var(--muted);
}
input, textarea, select {
  width: 100%;
  border-radius: 14px;
  border: 1px solid rgba(22, 19, 15, 0.16);
  background: var(--panel-strong);
  color: var(--ink);
  padding: 12px 14px;
  font: inherit;
}
textarea { min-height: 110px; resize: vertical; }
button {
  border: 0;
  border-radius: 999px;
  padding: 12px 18px;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  transition: transform 140ms ease, opacity 140ms ease;
}
button:hover { transform: translateY(-1px); }
button:disabled { opacity: 0.45; cursor: wait; transform: none; }
.button-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.primary { background: var(--accent); color: #fff7f1; }
.secondary { background: rgba(39, 66, 58, 0.12); color: var(--sage); }
.ghost { background: rgba(22, 19, 15, 0.06); color: var(--ink); }
.danger { background: var(--danger); color: #fff4f5; }
.row-between {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.status-pill {
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  background: rgba(39, 66, 58, 0.1);
  color: var(--sage);
}
.status-failed { background: rgba(142, 36, 48, 0.12); color: var(--danger); }
.status-running, .status-in-progress { background: rgba(203, 159, 64, 0.18); color: #8a5c00; }
.status-queued, .status-pending { background: rgba(22, 19, 15, 0.08); color: var(--muted); }
.status-disconnected, .status-blocked { background: rgba(98, 77, 44, 0.12); color: #7e5f2c; }
.status-done, .status-completed { background: rgba(39, 66, 58, 0.12); color: var(--sage); }
.meta, .agent-description, .muted {
  color: var(--muted);
}
code {
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 0.88rem;
}
.notice {
  border: 1px solid rgba(179, 82, 47, 0.2);
  background: rgba(179, 82, 47, 0.09);
  color: var(--accent-strong);
  border-radius: 18px;
  padding: 14px 16px;
}
.hidden { display: none !important; }
.login-shell {
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  gap: 20px;
}
.login-card {
  max-width: 460px;
}
.login-aside {
  background: linear-gradient(160deg, rgba(39, 66, 58, 0.14), rgba(203, 159, 64, 0.08));
}
.run-list, .security-list, .task-list {
  display: grid;
  gap: 10px;
}
.run-item, .security-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid rgba(22, 19, 15, 0.08);
}
.run-item:first-child, .security-item:first-child { border-top: 0; padding-top: 0; }
.banner {
  border-left: 4px solid var(--accent);
  padding-left: 12px;
  color: var(--muted);
}
.task-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.task-item {
  display: grid;
  gap: 10px;
  border: 1px solid rgba(22, 19, 15, 0.08);
  border-radius: 16px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.72);
  cursor: grab;
}
.task-item.dragging {
  opacity: 0.55;
  transform: scale(0.99);
}
.task-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.task-title {
  margin: 0;
  font-weight: 700;
}
.task-details {
  margin: 0;
  color: var(--muted);
  white-space: pre-wrap;
}
.queue-empty {
  margin: 0;
  color: var(--muted);
}
@media (max-width: 860px) {
  .login-shell { grid-template-columns: 1fr; }
  .shell { width: min(100vw - 18px, 1200px); padding-top: 18px; }
  .hero, .panel, .queue-card, .agent-card { border-radius: 22px; }
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

function renderLogin(message = '') {
  const app = document.getElementById('app');
  app.innerHTML = `
    <section class="panel login-shell">
      <div class="panel login-card">
        <p class="kicker">Authenticate</p>
        <h2>Open the control deck</h2>
        <p class="muted">Use the credentials stored in <code>.mindex/ui_config.json</code>. The server stores a salted password hash instead of a plaintext secret.</p>
        <form id="login-form" class="stack">
          <label>Username<input name="username" autocomplete="username" required value="admin"></label>
          <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
          <div class="button-row"><button class="primary" type="submit">Sign in</button></div>
          <p id="login-error" class="notice ${message ? '' : 'hidden'}">${escapeHtml(message)}</p>
        </form>
      </div>
      <aside class="panel login-aside">
        <p class="kicker">Security posture</p>
        <div class="security-list">
          <div class="security-item"><span>Loopback by default</span><strong>Yes</strong></div>
          <div class="security-item"><span>CSRF checks</span><strong>Enabled</strong></div>
          <div class="security-item"><span>Rate-limited logins</span><strong>Enabled</strong></div>
          <div class="security-item"><span>Shell injection surface</span><strong>Reduced</strong></div>
        </div>
      </aside>
    </section>`;
  document.getElementById('login-form').addEventListener('submit', submitLogin);
}

async function submitLogin(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
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

function renderQueueCard(queue) {
  const tasks = queue.tasks || [];
  return `
    <article class="queue-card stack">
      <div class="row-between">
        <div>
          <h2>${escapeHtml(queue.name)}</h2>
          <p class="muted">${escapeHtml(queue.description || 'No queue description yet.')}</p>
        </div>
        <div class="button-row">
          <button class="ghost" type="button" data-rename-queue="${escapeHtml(queue.queue_id)}">Edit queue</button>
          <button class="danger" type="button" data-delete-queue="${escapeHtml(queue.queue_id)}">Delete queue</button>
        </div>
      </div>
      <ul class="task-list" data-task-list="${escapeHtml(queue.queue_id)}">
        ${tasks.length ? tasks.map(task => renderTaskCard(queue.queue_id, task)).join('') : '<li class="queue-empty">No tasks yet. Add one below.</li>'}
      </ul>
      <form class="stack" data-task-form="${escapeHtml(queue.queue_id)}">
        <label>Task title<input name="title" placeholder="Audit queue persistence" required></label>
        <label>Task details<textarea name="details" placeholder="Notes, acceptance criteria, or reminders."></textarea></label>
        <label>Status<select name="status"><option value="pending">Pending</option><option value="in_progress">In progress</option><option value="blocked">Blocked</option><option value="done">Done</option></select></label>
        <div class="button-row"><button class="primary" type="submit">Add task</button></div>
      </form>
    </article>`;
}

function renderTaskCard(queueId, task) {
  return `
    <li class="task-item" draggable="true" data-task-id="${escapeHtml(task.task_id)}" data-queue-id="${escapeHtml(queueId)}">
      <div class="task-head">
        <div>
          <p class="task-title">${escapeHtml(task.title)}</p>
          <p class="task-details">${escapeHtml(task.details || 'No extra details.')}</p>
        </div>
        <div class="${statusClass(task.status)}">${escapeHtml(String(task.status || 'pending').replace('_', ' '))}</div>
      </div>
      <div class="button-row">
        <button class="ghost" type="button" data-queue-id="${escapeHtml(queueId)}" data-edit-task="${escapeHtml(task.task_id)}">Edit</button>
        <button class="danger" type="button" data-queue-id="${escapeHtml(queueId)}" data-delete-task="${escapeHtml(task.task_id)}">Delete</button>
      </div>
    </li>`;
}

function renderAgentCard(agent) {
  const running = agent.status === 'running';
  return `
    <article class="agent-card stack">
      <div class="row-between">
        <div>
          <h3>${escapeHtml(agent.name)}</h3>
          <p class="agent-description">${escapeHtml(agent.description || 'No description provided.')}</p>
        </div>
        <div class="${statusClass(agent.status)}">${escapeHtml(agent.status)}</div>
      </div>
      <p class="meta"><strong>Args:</strong> <code>${escapeHtml((agent.command_args || []).join(' '))}</code></p>
      <p class="meta"><strong>Workdir:</strong> <code>${escapeHtml(agent.workdir)}</code></p>
      ${agent.log_path ? `<p class="meta"><strong>Log:</strong> <code>${escapeHtml(agent.log_path)}</code></p>` : ''}
      ${agent.last_error ? `<p class="notice">${escapeHtml(agent.last_error)}</p>` : ''}
      <div class="button-row">
        <button class="primary" ${running ? 'disabled' : ''} data-start-agent="${escapeHtml(agent.agent_id)}">Start</button>
        <button class="ghost" ${!running ? 'disabled' : ''} data-stop-agent="${escapeHtml(agent.agent_id)}">Stop</button>
        <button class="danger" ${running ? 'disabled' : ''} data-delete-agent="${escapeHtml(agent.agent_id)}">Delete</button>
      </div>
    </article>`;
}

function renderDashboard(payload) {
  const app = document.getElementById('app');
  const agents = payload.agents || [];
  const queues = payload.queues || [];
  const recentRuns = payload.recent_runs || [];
  app.innerHTML = `
    <section class="grid">
      <article class="panel">
        <p class="kicker">Workspace</p>
        <p class="metric">${payload.agent_count}</p>
        <p class="muted">Registered agents in <code>${escapeHtml(payload.project_root)}</code></p>
      </article>
      <article class="panel">
        <p class="kicker">Task queues</p>
        <p class="metric">${queues.length}</p>
        <p class="muted">Drag tasks within a queue to reprioritize upcoming work.</p>
      </article>
      <article class="panel">
        <p class="kicker">Live sessions</p>
        <p class="metric">${payload.running_count}</p>
        <p class="muted">Running on branch <code>${escapeHtml(payload.branch)}</code></p>
      </article>
      <article class="panel">
        <p class="kicker">Exposure</p>
        <p class="metric">${payload.allow_remote ? 'Remote' : 'Local'}</p>
        <p class="muted">Allowed origins: <code>${escapeHtml((payload.allowed_origins || []).join(', '))}</code></p>
      </article>
    </section>
    <section class="grid">
      <article class="panel stack">
        <div class="row-between"><div><p class="kicker">Queue design</p><h2>Upcoming session work</h2></div><button id="refresh-button" class="ghost">Refresh</button></div>
        <p class="banner">Queues persist under <code>.mindex/task_queues.json</code>. Add, edit, delete, and reorder tasks from the browser.</p>
        <form id="queue-form" class="stack">
          <label>Queue name<input name="name" placeholder="Release hardening" required></label>
          <label>Queue description<textarea name="description" placeholder="What this queue is coordinating."></textarea></label>
          <div class="button-row"><button class="primary" type="submit">Create queue</button></div>
          <p id="queue-form-error" class="notice hidden"></p>
        </form>
      </article>
      <article class="panel stack">
        <p class="kicker">Recent Mindex runs</p>
        <h2>Observed activity</h2>
        <div class="run-list">${recentRuns.length ? recentRuns.map(run => `
          <div class="run-item">
            <div><strong>${escapeHtml(run.prompt)}</strong><div class="muted"><code>${escapeHtml(run.run_dir)}</code></div></div>
            <div class="${statusClass(run.status)}">${escapeHtml(run.status)}</div>
          </div>`).join('') : '<p class="muted">No recorded runs yet.</p>'}
        </div>
      </article>
    </section>
    <section class="panel stack">
      <div class="row-between"><div><p class="kicker">Task queues</p><h2>Session backlog</h2></div><button id="logout-button" class="secondary">Logout</button></div>
      <div class="grid">${queues.map(renderQueueCard).join('')}</div>
    </section>
    <section class="grid">
      <article class="panel stack">
        <p class="kicker">Launch agent</p>
        <h2>Queue a new Mindex job</h2>
        <p class="banner">Agents run as <code>python -m mindex ...</code> without a shell. Keep workdirs inside the configured project root.</p>
        <form id="agent-form" class="stack">
          <label>Agent name<input name="name" placeholder="Docs hardening pass" required></label>
          <label>Description<textarea name="description" placeholder="What this coding agent is responsible for."></textarea></label>
          <label>Mindex arguments<input name="command_args" placeholder="--version or exec --json \"triage repo\"" required></label>
          <label>Working directory<input name="workdir" value="${escapeHtml(payload.project_root)}" required></label>
          <label>Feature branch<input name="feature_branch" placeholder="mindex/secure-ui-agent"></label>
          <label><input type="checkbox" name="auto_publish" checked> Auto-publish with Mindex defaults</label>
          <div class="button-row"><button class="primary" type="submit">Create agent</button></div>
          <p id="agent-form-error" class="notice hidden"></p>
        </form>
      </article>
      <article class="panel stack">
        <p class="kicker">Agent roster</p>
        <h2>Managed coding agents</h2>
        <div class="grid">${agents.length ? agents.map(renderAgentCard).join('') : '<p class="muted">No agents yet. Create one to start directing Mindex jobs from the browser.</p>'}</div>
      </article>
    </section>`;

  document.getElementById('queue-form').addEventListener('submit', submitQueue);
  document.getElementById('agent-form').addEventListener('submit', submitAgent);
  document.getElementById('refresh-button').addEventListener('click', loadDashboard);
  document.getElementById('logout-button').addEventListener('click', logout);
  document.querySelectorAll('[data-rename-queue]').forEach(node => node.addEventListener('click', () => renameQueue(node.dataset.renameQueue)));
  document.querySelectorAll('[data-delete-queue]').forEach(node => node.addEventListener('click', () => removeQueue(node.dataset.deleteQueue)));
  document.querySelectorAll('[data-task-form]').forEach(node => node.addEventListener('submit', submitTask));
  document.querySelectorAll('[data-edit-task]').forEach(node => node.addEventListener('click', () => editTask(node.dataset.queueId, node.dataset.editTask)));
  document.querySelectorAll('[data-delete-task]').forEach(node => node.addEventListener('click', () => removeTask(node.dataset.queueId, node.dataset.deleteTask)));
  document.querySelectorAll('[data-start-agent]').forEach(node => node.addEventListener('click', () => changeAgent(node.dataset.startAgent, 'start')));
  document.querySelectorAll('[data-stop-agent]').forEach(node => node.addEventListener('click', () => changeAgent(node.dataset.stopAgent, 'stop')));
  document.querySelectorAll('[data-delete-agent]').forEach(node => node.addEventListener('click', () => changeAgent(node.dataset.deleteAgent, 'delete')));
  bindTaskDragAndDrop();
}

async function submitQueue(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const errorNode = document.getElementById('queue-form-error');
  errorNode.classList.add('hidden');
  try {
    await api('/api/queues', {
      method: 'POST',
      body: JSON.stringify({ name: form.get('name'), description: form.get('description') }),
    });
    event.currentTarget.reset();
    await loadDashboard();
  } catch (error) {
    errorNode.textContent = error.message;
    errorNode.classList.remove('hidden');
  }
}

async function renameQueue(queueId) {
  const name = window.prompt('Queue name');
  if (name === null) {
    return;
  }
  const description = window.prompt('Queue description (optional)', '');
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

async function removeQueue(queueId) {
  if (!window.confirm('Delete this queue and its tasks?')) {
    return;
  }
  try {
    await api(`/api/queues/${queueId}`, { method: 'DELETE' });
    await loadDashboard();
  } catch (error) {
    alert(error.message);
  }
}

async function submitTask(event) {
  event.preventDefault();
  const queueId = event.currentTarget.dataset.taskForm;
  const form = new FormData(event.currentTarget);
  try {
    await api(`/api/queues/${queueId}/tasks`, {
      method: 'POST',
      body: JSON.stringify({
        title: form.get('title'),
        details: form.get('details'),
        status: form.get('status'),
      }),
    });
    event.currentTarget.reset();
    await loadDashboard();
  } catch (error) {
    alert(error.message);
  }
}

async function editTask(queueId, taskId) {
  const title = window.prompt('Task title');
  if (title === null) {
    return;
  }
  const details = window.prompt('Task details (optional)', '');
  const status = window.prompt('Status: pending, in_progress, blocked, done', 'pending');
  try {
    await api(`/api/queues/${queueId}/tasks/${taskId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title, details, status }),
    });
    await loadDashboard();
  } catch (error) {
    alert(error.message);
  }
}

async function removeTask(queueId, taskId) {
  if (!window.confirm('Delete this task?')) {
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

async function submitAgent(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const errorNode = document.getElementById('agent-form-error');
  errorNode.classList.add('hidden');
  try {
    await api('/api/agents', {
      method: 'POST',
      body: JSON.stringify({
        name: form.get('name'),
        description: form.get('description'),
        command_args: form.get('command_args'),
        workdir: form.get('workdir'),
        feature_branch: form.get('feature_branch'),
        auto_publish: form.get('auto_publish') === 'on',
      }),
    });
    event.currentTarget.reset();
    await loadDashboard();
  } catch (error) {
    errorNode.textContent = error.message;
    errorNode.classList.remove('hidden');
  }
}

async function changeAgent(agentId, action) {
  try {
    if (action === 'delete') {
      await api(`/api/agents/${agentId}`, { method: 'DELETE' });
    } else {
      await api(`/api/agents/${agentId}/${action}`, { method: 'POST', body: '{}' });
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
                task = self.app.task_queue_manager.add_task(
                    queue_id,
                    title=str(payload.get("title", "")),
                    details=str(payload.get("details", "")),
                    status=str(payload.get("status", "pending")),
                )
                return _json_response(self, HTTPStatus.CREATED, {"task": task.to_dict()})
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
                        status=payload.get("status"),
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
            supplied = self.headers.get("X-Mindex-CSRF-Token", "")
            if not hmac.compare_digest(supplied, session.csrf_token):
                _json_response(self, HTTPStatus.FORBIDDEN, {"error": "invalid csrf token"})
                return None
        return session

    def _check_origin(self, *, require_auth: bool) -> str | None:
        if self.command in {"GET", "HEAD", "OPTIONS"}:
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
    return ConfiguredUiServer((config.host, config.port), MindexUiApp(config))


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

    serve_parser = subparsers.add_parser("serve", help="Serve the local Mindex UI")
    serve_parser.add_argument("--project-root", help="Project root to manage; defaults to the detected workspace")
    serve_parser.add_argument("--config", help="Override the UI config path")
    serve_parser.add_argument("--host", help="Override the configured host")
    serve_parser.add_argument("--port", type=int, help="Override the configured port")
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
        config = bootstrap.config
        if args.host or args.port:
            payload = json.loads(config.config_path.read_text(encoding="utf-8"))
            if args.host:
                payload.setdefault("server", {})["host"] = args.host
            if args.port:
                payload.setdefault("server", {})["port"] = args.port
            _write_private_json(config.config_path, payload)
            config = _parse_ui_config(payload, config.config_path)
        if bootstrap.generated_password:
            print(f"Generated UI password: {bootstrap.generated_password}", file=sys.stderr)
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
