from __future__ import annotations

import os
from pathlib import Path


def default_vanilla_codex_home(env: dict[str, str] | None = None) -> Path:
    environ = env if env is not None else os.environ
    configured = environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def default_managed_codex_home(
    project_root: Path | str,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    environ = env if env is not None else os.environ
    configured = environ.get("MINDEX_CODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(project_root).resolve() / ".mindex" / "codex-home").resolve()
