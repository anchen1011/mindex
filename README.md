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

The repository is being expanded to add the core runtime features below.

## Planned features

### 1. Configure skill

Mindex will provide a `configure` skill that acts as the central hub for
project setup.

Planned workflows:

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

Mindex will standardize logging for important Codex activity.

Requirements:

- all logs live under `logs/`
- logs are organized by session, timestamp, and prompt/task
- prompts, actions, outputs, and test results are captured together
- Codex activity should be recorded proactively rather than only after failure

### 3. `mindex` command

Mindex will provide a dedicated `mindex` command that launches Codex with the
project's configuration.

Requirements:

- `mindex` becomes the preferred project command
- the original `codex` command remains available and retains its normal
  behavior
- project-specific configuration is applied through Mindex rather than by
  replacing the global Codex installation

### 4. Mindex repo skill

The project will also provide a dedicated `Mindex repo` skill for working on
this repository itself.

This repo skill is intended to:

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

## Development note

The implementation work for the configure skill, runtime logging, repo skill,
and `mindex` launcher is still in progress and will be completed through the
project's PR-based workflow.
