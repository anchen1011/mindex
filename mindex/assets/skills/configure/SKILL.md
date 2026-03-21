---
name: configure
description: Use this skill to set up Mindex in a fresh or existing Codex environment.
metadata:
  short-description: Configure Mindex for a Codex environment
---

# configure skill

Use this skill to set up Mindex in a fresh or existing Codex environment.

Mindex is a Codex wrapper, so this setup must install the managed instructions
that define Codex's future branch, fork, logging, and PR behavior across the
tasks and repositories launched through `mindex`.

Treat `mindex` as the Mindex-enhanced Codex entry point that installation
prepares by default across projects, while plain `codex` stays available in its
original vanilla form unless the user explicitly asks to configure Mindex into
that Codex environment.

## Goals

- run `mindex configure` to apply the global Mindex-managed setup, and use `--project-root <root>` only when you want to point configure at a specific workspace or source checkout
- make sure the managed instructions file inside the Mindex-managed Codex home is kept up to date
- keep the default Mindex-managed Codex home under `~/.mindex/codex-home` so Mindex does not reuse vanilla `~/.codex`
- install the packaged Mindex skills into `~/.mindex/codex-home/skills/` or the configured Codex home, including the multi-agent coordination skill
- link packaged skills back to the source tree when possible so editable-install skill edits take effect without another copy step
- keep the managed `[profiles.mindex]` block in the Codex config file up to date
- record configure activity under `logs/`
- ensure the managed instructions say that when multiple agents or parallel efforts pursue different goals, each goal must use its own branch and PR
- make it clear that `pip install` of Mindex installs the `mindex` command and applies the Mindex-managed setup by default unless auto-configure is disabled
- keep plain `codex` unchanged by default, but if the user asks Codex to configure Mindex, apply the same managed instructions, packaged skills, and Mindex profile to that Codex environment across projects
- ensure the managed instructions enforce feature branches, automatic PR publication, full-branch PR descriptions, PR URL verification, and no direct pushes to protected branches
- ensure the managed instructions make fresh feature branches, corresponding commits, and PR publication the default path for new feature work
- ensure the managed instructions make separate branches and PRs the default
  when multiple coding agents work in the same repository

## Workflow

1. Prefer a dry run first when you need to inspect the generated plan.
2. Confirm the current workspace context when you pass `--project-root`; otherwise treat configure as a global Mindex setup step.
3. Review the dependency command plan for Miniconda, Codex, NPM, Tmux, and RTK.
4. Review the managed instructions to confirm they describe Mindex as a Codex wrapper, explain the `mindex` versus vanilla `codex` distinction, enforce the GitHub branch and fork policy, require verified PR creation on GitHub, commit the work as part of the default publication flow, and isolate concurrent agents onto separate branches and PRs by default.
5. Confirm the packaged skills include `configure`, `repo`, and `multi-agent`.
6. Re-run without `--dry-run` to apply the managed files.
7. Run and record validation after setup.
