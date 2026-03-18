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
- install the packaged Mindex skills into `~/.mindex/codex-home/skills/` or the configured Codex home
- link packaged skills back to the source tree when possible so editable-install skill edits take effect without another copy step
- keep the managed `[profiles.mindex]` block in the Codex config file up to date
- record configure activity under `logs/`
- make it clear that `pip install` of Mindex installs the `mindex` command and applies the Mindex-managed setup by default unless auto-configure is disabled
- keep plain `codex` unchanged by default, but if the user asks Codex to configure Mindex, apply the same managed instructions, packaged skills, and Mindex profile to that Codex environment across projects
- ensure the managed instructions enforce feature branches, automatic PR publication, full-branch PR descriptions, PR URL verification, and no direct pushes to protected branches

## Workflow

1. Prefer a dry run first when you need to inspect the generated plan.
2. Confirm the current workspace context when you pass `--project-root`; otherwise treat configure as a global Mindex setup step.
3. Review the dependency command plan for Miniconda, Codex, NPM, and Tmux.
4. Review the managed instructions to confirm they describe Mindex as a Codex wrapper, explain the `mindex` versus vanilla `codex` distinction, enforce the GitHub branch and fork policy, and require verified PR creation on GitHub.
5. Re-run without `--dry-run` to apply the managed files.
6. Run and record validation after setup.
