# Mindex

Mindex is a Codex wrapper and configuration layer that installs a managed Codex
environment with repeatable setup, proactive logging, and a GitHub PR-first
workflow across the tasks and repositories you run through `mindex`.

The important distinction is that Mindex is not a separate coding model. It is
a wrapper around Codex that installs persistent instructions, skills, and
profile settings so the coding agent follows the required executive behavior on
future tasks across projects launched through `mindex`.

## Current status

The repository currently includes:

- project history and requirement tracking in `HISTORY.md`
- structured local task logs under `logs/`
- a documented testing-first and PR-first workflow
- an open GitHub PR workflow for AI-generated changes

The repository now has an initial implementation for the core runtime features
below, with additional hardening and integration work still in progress.

## Core features

### 1. Configure skill

Mindex now includes a packaged `configure` skill plus a Python-based configure
workflow that acts as the central hub for project setup.

Current commands:

- `mindex configure --dry-run`
- `python -m mindex.configure configure --dry-run`
- `mindex configure --project-root <root> --dry-run`

Implemented behavior:

- installing Mindex with `pip install .` or `pip install -e .` installs the
  `mindex` command and runs Mindex auto-configure by default unless
  `MINDEX_SKIP_AUTO_CONFIGURE=1`
- that install flow turns `mindex` into the Mindex-enhanced Codex entry point
  by writing the managed instructions, packaged skills, and profile settings
  described here
- writes managed instructions into `~/.mindex/codex-home/mindex_instructions.md`
- keeps a separate Mindex-managed Codex home under `~/.mindex/codex-home` by
  default instead of reusing `~/.codex`
- lets `mindex configure` run without `--project-root`, defaulting to the
  current directory only when workspace context is needed
- installs packaged skills into `~/.mindex/codex-home/skills/` or a provided
  Codex home
- symlinks packaged skills back to the source tree when possible so editable
  installs pick up skill edits from the repo immediately
- writes a managed `[profiles.mindex]` block into the Codex config file
- leaves the original `codex` command installed and unchanged, so plain Codex
  remains vanilla unless the user explicitly opts into the Mindex-managed setup
- prepares dependency installation commands for Miniconda, NPM, Tmux, and
  Codex
- records configure runs under `logs/`

Target workflows:

- **New installation**
  - support `pip install .` and `pip install -e .`
  - install Mindex so the `mindex` command is ready as the enhanced Codex entry
    point across projects
  - allow `mindex configure` to be run globally without a project argument
  - keep the original `codex` command available in its normal vanilla state
  - install required dependencies, including Miniconda, Codex, NPM, and Tmux

- **Existing Codex workflow**
  - allow a user who already has Codex installed to ask Codex to configure
    Mindex or to run `mindex configure` directly
  - apply the same managed instructions, packaged skills, and Mindex profile to
    that Codex environment
  - configure Codex with the Mindex coding-agent rules across the tasks and
    repositories launched through `mindex` while still leaving the base
    `codex` command behavior untouched unless the user chooses the
    Mindex-managed setup

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
- `mindex publish-pr` records branch, push, and PR verification steps under
  `logs/`
- `mindex` launcher can auto-publish completed coding sessions so each
  interaction is reflected on GitHub by default
- repository work also records validation results under the local `logs/`
  directory
- `logs/` is intended as a local artifact and should not be committed to Git

### 3. `mindex` command

Mindex now includes an initial `mindex` command entry point.

Requirements:

- `mindex` becomes the preferred project command
- the original `codex` command remains available and retains its normal
  behavior
- Mindex-managed configuration is applied through `mindex` across projects
  rather than by replacing the global Codex installation

Current implementation:

- the package exposes `mindex` as a console script and intended enhanced Codex
  entry point across projects
- `mindex configure ...` runs the configure workflow
- `mindex` launches Codex with `CODEX_HOME` pointed at the Mindex-managed
  `~/.mindex/codex-home` by default
- `mindex publish-pr ...` creates a safe feature branch when needed, commits
  the current work, pushes it, creates the pull request, and verifies the PR
  URL on GitHub
- `mindex publish-pr ...` regenerates the PR title/body from the full branch
  scope so the PR reflects every commit included in that branch, not just the
  latest change
- other `mindex ...` invocations proxy to `codex` from the detected workspace
  root
- plain `codex` still exists as the unchanged vanilla command outside the
  Mindex-managed workflow
- when a `mindex`-launched Codex session starts on `main`, `master`,
  `production`, or another protected branch, Mindex first creates and switches
  to a fresh feature branch
- after a `mindex`-launched coding session finishes, Mindex auto-publishes the
  resulting branch changes to GitHub by default, creating a new PR when needed
  or updating the existing PR for that branch
