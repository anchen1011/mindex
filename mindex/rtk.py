from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


@dataclass(frozen=True)
class RtkInitResult:
    status: str
    command: str | None
    reason: str | None = None
    stdout: str = ""
    stderr: str = ""


def resolve_rtk_command(env: dict[str, str] | None = None) -> str | None:
    environ = os.environ.copy()
    if env:
        environ.update(env)
    configured = environ.get("MINDEX_RTK_BIN")
    if configured:
        return configured
    return shutil.which("rtk", path=environ.get("PATH"))


def rtk_codex_init_command(command: str = "rtk") -> list[str]:
    return [command, "init", "--codex"]


def ensure_rtk_codex_integration(
    codex_home: Path | str,
    *,
    env: dict[str, str] | None = None,
) -> RtkInitResult:
    resolved_home = Path(codex_home).expanduser().resolve()
    rtk_command = resolve_rtk_command(env=env)
    if not rtk_command:
        return RtkInitResult(status="unavailable", command=None, reason="rtk not found on PATH")

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    resolved_home.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        rtk_codex_init_command(rtk_command),
        cwd=str(resolved_home),
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        reason = completed.stderr.strip() or completed.stdout.strip() or "rtk init failed"
        return RtkInitResult(
            status="failed",
            command=rtk_command,
            reason=reason,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    return RtkInitResult(
        status="configured",
        command=rtk_command,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
