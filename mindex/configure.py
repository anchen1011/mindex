from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Iterable

from mindex.codex_home import default_managed_codex_home, default_managed_logs_root, default_vanilla_codex_home
from mindex.logging_utils import append_action, create_log_run, write_json, write_status


MANAGED_BLOCK_START = "# BEGIN MINDEX MANAGED BLOCK"
MANAGED_BLOCK_END = "# END MINDEX MANAGED BLOCK"


@dataclass(frozen=True)
class ConfigureResult:
    project_root: Path
    codex_home: Path
    codex_config_path: Path
    logs_root: Path
    instructions_path: Path
    installed_skills: list[str]
    dependency_commands: list[str]
    dry_run: bool
    log_dir: Path

    def to_json(self) -> str:
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, Path):
                payload[key] = str(value)
        return json.dumps(payload, indent=2, sort_keys=True)


def _assets_root() -> Path:
    return Path(__file__).resolve().parent / "assets" / "skills"


def build_dependency_commands(project_root: Path | None) -> list[str]:
    conda_exe = shutil.which("conda") or os.environ.get("CONDA_EXE") or "conda"
    install_target = (
        f"-e {project_root}"
        if project_root is not None and (project_root / "setup.py").exists()
        else "--upgrade mindex"
    )
    return [
        f"{conda_exe} create -n mindex python=3.11 -y",
        f"{conda_exe} run -n mindex pip install {install_target}",
        "npm install -g @openai/codex",
        "python -m pip install --upgrade openai-codex || pip install --upgrade openai-codex",
        "tmux -V",
    ]


def render_instructions() -> str:
    return """# Mindex Codex Instructions

You are working through the Mindex Codex wrapper.

Mindex is a managed Codex wrapper. Treat these instructions as the operating
policy for any future task or repository work launched through `mindex`, not as
a one-off task note that only applies to a single project.

Installing Mindex through `pip install` prepares `mindex` as the
Mindex-enhanced Codex entry point by default unless
`MINDEX_SKIP_AUTO_CONFIGURE=1`.

Mindex keeps its managed Codex home under `~/.mindex/codex-home` by default so
plain `~/.codex` stays vanilla while Mindex loads its own managed skills
globally across workspaces.

## Operating rules

- Run explicit tests for every meaningful change and record the results under `logs/`.
- Keep the original `codex` command untouched; it remains the plain vanilla Codex command.
- Use `mindex` when you want the Mindex-managed instructions, packaged skills, and profile settings across projects.
- Load Mindex-managed skills from `~/.mindex/codex-home/skills` instead of reusing `~/.codex/skills`.
- If a user asks Codex to configure Mindex, apply the same managed instructions, packaged skills, and profile block to that Codex environment.
- Publish meaningful AI-generated changes to GitHub instead of leaving them only on the local machine.
- Use one branch per feature and one PR per feature.
- Do not bundle multiple independent features into a single branch or PR.
- Publish meaningful completed interactions to GitHub automatically by default.
- Never push directly to `main`, `master`, `production`, or similarly protected release branches.
- When work starts from a protected branch, create a fresh feature branch before any repository changes continue.
- When multiple agents or parallel efforts pursue different goals, keep each goal on its own branch and PR instead of mixing them together.
- Never touch another person's existing branch unless the user explicitly instructs you to do so.
- Ensure each PR title and description reflect the full branch scope rather than only the newest commit.
- Verify that each PR actually exists on GitHub and capture the PR URL before considering publication complete.
- Apply that branch-and-PR publication policy even when the user only asks for code, docs, tests, or behavior changes and does not explicitly mention repo workflow, Git, GitHub, branches, or PRs.
- Treat local documentation updates as part of feature completion when the workflow changes.

## GitHub workflow policy

- If the repository belongs to the user, create a fresh feature branch for each specific feature, push that branch to GitHub, open or update the matching PR, and confirm the PR can be located on GitHub.
- Make PR creation and updates automatic for meaningful completed interactions so users do not need to request GitHub publication manually.
- If the repository belongs to someone else or to an organization, fork it to the user's own GitHub account whenever possible, push the feature branch to that fork, and open the PR from the user's account.
- If forking is not possible, create a new feature branch in the organization repository and keep clear ownership boundaries.
- Never work on `main`, `master`, `production`, or any other shared release branch as the source branch for a feature.
- Never reuse an unrelated in-flight feature branch for a new task.
- Only continue on an existing feature branch when the new interaction is clearly follow-up work for that same feature; otherwise create a new feature branch and PR.
- Use `mindex publish-pr` to automate branch creation when needed, commit the work, push it, create the PR, and verify the PR URL.

## Repository reminders

- `mindex configure` manages this file, the Mindex-managed Codex home, packaged skills, and the Codex profile block.
- `logs/` is a local artifact; do not commit it.
- Use the packaged `repo` skill when working on the Mindex repository itself and the packaged `configure` skill when setting up new environments.
"""