- when available, the launcher uses `script` to capture terminal I/O into
  `logs/`
- the managed instructions describe Mindex as a Codex wrapper and enforce the
  repository's branch, fork, and PR protocol

### 4. Repo skill under `mindex/`

Mindex now includes an initial `repo` skill under `mindex/` for working on this
repository itself.

### 5. GitHub publication workflow

Mindex now includes an initial automated publication workflow for GitHub PRs.

Current commands:

- `mindex publish-pr --message "<commit message>"`

Implemented behavior:

- refuses to publish from `main`, `master`, `production`, or another protected
  branch without first creating a fresh feature branch
- reuses the current non-protected feature branch when it is already suitable
  for the task
- prefers a fork remote for non-personal repositories when the authenticated
  GitHub user is not the upstream owner
- stages and commits the current work when the working tree is dirty
- pushes the feature branch, creates or updates the pull request, and verifies
  that the PR can be located on GitHub
- regenerates the PR description from all commits and changed files on the
  branch so the published PR matches the full feature scope
- is the default publication path for completed Mindex coding interactions, so
  GitHub reflection does not depend on a separate manual request
- records publication metadata, command output, and PR verification details
  under `logs/`

### 6. Repository-local development skill

This repository also includes a top-level `SKILL.md` that guides agent work on
the Mindex project itself. It complements the packaged `repo` skill, but it is
more specific to maintaining this repository's source code, tests, logging, and
documentation.

Current packaged skills:

- `mindex/assets/skills/configure/`
- `mindex/assets/skills/repo/`

The repo skill is intended to:

- centralize repository-specific guidance
- help Codex understand the project workflow and structure
- reinforce testing, logging, GitHub publication, and PR requirements when
  working in this repo
- require meaningful repository work to be published with `mindex publish-pr`
  or an equivalent verified PR workflow before it is considered complete

## Project rules

### Testing first

- work is not complete when code is only written
- every meaningful task must include explicit tests
- test results must be recorded in the logging system
- after tests pass, Codex should do a simplification pass and rerun the
  relevant tests so the project stays simple

### Git and PR workflow

- all changes are tracked in Git
- meaningful AI-generated work must be published to GitHub
- use one branch per feature
- submit one PR per feature
- avoid combining multiple features into one branch or one PR
- if work begins on `main`, `master`, `production`, or another protected
  branch, create a new feature branch before continuing
- for personal repositories, create a fresh feature branch for each specific
  feature and push that branch before opening the PR
- for repositories owned by someone else or by an organization, fork to the
  user's own account whenever possible, do the work there, and submit the PR
  from the user's account
- if forking is not possible, create a new feature branch inside the original
  repository without touching anyone else's branch
- publication is not complete until the PR is verified on GitHub and its URL is
  captured
- the PR title and body must describe the cumulative scope of the branch, not
  only the most recent commit
- GitHub publication should happen automatically for each meaningful
  interaction; starting from a protected branch should create a new feature
  branch and PR, while follow-up work on the same feature branch should update
  that branch's existing PR
- that automatic branch-and-PR publication behavior is still the default even
  when the user only asks for code, docs, tests, or behavior changes and does
  not explicitly mention repo workflow, Git, GitHub, branches, or PRs
- never push directly to `main`, `master`, `production`, or another protected
  release branch
- never work on or overwrite another person's branch unless the user explicitly
  instructs it

### README requirement

- all meaningful features and workflows must be documented in `README.md`

### Wrapper policy

- Mindex is a Codex wrapper, not a replacement model
- its managed instructions and packaged skills define Codex's standing
  repository policy for future work
- future editor-driven features must continue to preserve the branch, fork, PR,
  testing, and logging rules described here

## Repository files

- `SKILL.md` - repository-local agent guidance for developing Mindex itself
- `README.md` - feature and workflow documentation
- `HISTORY.md` - tracked requirements and status
- `logs/` - structured local execution, validation, and policy logs
- `mindex/` - Python package for configure, logging, install hooks, skills,
  and launcher code that wraps Codex with project policy
- `tests/` - automated validation for the package behavior
- `setup.py` - packaging plus editable-install hook entry

## Validation

Current automated validation includes:

- `python3 -m unittest discover -s tests -v`
- editable-install validation with `MINDEX_SKIP_AUTO_CONFIGURE=1 pip install -e .`
- dry-run configure validation through the installed `mindex` command
- publish workflow validation with fake GitHub CLI responses and local Git
  remotes

## Development note

The implementation work for the configure skill, runtime logging, repo skill,
and `mindex` launcher is now started in-repo and will continue through the
project's PR-based workflow until the remaining integration gaps are closed.
