from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid
from typing import Any


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "task"


@dataclass(frozen=True)
class LogRun:
    logs_root: Path
    session_id: str
    run_dir: Path
    prompt_path: Path
    actions_path: Path
    metadata_path: Path
    status_path: Path
    validation_path: Path
    terminal_capture_path: Path


def create_log_run(
    logs_root: Path | str,
    prompt_name: str,
    *,
    prompt_text: str = "",
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> LogRun:
    root = Path(logs_root)
    resolved_session = session_id or f"session-{utc_timestamp()}-{uuid.uuid4().hex[:8]}"
    run_dir = root / resolved_session / f"{utc_timestamp()}-{slugify(prompt_name)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    log_run = LogRun(
        logs_root=root,
        session_id=resolved_session,
        run_dir=run_dir,
        prompt_path=run_dir / "prompt.txt",
        actions_path=run_dir / "actions.txt",
        metadata_path=run_dir / "metadata.json",
        status_path=run_dir / "status.json",
        validation_path=run_dir / "validation.json",
        terminal_capture_path=run_dir / "terminal.typescript",
    )
    log_run.prompt_path.write_text(prompt_text, encoding="utf-8")
    log_run.actions_path.write_text("", encoding="utf-8")
    write_json(log_run.metadata_path, metadata or {})
    return log_run


def write_json(path: Path | str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_action(log_run: LogRun, message: str) -> None:
    with log_run.actions_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def write_status(log_run: LogRun, status: str, **details: Any) -> None:
    payload = {"status": status, "updated_at": utc_timestamp(), **details}
    write_json(log_run.status_path, payload)


def record_validation(
    log_run: LogRun,
    *,
    command: list[str],
    returncode: int,
    passed: bool,
    stdout: str = "",
    stderr: str = "",
) -> None:
    payload = {
        "command": command,
        "returncode": returncode,
        "passed": passed,
        "stdout": stdout,
        "stderr": stderr,
        "recorded_at": utc_timestamp(),
    }
    write_json(log_run.validation_path, payload)
