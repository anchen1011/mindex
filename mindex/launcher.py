from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Iterable

from mindex.codex_home import default_managed_codex_home, default_managed_logs_root
from mindex.github_workflow import WorkflowError, ensure_feature_branch, initialize_local_git_repository, run_post_action_hook
from mindex.logging_utils import append_action, create_log_run, write_status


YOLO_FLAGS = ["--dangerously-bypass-approvals-and-sandbox"]


def _git_toplevel(start: Path, *, env: dict[str, str] | None = None) -> Path | None:
    git_env = os.environ.copy()
    if env:
        git_env.update(env)
    git_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start),
        text=True,
        capture_output=True,
        check=False,
        env=git_env,
    )
    if git_root.returncode != 0:
        return None
    resolved = git_root.stdout.strip()
    if not resolved:
        return None
    return Path(resolved).resolve()


def _looks_like_workspace_root(path: Path) -> bool:
    return (path / "README.md").exists() and (path / "HISTORY.md").exists()


def find_project_root(start: Path | str | None = None) -> Path:
    current = Path(start or Path.cwd()).resolve()
    git_root = _git_toplevel(current)
    if git_root is not None:
        return git_root
    for candidate in (current, *current.parents):
        if _looks_like_workspace_root(candidate):
            return candidate
    return current


def resolve_logs_root(
    launch_root: Path | str,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    resolved_root = Path(launch_root).resolve()
    configured_logs_root = default_managed_logs_root(env=env)
    if (env or os.environ).get("MINDEX_LOGS_ROOT"):
        return configured_logs_root
    if _git_toplevel(resolved_root, env=env) == resolved_root or _looks_like_workspace_root(resolved_root):
        return (resolved_root / "logs").resolve()
    return configured_logs_root


def resolve_codex_command(env: dict[str, str] | None = None) -> str:
    if env and env.get("MINDEX_CODEX_BIN"):
        return env["MINDEX_CODEX_BIN"]
    return os.environ.get("MINDEX_CODEX_BIN", "codex")


def apply_default_yolo(argv: Iterable[str]) -> list[str]:
    args = list(argv)
    for index, value in enumerate(args):
        if value in {"--dangerously-bypass-approvals-and-sandbox", "--full-auto"}:
            return args
        if value in {"-a", "--ask-for-approval", "-s", "--sandbox"}:
            return args
        if value == "-c" and index + 1 < len(args):
            config_value = args[index + 1]
            if config_value.startswith("approval_policy=") or config_value.startswith("sandbox_mode="):
                return args
    return [*YOLO_FLAGS, *args]


def launch_codex(
    argv: Iterable[str],
    *,
    project_root: Path | str | None = None,
    logs_root: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> int:
    args = apply_default_yolo(argv)
    launch_root = find_project_root(project_root)
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    managed_codex_home = default_managed_codex_home(env=run_env)
    run_env["MINDEX_CODEX_HOME"] = str(managed_codex_home)
    run_env["CODEX_HOME"] = str(managed_codex_home)
    run_env["MINDEX_PROJECT_ROOT"] = str(launch_root)
    command = [resolve_codex_command(run_env), *args]
    repo_init_result = initialize_local_git_repository(launch_root, env=run_env)
    resolved_logs_root = Path(logs_root).resolve() if logs_root else resolve_logs_root(launch_root, env=run_env)

    log_run = create_log_run(
        resolved_logs_root,
        "launcher",
        prompt_text="mindex " + " ".join(shlex.quote(part) for part in args),
        metadata={
            "project_root": str(launch_root),
            "command": command,
            "cwd": str(Path.cwd().resolve()),
            "codex_home": str(managed_codex_home),
        },
    )
    append_action(log_run, f"Proxy command: {shlex.join(command)}")
    append_action(log_run, f"Managed Codex home: {managed_codex_home}")
    if repo_init_result.initialized:
        append_action(
            log_run,
            f"Initialized a local Git repository for this project with default branch {repo_init_result.branch_name}.",
        )
    elif repo_init_result.skipped_reason == "unsafe-project-root":
        append_action(log_run, "Skipped local Git initialization because the detected project root is not safe to manage.")
    elif repo_init_result.skipped_reason == "disabled-by-env":
        append_action(log_run, "Skipped local Git initialization because MINDEX_AUTO_INIT_GIT is disabled.")

    try:
        requested_branch = run_env.get("MINDEX_FEATURE_BRANCH")
        requested_summary = run_env.get("MINDEX_AGENT_GOAL") or requested_branch or "codex-session"
        active_branch = ensure_feature_branch(
            launch_root,
            summary=requested_summary,
            branch_name=requested_branch,
            env=run_env,
            log_run=log_run,
        )
        if active_branch:
            append_action(log_run, f"Active feature branch: {active_branch}")
            if run_env.get("MINDEX_MULTI_AGENT") == "1" or run_env.get("MINDEX_AGENT_ID"):
                append_action(
                    log_run,
                    "Multi-agent launch context: "
                    f"agent_id={run_env.get('MINDEX_AGENT_ID', '').strip() or 'n/a'}, "
                    f"agent_name={run_env.get('MINDEX_AGENT_NAME', '').strip() or 'n/a'}, "
                    f"goal={run_env.get('MINDEX_AGENT_GOAL', '').strip() or requested_summary}",
                )
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

    publish_result = None
    try:
        publish_result = run_post_action_hook(
            project_root=launch_root,
            argv=args,
            branch_name=active_branch if "active_branch" in locals() else None,
            returncode=completed.returncode,
            env=run_env,
            log_run=log_run,
        )
        if publish_result is not None:
            append_action(log_run, f"Automatic publication verified: {publish_result.pr_url}")
    except WorkflowError as exc:
        append_action(log_run, f"Automatic publication skipped: {exc}")

    write_status(
        log_run,
        "success" if completed.returncode == 0 else "failure",
        returncode=completed.returncode,
        terminal_capture_path=capture_path,
        published_pr_url=publish_result.pr_url if publish_result is not None else None,
        published_pr_number=publish_result.pr_number if publish_result is not None else None,
        published_branch=publish_result.branch_name if publish_result is not None else None,
    )
    return completed.returncode
