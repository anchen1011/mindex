import argparse
import json
import platform
import shutil
import subprocess
from pathlib import Path

from .logging_utils import (
    create_log_paths,
    write_actions,
    write_metadata,
    write_prompt,
    write_status,
)
from .paths import codex_home, mindex_state_root, repo_root
from .skill_assets import install_skills


MANAGED_PROFILE_START = '# >>> mindex managed profile >>>'
MANAGED_PROFILE_END = '# <<< mindex managed profile <<<'


def _string_literal(value: str) -> str:
    return json.dumps(value)


def detect_platform() -> tuple[str, str]:
    return platform.system().lower(), platform.machine().lower()


def miniconda_url(system_name: str, machine: str) -> str:
    machine = machine.replace('amd64', 'x86_64')
    if machine not in {'x86_64', 'arm64', 'aarch64'}:
        machine = 'x86_64'
    if system_name == 'darwin':
        mapped = 'arm64' if machine in {'arm64', 'aarch64'} else 'x86_64'
        return f'https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-{mapped}.sh'
    return 'https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh'


def dependency_plan(project_root: Path) -> list[dict[str, object]]:
    system_name, machine = detect_platform()
    conda_prefix = Path.home() / 'miniconda3'
    installer = project_root / '.mindex' / 'downloads' / 'miniconda-installer.sh'
    installer.parent.mkdir(parents=True, exist_ok=True)
    conda_url = miniconda_url(system_name, machine)

    if system_name == 'darwin':
        npm_commands = [['brew', 'install', 'node']]
        tmux_commands = [['brew', 'install', 'tmux']]
    else:
        npm_commands = [
            ['sudo', 'apt-get', 'update'],
            ['sudo', 'apt-get', 'install', '-y', 'nodejs', 'npm'],
        ]
        tmux_commands = [['sudo', 'apt-get', 'install', '-y', 'tmux']]

    return [
        {
            'name': 'miniconda',
            'present': (conda_prefix / 'bin' / 'conda').exists(),
            'commands': [
                ['bash', '-lc', f'curl -fsSL {conda_url} -o {installer}'],
                ['bash', str(installer), '-b', '-p', str(conda_prefix)],
            ],
        },
        {
            'name': 'npm',
            'present': shutil.which('npm') is not None,
            'commands': npm_commands,
        },
        {
            'name': 'tmux',
            'present': shutil.which('tmux') is not None,
            'commands': tmux_commands,
        },
        {
            'name': 'codex',
            'present': shutil.which('codex') is not None,
            'commands': [['npm', 'install', '-g', '@openai/codex']],
        },
    ]


def flatten_commands(plan: list[dict[str, object]]) -> list[list[str]]:
    commands: list[list[str]] = []
    for item in plan:
        if item['present']:
            continue
        commands.extend(item['commands'])
    return commands


def ensure_project_instructions(project_root: Path) -> Path:
    state_root = mindex_state_root(project_root)
    state_root.mkdir(parents=True, exist_ok=True)
    path = state_root / 'codex_instructions.md'
    content = f"""# Mindex Instructions

Use this repository with the Mindex workflow.

- Keep `README.md` and `HISTORY.md` aligned with meaningful feature changes.
- Record meaningful activity under `{project_root / 'logs'}`.
- Run explicit tests before calling a task complete.
- Keep meaningful AI-generated work on a GitHub PR branch.
- Use the `configure` and `repo` skills installed by Mindex when they apply.
"""
    path.write_text(content, encoding='utf-8')
    return path


def managed_profile_block(instructions_path: Path) -> str:
    lines = [
        MANAGED_PROFILE_START,
        '[profiles.mindex]',
        f'model_instructions_file = {_string_literal(str(instructions_path))}',
        MANAGED_PROFILE_END,
        '',
    ]
    return '\n'.join(lines)


def update_codex_config(config_path: Path, instructions_path: Path) -> Path:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding='utf-8') if config_path.exists() else ''
    block = managed_profile_block(instructions_path)
    if MANAGED_PROFILE_START in existing and MANAGED_PROFILE_END in existing:
        start = existing.index(MANAGED_PROFILE_START)
        end = existing.index(MANAGED_PROFILE_END) + len(MANAGED_PROFILE_END)
        updated = existing[:start] + block + existing[end:]
    else:
        updated = existing
        if updated and not updated.endswith('\n'):
            updated += '\n'
        updated += block
    config_path.write_text(updated, encoding='utf-8')
    return config_path


