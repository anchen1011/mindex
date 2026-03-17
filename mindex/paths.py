import os
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parent


def repo_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()

    override = os.environ.get('MINDEX_PROJECT_ROOT')
    if override:
        return Path(override).expanduser().resolve()

    candidate = package_root().parent
    if (candidate / 'HISTORY.md').exists():
        return candidate
    return Path.cwd().resolve()


def codex_home(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    override = os.environ.get('CODEX_HOME')
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / '.codex'


def mindex_state_root(project_root: Path) -> Path:
    return project_root / '.mindex'