def render_managed_profile_block(codex_home: Path, instructions_path: Path) -> str:
    instructions_text = instructions_path.as_posix()
    managed_home_text = codex_home.as_posix()
    return "\n".join(
        [
            MANAGED_BLOCK_START,
            "[profiles.mindex]",
            'model = "gpt-5"',
            'reasoning_effort = "high"',
            'approval_policy = "on-request"',
            'sandbox_mode = "workspace-write"',
            "",
            "[profiles.mindex.env]",
            f'CODEX_HOME = "{managed_home_text}"',
            f'MINDEX_INSTRUCTIONS_FILE = "{instructions_text}"',
            MANAGED_BLOCK_END,
            "",
        ]
    )


def upsert_managed_block(existing_text: str, managed_block: str) -> str:
    if MANAGED_BLOCK_START in existing_text and MANAGED_BLOCK_END in existing_text:
        prefix, remainder = existing_text.split(MANAGED_BLOCK_START, 1)
        _, suffix = remainder.split(MANAGED_BLOCK_END, 1)
        separator = "" if prefix.endswith("\n") or not prefix else "\n"
        return f"{prefix}{separator}{managed_block}{suffix.lstrip()}"
    if existing_text and not existing_text.endswith("\n"):
        existing_text += "\n"
    return existing_text + managed_block


def _remove_existing_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)


def _bootstrap_managed_codex_home(*, source_home: Path, destination_home: Path, dry_run: bool) -> list[str]:
    synced_entries: list[str] = []
    if dry_run or not source_home.exists():
        return synced_entries

    destination_home.mkdir(parents=True, exist_ok=True)
    for source_path in sorted(source_home.iterdir()):
        if source_path.name in {"config.toml", "skills"}:
            continue

        destination_path = destination_home / source_path.name
        if destination_path.exists() or destination_path.is_symlink():
            continue

        if source_path.is_symlink():
            destination_path.symlink_to(source_path.resolve(), target_is_directory=source_path.is_dir())
        elif source_path.is_dir():
            shutil.copytree(source_path, destination_path, symlinks=True)
        else:
            shutil.copy2(source_path, destination_path)
        synced_entries.append(source_path.name)
    return synced_entries


def install_packaged_skills(destination_root: Path, *, dry_run: bool) -> tuple[list[str], str]:
    installed: list[str] = []
    install_mode = "symlink"
    source_root = _assets_root()
    for source_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        target_dir = destination_root / source_dir.name
        installed.append(source_dir.name)
        if dry_run:
            continue
        destination_root.mkdir(parents=True, exist_ok=True)
        if target_dir.exists() or target_dir.is_symlink():
            _remove_existing_path(target_dir)
        try:
            target_dir.symlink_to(source_dir.resolve(), target_is_directory=True)
        except OSError:
            shutil.copytree(source_dir, target_dir)
            install_mode = "copy"
    return installed, install_mode