def run_commands(commands: list[list[str]], dry_run: bool) -> list[dict[str, object]]:
    results = []
    for command in commands:
        entry = {'command': command, 'status': 'dry-run' if dry_run else 'pending'}
        if not dry_run:
            completed = subprocess.run(command, check=False)
            entry['status'] = 'passed' if completed.returncode == 0 else 'failed'
            entry['returncode'] = completed.returncode
            if completed.returncode != 0:
                results.append(entry)
                raise RuntimeError(f'command failed: {command}')
        results.append(entry)
    return results


def run_codex_bootstrap(project_root: Path, dry_run: bool) -> dict[str, object]:
    codex = shutil.which('codex')
    if not codex:
        return {'status': 'skipped', 'reason': 'codex-not-installed'}
    command = [
        codex,
        'exec',
        '-C',
        str(project_root),
        'Confirm that the Mindex configure workflow files exist and mention README.md, HISTORY.md, and logs/ in one sentence.',
    ]
    if dry_run:
        return {'status': 'dry-run', 'command': command}
    completed = subprocess.run(command, check=False)
    return {
        'status': 'passed' if completed.returncode == 0 else 'failed',
        'command': command,
        'returncode': completed.returncode,
    }


def configure_project(
    project_root_value: str | Path | None = None,
    codex_home_value: str | Path | None = None,
    dry_run: bool = False,
    install_mode: str = 'manual',
) -> dict[str, object]:
    project_root_path = repo_root(project_root_value)
    logs_root = project_root_path / 'logs'
    log_paths = create_log_paths(logs_root, prompt='mindex configure', label='configure')
    write_prompt(
        log_paths,
        'Prompt',
        f'Run the Mindex configure workflow in {project_root_path} (mode={install_mode}, dry_run={dry_run}).',
    )

    target_codex_home = codex_home(codex_home_value)
    plan = dependency_plan(project_root_path)
    commands = flatten_commands(plan)
    instructions_path = ensure_project_instructions(project_root_path)
    installed_skill_paths = install_skills(target_codex_home)
    config_path = update_codex_config(target_codex_home / 'config.toml', instructions_path)
    command_results = run_commands(commands, dry_run=dry_run)
    codex_result = run_codex_bootstrap(project_root_path, dry_run=dry_run)

    actions = [
        f'Prepared {len(plan)} dependency checks for the configure workflow.',
        f'Wrote project instructions to {instructions_path}.',
        f'Installed {len(installed_skill_paths)} skill directories into {target_codex_home / "skills"}.',
        f'Updated Codex config at {config_path}.',
        f'Recorded {len(command_results)} dependency command entries.',
        f'Codex bootstrap status: {codex_result["status"]}.',
    ]
    write_actions(log_paths, actions)
    report = {
        'install_mode': install_mode,
        'dry_run': dry_run,
        'project_root': str(project_root_path),
        'instructions_path': str(instructions_path),
        'codex_config_path': str(config_path),
        'skills': [str(path) for path in installed_skill_paths],
        'dependency_plan': plan,
        'command_results': command_results,
        'codex_result': codex_result,
    }
    write_metadata(log_paths, report)
    write_status(
        log_paths,
        {
            'task': 'configure',
            'install_mode': install_mode,
            'status': 'passed',
            'dry_run': dry_run,
        },
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='mindex-configure')
    subparsers = parser.add_subparsers(dest='command', required=False)

    configure_parser = subparsers.add_parser('configure')
    configure_parser.add_argument('--project-root')
    configure_parser.add_argument('--codex-home')
    configure_parser.add_argument('--dry-run', action='store_true')

    install_hook = subparsers.add_parser('install-hook')
    install_hook.add_argument('--project-root', required=True)
    install_hook.add_argument('--codex-home')
    install_hook.add_argument('--install-mode', required=True)
    install_hook.add_argument('--dry-run', action='store_true')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or 'configure'
    if command == 'install-hook':
        configure_project(
            project_root_value=args.project_root,
            codex_home_value=args.codex_home,
            dry_run=args.dry_run,
            install_mode=args.install_mode,
        )
        return 0
    configure_project(
        project_root_value=getattr(args, 'project_root', None),
        codex_home_value=getattr(args, 'codex_home', None),
        dry_run=getattr(args, 'dry_run', False),
        install_mode='manual',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
