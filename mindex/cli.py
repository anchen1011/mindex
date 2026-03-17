import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .configure import main as configure_main
from .logging_utils import (
    create_log_paths,
    write_actions,
    write_metadata,
    write_prompt,
    write_status,
)
from .paths import repo_root


def _extract_prompt(args: list[str]) -> str | None:
    for arg in args:
        if not arg.startswith('-'):
            return arg
    return None


def build_codex_command(project_root_path: Path, forwarded_args: list[str]) -> list[str]:
    command = ['codex', '-C', str(project_root_path)]
    instructions_path = project_root_path / '.mindex' / 'codex_instructions.md'
    if instructions_path.exists():
        command.extend(['-c', f'model_instructions_file={json.dumps(str(instructions_path))}'])
    command.extend(forwarded_args)
    return command


def run_logged_codex(project_root_path: Path, forwarded_args: list[str]) -> int:
    logs_root = project_root_path / 'logs'
    prompt = _extract_prompt(forwarded_args)
    log_paths = create_log_paths(logs_root, prompt=prompt, label='mindex-launch')
    write_prompt(
        log_paths,
        'Prompt',
        f'Launch Mindex with args: {forwarded_args or ["<interactive>"]}',
    )
    command = build_codex_command(project_root_path, forwarded_args)
    write_metadata(
        log_paths,
        {
            'project_root': str(project_root_path),
            'command': command,
        },
    )

    io_path = log_paths.root / 'terminal.io'
    timing_path = log_paths.root / 'terminal.timing'
    if shutil.which('script'):
        wrapped = [
            'script',
            '-qef',
            '--log-io',
            str(io_path),
            '--log-timing',
            str(timing_path),
            '-c',
            shlex.join(command),
        ]
        completed = subprocess.run(wrapped, check=False, cwd=project_root_path)
    else:
        with io_path.open('w', encoding='utf-8') as stream:
            completed = subprocess.run(
                command,
                check=False,
                cwd=project_root_path,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )

    write_actions(
        log_paths,
        [
            f'Launched Codex via Mindex from {project_root_path}.',
            f'Captured terminal output at {io_path}.',
            f'Process exit code: {completed.returncode}.',
        ],
    )
    write_status(
        log_paths,
        {
            'task': 'mindex-launch',
            'status': 'passed' if completed.returncode == 0 else 'failed',
            'exit_code': completed.returncode,
        },
    )
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == 'configure':
        return configure_main(args)
    return run_logged_codex(repo_root(), args)
