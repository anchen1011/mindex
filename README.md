# Mindex

Mindex is a Codex wrapper and configuration layer that installs a managed Codex
environment with repeatable setup, proactive logging, and a GitHub PR-first
workflow across the tasks and repositories you run through `mindex`.

The important distinction is that Mindex is not a separate coding model. It is
a wrapper around Codex that installs persistent instructions, skills, and
profile settings so the coding agent follows the required executive behavior on
future tasks across projects launched through `mindex`.

## One-Click Configuration

There are two simple setup paths:

1. **Local install:** Clone the repository, then run `pip install -e .`
2. **Codex install:** Open Codex and send:
   `Use https://github.com/anchen1011/mindex configure skill to install and configure codex.`

## Project Highlights

1. **Skill-first configuration:** Mindex ships with a configure workflow that
   can be triggered from a direct install command or from a single Codex
   instruction.
2. **Systematic logging:** Mindex records prompts, actions, outputs, and
   validation results under `logs/` for better observability.
3. **Automatic commits and PRs:** Mindex defaults to feature-branch
   publication so changes are easier to trace, review, and control.
4. **Queue management UI:** Mindex includes a local UI for managing task
   queues and coding agents with better visibility across workflows.

Next, we will keep improving the Harness with a strong focus on **security**,
**testing**, and **memory**.

## Current status

The repository currently includes:

- project history and requirement tracking in `HISTORY.md`
- structured local task logs under `logs/`
- a documented testing-first and PR-first workflow
- an open GitHub PR workflow for AI-generated changes
- a secure local web UI for managing Mindex jobs and coding agents

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
- prints the active `CODEX_HOME`, managed instructions file, and logs root
  during configure so the user can see the current Mindex runtime targets
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
- `mindex` launcher records command metadata and terminal capture paths in the
  detected workspace `logs/` directory, or falls back to `~/.mindex/logs`
  when no project root is available
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
- when available, the launcher uses `script` to capture terminal I/O into the
  active workspace `logs/` directory or the managed `~/.mindex/logs` fallback
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

### 6. Secure web UI

Mindex now includes a browser-accessible control surface for managing queued
Mindex jobs and the coding agents launched through the wrapper.

Current commands:

- `mindex ui init-config --project-root <root>`
- `mindex ui serve --project-root <root>`
- `mindex ui serve --project-root <root> --dev`

Implemented behavior:

- creates or migrates `.mindex/ui_config.json` with a salted PBKDF2 password
  hash instead of storing a plaintext password
- defaults the server to `127.0.0.1` and requires an explicit
  `allow_remote=true` config choice before binding to non-localhost interfaces
- serves a simpler session-first browser view where each session owns one
  editable queue, supports drag-to-reorder queue items, and shows its visible
  output inline
- lets a new session be created from just its name and workdir, defaulting the
  underlying Mindex prompt and queue description automatically
- stores opaque session cookies in-memory, uses CSRF tokens for state-changing
  requests, and rate-limits repeated login failures
- supports explicit `--disable-origin-checks` and `--disable-csrf-checks`
  overrides for operators who need to bypass those protections in public or
  cross-origin deployments
- supports `mindex ui serve --dev` for local iteration, which watches the
  packaged `mindex/*.py` UI code plus `.mindex/ui_config.json`, restarts the
  child server on changes, and disables origin/CSRF checks in that dev child
  without permanently rewriting the saved UI config
- persists session queue state under `.mindex/task_queues.json`, including
  queue names, queue descriptions, and ordered task lists per managed session
- lets users add, edit, delete, and drag-to-reorder tasks inside each
  session-owned queue so upcoming work can be reprioritized directly in the
  browser, and automatically drives those tasks through `queued`, `running`,
  `completed`, or `failed` execution states instead of relying on manual task
  status entry; stopping a session interrupts the active task and returns it to
  the front of the queue so the next start resumes from that item
- presents each session itself as either `running` or `stopped`, and visually
  highlights the front queue item when it is the active running task
- keeps agent workdirs constrained to the configured project root and launches
  agents as `python -m mindex ...` without going through a shell; queued
  `exec` tasks automatically add `--skip-git-repo-check` so session-managed
  work can still run inside non-git workspaces that Mindex explicitly manages
- persists agent state under `.mindex/task_queues.json` and writes per-agent
  output under `.mindex/queue_logs/`
- migrates legacy `.mindex/ui_config.json` files that still contain a plaintext
  password and rewrites them into the secure hash-based format

Design direction:

- draws on the browser-accessible Codex control-room model shown by CodexUI and
  the OpenAI Codex app, especially around session visibility and agent
  management
- keeps the remote-access convenience of community Codex web frontends, but
  hardens Mindex with localhost-first binding, hashed credentials, CSRF
  protection, and origin checks because reference projects explicitly leave
  parts of that threat model to the operator

### 7. Multi-agent branch and PR coordination skill

Mindex now includes a packaged `multi-agent` skill for coordinating several
coding agents inside the same repository at the same time.

Implemented behavior:

- treats one agent as the owner of one goal, one feature branch, and one PR
- makes branch and PR isolation the default even when the user does not
  explicitly mention repository workflow details
- reinforces that new feature work should create a fresh branch, make the
  corresponding commit, and publish through the matching PR by default
- prevents different features from being mixed into the same in-flight branch
  or pull request
- requires each agent to inspect existing in-flight work before choosing a
  branch
- keeps integration between agents on reviewed PRs instead of ad hoc branch
  sharing
- records the agent, goal, branch, and PR status in local logs when that
  coordination data is available

### 8. Repository-local development skill

This repository also includes a top-level `SKILL.md` that guides agent work on
the Mindex project itself. It complements the packaged `repo` skill, but it is
more specific to maintaining this repository's source code, tests, logging, and
documentation.

Current packaged skills:

- `mindex/assets/skills/configure/`
- `mindex/assets/skills/multi-agent/`
- `mindex/assets/skills/repo/`

The repo skill is intended to:

- centralize repository-specific guidance
- help Codex understand the project workflow and structure
- reinforce testing, logging, GitHub publication, and PR requirements when
  working in this repo
- point concurrent work toward the packaged `multi-agent` coordination rules
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
- when multiple coding agents are active in the same repository, assign one
  branch and one PR per agent-owned goal by default
- do not wait for the user to mention repo or PR workflow before isolating
  concurrent agent work
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
- when multiple agents or parallel efforts are pursuing different goals, each
  goal should use its own branch and its own PR instead of being combined into
  one branch or one PR
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
  launcher code, task queues, and the secure web UI that wraps Codex with
  project policy
- `tests/` - automated validation for the package behavior
- `setup.py` - packaging plus editable-install hook entry

## Validation

Current automated validation includes:

- `python3 -m unittest discover -s tests -v`
- editable-install validation with `MINDEX_SKIP_AUTO_CONFIGURE=1 pip install -e .`
- dry-run configure validation through the installed `mindex` command
- secure UI config, live session/queue API flows, agent-manager, and CLI
  routing tests in `tests/test_ui.py`
- publish workflow validation with fake GitHub CLI responses and local Git
  remotes

## Development note

The implementation work for the configure skill, runtime logging, repo skill,
and `mindex` launcher is now started in-repo and will continue through the
project's PR-based workflow until the remaining integration gaps are closed.
