from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Iterable

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


def default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def build_dependency_commands(project_root: Path) -> list[str]:
    conda_exe = shutil.which("conda") or os.environ.get("CONDA_EXE") or "conda"
    return [
        f"{conda_exe} create -n mindex python=3.11 -y",
        f"{conda_exe} run -n mindex pip install -e {project_root}",
        "npm install -g @openai/codex",
        "python -m pip install --upgrade openai-codex || pip install --upgrade openai-codex",
        "tmux -V",
    ]


def render_instructions(project_root: Path) -> str:
    return f"""# Mindex Codex Instructions

You are working in `{project_root}` through the Mindex Codex wrapper.

Mindex is a project-specific Codex wrapper. Treat these instructions as the
operating policy for future repository work, not as a one-off task note.

## Operating rules

- Run explicit tests for every meaningful change and record the results under `logs/`.
- Keep the original `codex` command untouched; use `mindex` for repo-specific workflows.
- Publish meaningful AI-generated changes to GitHub instead of leaving them only on the local machine.
- Use one branch per feature and one PR per feature.
- Do not bundle multiple independent features into a single branch or PR.
- Publish meaningful completed interactions to GitHub automatically by default.
- Never push directly to `main`, `master`, `production`, or similarly protected release branches.
- When work starts from a protected branch, create a fresh feature branch before any repository changes continue.
- Never touch another person's existing branch unless the user explicitly instructs you to do so.
- Ensure each PR title and description reflect the full branch scope rather than only the newest commit.
- Verify that each PR actually exists on GitHub and capture the PR URL before considering publication complete.
- Treat `README.md` updates as part of feature completion when the workflow changes.

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

- `mindex configure` manages this file, packaged skills, and the Codex profile block.
- `logs/` is a local artifact; do not commit it.
- Use the packaged `repo` skill when working on this repository and the packaged `configure` skill when setting up new environments.
"""


def render_managed_profile_block(project_root: Path, instructions_path: Path) -> str:
    root_text = project_root.as_posix()
    instructions_text = instructions_path.as_posix()
    return "\n".join(
        [
            MANAGED_BLOCK_START,
            "[profiles.mindex]",
            'model = "gpt-5"',
            'reasoning_effort = "high"',
            'approval_policy = "on-request"',
            'sandbox_mode = "workspace-write"',
            f'cwd = "{root_text}"',
            "",
            "[profiles.mindex.env]",
            f'MINDEX_PROJECT_ROOT = "{root_text}"',
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


def copy_packaged_skills(destination_root: Path, *, dry_run: bool) -> list[str]:
    installed: list[str] = []
    source_root = _assets_root()
    for source_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        target_dir = destination_root / source_dir.name
        installed.append(source_dir.name)
        if dry_run:
            continue
        destination_root.mkdir(parents=True, exist_ok=True)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
    return installed


def configure_project(
    *,
    project_root: Path | str,
    codex_home: Path | str | None = None,
    codex_config_path: Path | str | None = None,
    logs_root: Path | str | None = None,
    dry_run: bool = False,
) -> ConfigureResult:
    project_root = Path(project_root).resolve()
    codex_home = Path(codex_home).expanduser().resolve() if codex_home else default_codex_home().resolve()
    codex_config_path = (
        Path(codex_config_path).expanduser().resolve() if codex_config_path else (codex_home / "config.toml")
    )
    logs_root = Path(logs_root).resolve() if logs_root else (project_root / "logs")
    instructions_path = project_root / ".mindex" / "codex_instructions.md"

    log_run = create_log_run(
        logs_root,
        "configure",
        prompt_text=f"mindex configure --project-root {project_root}{' --dry-run' if dry_run else ''}",
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

        dependency_commands = build_dependency_commands(project_root)
        for command in dependency_commands:
            append_action(log_run, f"Dependency command: {command}")

        skills_root = codex_home / "skills"
        installed_skills = copy_packaged_skills(skills_root, dry_run=dry_run)
        append_action(log_run, f"Packaged skills: {', '.join(installed_skills)}")

        instructions_text = render_instructions(project_root)
        managed_block = render_managed_profile_block(project_root, instructions_path)

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
    configure_parser.add_argument("--project-root", required=True, help="Path to the project root")
    configure_parser.add_argument("--codex-home", help="Override the Codex home directory")
    configure_parser.add_argument("--codex-config", help="Override the Codex config path")
    configure_parser.add_argument("--logs-root", help="Override the logs directory")
    configure_parser.add_argument("--dry-run", action="store_true", help="Plan the configuration without writing targets")
    return parser


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
    print(result.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