def configure_project(
    *,
    project_root: Path | str | None = None,
    codex_home: Path | str | None = None,
    codex_config_path: Path | str | None = None,
    logs_root: Path | str | None = None,
    dry_run: bool = False,
) -> ConfigureResult:
    configured_project_root = Path(project_root).resolve() if project_root else None
    project_root = configured_project_root or Path.cwd().resolve()
    codex_home = (
        Path(codex_home).expanduser().resolve()
        if codex_home
        else default_managed_codex_home()
    )
    codex_config_path = (
        Path(codex_config_path).expanduser().resolve() if codex_config_path else (codex_home / "config.toml")
    )
    logs_root = Path(logs_root).resolve() if logs_root else default_managed_logs_root()
    instructions_path = codex_home / "mindex_instructions.md"
    prompt_suffix = f" --project-root {project_root}" if configured_project_root else ""

    log_run = create_log_run(
        logs_root,
        "configure",
        prompt_text=f"mindex configure{prompt_suffix}{' --dry-run' if dry_run else ''}",
        metadata={
            "project_root": str(project_root),
            "codex_home": str(codex_home),
            "codex_config_path": str(codex_config_path),
            "dry_run": dry_run,
        },
    )

    try:
        append_action(log_run, f"Prepare instructions path: {instructions_path}")
        append_action(log_run, f"Prepare Codex home: {codex_home}")
        append_action(log_run, f"Prepare Codex config: {codex_config_path}")
        vanilla_codex_home = default_vanilla_codex_home()
        append_action(log_run, f"Vanilla Codex home: {vanilla_codex_home}")

        dependency_commands = build_dependency_commands(project_root)
        for command in dependency_commands:
            append_action(log_run, f"Dependency command: {command}")

        synced_entries = _bootstrap_managed_codex_home(
            source_home=vanilla_codex_home,
            destination_home=codex_home,
            dry_run=dry_run,
        )
        if synced_entries:
            append_action(log_run, f"Bootstrap unmanaged Codex home entries: {', '.join(synced_entries)}")

        skills_root = codex_home / "skills"
        installed_skills, skill_install_mode = install_packaged_skills(skills_root, dry_run=dry_run)
        append_action(log_run, f"Packaged skills ({skill_install_mode}): {', '.join(installed_skills)}")

        instructions_text = render_instructions()
        managed_block = render_managed_profile_block(codex_home, instructions_path)

        if not dry_run:
            instructions_path.parent.mkdir(parents=True, exist_ok=True)
            instructions_path.write_text(instructions_text, encoding="utf-8")

            codex_config_path.parent.mkdir(parents=True, exist_ok=True)
            existing_text = codex_config_path.read_text(encoding="utf-8") if codex_config_path.exists() else ""
            codex_config_path.write_text(upsert_managed_block(existing_text, managed_block), encoding="utf-8")

        plan_payload = {
            "instructions_path": str(instructions_path),
            "codex_config_path": str(codex_config_path),
            "skills_root": str(skills_root),
            "packaged_skills": installed_skills,
            "skill_install_mode": skill_install_mode,
            "dependency_commands": dependency_commands,
            "dry_run": dry_run,
        }
        write_json(log_run.run_dir / "configure_plan.json", plan_payload)
        write_status(log_run, "success", dry_run=dry_run, installed_skills=installed_skills)
    except Exception as exc:
        write_status(log_run, "failure", error=str(exc), dry_run=dry_run)
        raise

    return ConfigureResult(
        project_root=project_root,
        codex_home=codex_home,
        codex_config_path=codex_config_path,
        logs_root=logs_root,
        instructions_path=instructions_path,
        installed_skills=installed_skills,
        dependency_commands=dependency_commands,
        dry_run=dry_run,
        log_dir=log_run.run_dir,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mindex project configuration tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser("configure", help="Configure the Mindex project environment")
    configure_parser.add_argument(
        "--project-root",
        help="Optional path to the current workspace or source checkout; defaults to the current directory",
    )
    configure_parser.add_argument("--codex-home", help="Override the Codex home directory")
    configure_parser.add_argument("--codex-config", help="Override the Codex config path")
    configure_parser.add_argument("--logs-root", help="Override the logs directory")
    configure_parser.add_argument("--dry-run", action="store_true", help="Plan the configuration without writing targets")
    return parser


def print_configure_summary(result: ConfigureResult) -> None:
    mode = "dry-run" if result.dry_run else "applied"
    print(f"Mindex configure ({mode})", file=sys.stderr)
    print(f"CODEX_HOME: {result.codex_home}", file=sys.stderr)
    print(f"Instructions file: {result.instructions_path}", file=sys.stderr)
    print(f"Logs root: {result.logs_root}", file=sys.stderr)


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command != "configure":
        parser.error(f"unsupported command: {args.command}")

    result = configure_project(
        project_root=args.project_root,
        codex_home=args.codex_home,
        codex_config_path=args.codex_config,
        logs_root=args.logs_root,
        dry_run=args.dry_run,
    )
    print_configure_summary(result)
    print(result.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
