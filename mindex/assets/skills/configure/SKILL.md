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
- install the packaged Mindex skills into `~/.codex/skills/` or the configured Codex home
- keep the managed `[profiles.mindex]` block in the Codex config file up to date
- record configure activity under `logs/`
- ensure the managed instructions enforce feature branches, automatic PR publication, full-branch PR descriptions, PR URL verification, and no direct pushes to protected branches

## Workflow

1. Prefer a dry run first when you need to inspect the generated plan.
2. Confirm the project root before writing any files.
3. Review the dependency command plan for Miniconda, Codex, NPM, and Tmux.
4. Review the managed instructions to confirm they describe Mindex as a Codex wrapper, enforce the GitHub branch and fork policy, and require verified PR creation on GitHub.
5. Re-run without `--dry-run` to apply the managed files.
6. Run and record validation after setup.
