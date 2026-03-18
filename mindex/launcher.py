from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Iterable

from mindex.github_workflow import WorkflowError, ensure_feature_branch
from mindex.logging_utils import append_action, create_log_run, write_status


def find_project_root(start: Path | str | None = None) -> Path:
    current = Path(start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "README.md").exists() and (candidate / "HISTORY.md").exists():
            return candidate
    return current


def resolve_codex_command(env: dict[str, str] | None = None) -> str:
    if env and env.get("MINDEX_CODEX_BIN"):
        return env["MINDEX_CODEX_BIN"]
    return os.environ.get("MINDEX_CODEX_BIN", "codex")


def launch_codex(
    argv: Iterable[str],
    *,
    project_root: Path | str | None = None,
    logs_root: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> int:
    args = list(argv)
    launch_root = find_project_root(project_root)
    resolved_logs_root = Path(logs_root).resolve() if logs_root else (launch_root / "logs")
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    command = [resolve_codex_command(run_env), *args]

    log_run = create_log_run(
        resolved_logs_root,
        "launcher",
        prompt_text="mindex " + " ".join(shlex.quote(part) for part in args),
        metadata={
            "project_root": str(launch_root),
            "command": command,
            "cwd": str(Path.cwd().resolve()),
        },
    )
    append_action(log_run, f"Proxy command: {shlex.join(command)}")

    try:
        requested_branch = run_env.get("MINDEX_FEATURE_BRANCH")
        active_branch = ensure_feature_branch(
            launch_root,
            summary=requested_branch or "codex-session",
            branch_name=requested_branch,
            env=run_env,
            log_run=log_run,
        )
        if active_branch:
            append_action(log_run, f"Active feature branch: {active_branch}")
    except WorkflowError as exc:
        append_action(log_run, f"Feature branch automation skipped: {exc}")

    use_script = shutil.which("script") is not None and run_env.get("MINDEX_DISABLE_SCRIPT") != "1"
    if use_script:
        append_action(log_run, f"Terminal capture: {log_run.terminal_capture_path}")
        completed = subprocess.run(
            ["script", "-q", "-c", shlex.join(command), str(log_run.terminal_capture_path)],
            cwd=str(launch_root),
            env=run_env,
            check=False,
        )
        capture_path = str(log_run.terminal_capture_path)
    else:
        completed = subprocess.run(
            command,
            cwd=str(launch_root),
            env=run_env,
            check=False,
        )
        capture_path = None

    write_status(
        log_run,
        "success" if completed.returncode == 0 else "failure",
        returncode=completed.returncode,
        terminal_capture_path=capture_path,
    )
    return completed.returncode
