---
name: configure
description: Use this skill to set up Mindex in a fresh or existing Codex environment.
metadata:
  short-description: Configure Mindex for a Codex environment
---

# configure skill

Use this skill to set up Mindex in a fresh or existing Codex environment.

Mindex is a Codex wrapper, so this setup must install the managed instructions
that define Codex's future branch, fork, logging, and PR behavior.

## Goals

- run `mindex configure --project-root <root>` from the repository root
- make sure `.mindex/codex_instructions.md` is managed by Mindex
- install the packaged Mindex skills into `~/.codex/skills/` or the configured Codex home, including the multi-agent coordination skill
- keep the managed `[profiles.mindex]` block in the Codex config file up to date
- record configure activity under `logs/`
- ensure the managed instructions enforce feature branches, automatic PR publication, full-branch PR descriptions, PR URL verification, and no direct pushes to protected branches
- ensure the managed instructions make fresh feature branches, corresponding commits, and PR publication the default path for new feature work
- ensure the managed instructions make separate branches and PRs the default
  when multiple coding agents work in the same repository

## Workflow

1. Prefer a dry run first when you need to inspect the generated plan.
2. Confirm the project root before writing any files.
3. Review the dependency command plan for Miniconda, Codex, NPM, and Tmux.
4. Review the managed instructions to confirm they describe Mindex as a Codex wrapper, enforce the GitHub branch and fork policy, require verified PR creation on GitHub, commit the work as part of the default publication flow, and isolate concurrent agents onto separate branches and PRs by default.
5. Confirm the packaged skills include `configure`, `repo`, and `multi-agent`.
6. Re-run without `--dry-run` to apply the managed files.
7. Run and record validation after setup.
