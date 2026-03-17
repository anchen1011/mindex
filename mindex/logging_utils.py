import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


_SLUG_RE = re.compile(r'[^a-z0-9]+')


@dataclass(frozen=True)
class LogPaths:
    root: Path
    prompt_path: Path
    actions_path: Path
    status_path: Path
    metadata_path: Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(value: str | None, default: str = 'interactive') -> str:
    if not value:
        return default
    slug = _SLUG_RE.sub('-', value.strip().lower()).strip('-')
    return slug or default


def generate_session_id(now: datetime | None = None) -> str:
    current = now or utc_now()
    return f"session-{current.strftime('%Y%m%d')}-{uuid4().hex[:8]}"


def create_log_paths(
    logs_root: Path,
    prompt: str | None,
    label: str | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
) -> LogPaths:
    current = now or utc_now()
    day = current.strftime('%Y-%m-%d')
    stamp = current.strftime('%Y%m%dT%H%M%SZ')
    sid = session_id or generate_session_id(current)
    slug = slugify(label or prompt)
    root = logs_root / day / sid / stamp / slug
    root.mkdir(parents=True, exist_ok=True)
    return LogPaths(
        root=root,
        prompt_path=root / 'prompt.md',
        actions_path=root / 'actions.md',
        status_path=root / 'status.json',
        metadata_path=root / 'metadata.json',
    )


def write_prompt(log_paths: LogPaths, title: str, body: str) -> None:
    log_paths.prompt_path.write_text(f'# {title}\n\n{body}\n', encoding='utf-8')


def write_actions(log_paths: LogPaths, actions: list[str]) -> None:
    lines = ['# Actions', ''] + [f'- {action}' for action in actions]
    log_paths.actions_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def write_status(log_paths: LogPaths, payload: dict) -> None:
    log_paths.status_path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def write_metadata(log_paths: LogPaths, payload: dict) -> None:
    log_paths.metadata_path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
