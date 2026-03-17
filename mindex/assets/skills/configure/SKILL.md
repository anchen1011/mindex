---
name: configure
description: Use when setting up the Mindex repository, installing the Mindex wrapper, or repairing the Mindex Codex profile, skills, and logging configuration.
---

# Configure

Use this skill only for Mindex setup work.

Preferred workflow:

1. Confirm the repository root contains `README.md` and `HISTORY.md`.
2. Run `mindex configure --project-root <root>` when the package is installed.
3. If the package is not yet installed, run `python -m mindex.configure configure --project-root <root>` from the repository root.
4. Verify that `.mindex/`, `logs/`, and the Codex skills under `~/.codex/skills/` were updated.
5. Run the repository tests and record the results under `logs/`.
