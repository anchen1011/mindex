# Mindex

Mindex is a project-specific Codex wrapper and configuration layer for working
on the `mindex` repository with repeatable setup, proactive logging, and a
GitHub PR-first workflow.

## Current status

The repository currently includes:

- project history and requirement tracking in `HISTORY.md`
- structured task logs under `logs/`
- a documented testing-first and PR-first workflow
- an open GitHub PR workflow for AI-generated changes

The repository now has an initial implementation for the core runtime features
below, with additional hardening and integration work still in progress.

## Core features

### 1. Configure skill

Mindex now includes an initial `configure` skill plus a Python-based configure
workflow that acts as the central hub for project setup.

Current commands:

- `mindex configure --project-root <root> --dry-run`
- `python -m mindex.configure configure --project-root <root> --dry-run`

Implemented behavior:

- writes project instructions into `.mindex/codex_instructions.md`
- installs packaged skills into `~/.codex/skills/` or a provided Codex home
- writes a managed `[profiles.mindex]` block into the Codex config file
- prepares dependency installation commands for Miniconda, NPM, Tmux, and
  Codex
- records configure runs under `logs/`

Target workflows:

- **New installation**
  - support `pip install -e .`
  - install required dependencies, including Miniconda, Codex, NPM, and Tmux
  - use Codex during setup to configure project settings

- **Existing Codex workflow**
  - allow a user who already has Codex installed to invoke the `configure`
    skill directly inside Codex
  - configure the Mindex environment and required parameters without changing
    the base `codex` command behavior

### 2. Logging system

Mindex now includes an initial logging helper and launcher logging flow.

Requirements:

- all logs live under `logs/`
- logs are organized by session, timestamp, and prompt/task
- prompts, actions, outputs, and test results are captured together
- Codex activity should be recorded proactively rather than only after failure

Current implementation:

- `mindex.logging_utils` creates the log directory layout
- `mindex configure` writes prompt, action, metadata, and status files
- `mindex` launcher records command metadata and terminal capture paths
- repository work also records validation results under `logs/`

### 3. `mindex` command

Mindex now includes an initial `mindex` command entry point.

Requirements:

- `mindex` becomes the preferred project command
- the original `codex` command remains available and retains its normal
  behavior
- project-specific configuration is applied through Mindex rather than by
  replacing the global Codex installation

Current implementation:

- the package exposes `mindex` as a console script
- `mindex configure ...` runs the configure workflow
- other `mindex ...` invocations proxy to `codex` from the repo root
- when available, the launcher uses `script` to capture terminal I/O into
  `logs/`

### 4. Mindex repo skill

Mindex now includes an initial `Mindex repo` skill for working on this
repository itself.

Current packaged skills:

- `mindex/assets/skills/configure/`
- `mindex/assets/skills/mindex-repo/`

The repo skill is intended to:

- centralize repository-specific guidance
- help Codex understand the project workflow and structure
- reinforce testing, logging, and PR requirements when working in this repo

## Project rules

### Testing first

- work is not complete when code is only written
- every meaningful task must include explicit tests
- test results must be recorded in the logging system

### Git and PR workflow

- all changes are tracked in Git
- meaningful AI-generated work must be published to GitHub
- non-personal repositories should use fork-and-PR workflows
- personal repositories should use branch-and-PR workflows
- direct changes to `main` are not allowed

### README requirement

- all meaningful features and workflows must be documented in `README.md`

## Repository files

- `README.md` - feature and workflow documentation
- `HISTORY.md` - tracked requirements and status
- `logs/` - structured execution, validation, and policy logs
- `mindex/` - Python package for configure, logging, install hooks, skills,
  and launcher code
- `tests/` - automated validation for the package behavior
- `setup.py` - packaging plus editable-install hook entry

## Validation

Current automated validation includes:

- `python3 -m unittest discover -s tests -v`
- editable-install validation with `MINDEX_SKIP_AUTO_CONFIGURE=1 pip install -e .`
- dry-run configure validation through the installed `mindex` command

## Development note

The implementation work for the configure skill, runtime logging, repo skill,
and `mindex` launcher is now started in-repo and will continue through the
project's PR-based workflow until the remaining integration gaps are closed.
