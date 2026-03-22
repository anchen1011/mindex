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

1. **Local install:** Clone the repository, then run `python3 -m pip install -e .`
2. **Codex install:** Open Codex and send:
   `Use https://github.com/anchen1011/mindex configure skill to install and configure codex.`

## Fastest UI Setup

If you only care about the browser UI, this is the shortest path:

```bash
mindex ui setup
mindex ui serve
```

What happens:

1. `mindex ui setup`
   - installs Codoxear into `~/.mindex/codoxear/venv`
   - creates a secure config at `~/.mindex/codoxear/config.json`
   - prompts you for the UI password without saving it in plaintext
2. `mindex ui serve`
   - starts the UI on `http://127.0.0.1:8743/`
   - asks for the password again so the plaintext password never needs to be stored in the config file

If you prefer a single executable, Mindex also installs:

```bash
mindex-ui-setup
```

That command is just a shortcut for `mindex ui setup`.

## Project Highlights

1. **Skill-first configuration:** Mindex ships with a configure workflow that
   can be triggered from a direct install command or from a single Codex
   instruction.
2. **Systematic logging:** Mindex records prompts, actions, outputs, and
   validation results under `logs/` for better observability.
3. **Automatic commits and PRs:** Mindex defaults to feature-branch
   publication so changes are easier to trace, review, and control.
4. **Codoxear UI integration:** Mindex can launch Codoxear's mobile-friendly UI
   for Codex sessions while keeping Mindex-managed settings and avoiding
   plaintext password storage.

## How Mindex, Codex, and RTK fit together

- `mindex` is a wrapper around Codex. It does not replace the Codex model.
- `mindex` launches Codex with a separate managed home at
  `~/.mindex/codex-home`.
- plain `codex` stays vanilla by default and does not automatically inherit the
  Mindex-managed setup.
- when `rtk` is installed, `mindex configure` and the `mindex` launcher both
  ensure RTK is initialized inside that managed Codex home, so `mindex`
  sessions default to RTK-aware shell usage.
- if `rtk` is not installed yet, Mindex still runs, but RTK-specific behavior
  cannot activate until `rtk` is installed.

In short:

- use `mindex` when you want Mindex rules plus RTK-by-default behavior
- use plain `codex` when you want the untouched vanilla Codex workflow

Next, we will keep improving the Harness with a strong focus on **security**,
**testing**, and **memory**.

## Current status

The repository currently includes:

- project history and requirement tracking in `HISTORY.md`
- structured local task logs under `logs/`
- a documented testing-first and PR-first workflow
- an open GitHub PR workflow for AI-generated changes
- a Codoxear-based UI workflow for mobile-friendly session handoff (with
  Mindex-managed config and safer secret handling)

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

- installing Mindex with `python3 -m pip install .` or
  `python3 -m pip install -e .` installs the
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
- makes the managed `mindex` profile default to YOLO execution with
  `approval_policy = "never"` and `sandbox_mode = "danger-full-access"`
- when `rtk` is installed, runs `rtk init --codex` inside the managed
  `~/.mindex/codex-home` so `mindex`-launched Codex sessions default to RTK
  instructions
- leaves the original `codex` command installed and unchanged, so plain Codex
  remains vanilla unless the user explicitly opts into the Mindex-managed setup
- prepares dependency installation commands for Miniconda, NPM, Tmux, Codex,
  and RTK
- records configure runs under `logs/`

Target workflows:

- **New installation**
  - support `python3 -m pip install .` and `python3 -m pip install -e .`
  - install Mindex so the `mindex` command is ready as the enhanced Codex entry
    point across projects
  - allow `mindex configure` to be run globally without a project argument
  - keep the original `codex` command available in its normal vanilla state
  - install required dependencies, including Miniconda, Codex, NPM, Tmux, and
    RTK

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
- `mindex` ensures the managed Codex home contains RTK Codex instructions when
  the `rtk` binary is available, so `mindex` sessions default to RTK-aware
  shell command usage
- `mindex` also defaults Codex launches into YOLO mode by prepending
  `--dangerously-bypass-approvals-and-sandbox` unless the user already
  supplied explicit approval or sandbox flags for that run
- when `mindex` starts inside a project directory that is not yet a Git
  repository, it initializes a local Git repository first so branch-based
  local version management is available even before any GitHub remote exists
- `mindex publish-pr ...` creates a safe feature branch when needed, commits
  the current work, pushes it, creates the pull request, and verifies the PR
  URL on GitHub
- `mindex publish-pr ...` regenerates the PR title/body from the full branch
  scope so the PR reflects every commit included in that branch, not just the
  latest change
- other `mindex ...` invocations proxy to `codex` from the detected workspace
  root
- plain `codex` still exists as the unchanged vanilla command outside the
  Mindex-managed workflow, including RTK, unless the user configures vanilla
  Codex separately
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

### 6. Codoxear UI (recommended)

Mindex no longer ships an in-tree UI implementation. Instead, it integrates
with Codoxear, an external lightweight web UI designed for continuing the same
live Codex TUI session from a phone or browser.

If you are a new user, you can treat this as a 3-step workflow:

```bash
mindex ui setup
mindex ui serve
```

Then open:

- `http://127.0.0.1:8743/`

If you configured a URL prefix such as `--url-prefix /codoxear`, open:

- `http://127.0.0.1:8743/codoxear/`

What `mindex ui setup` does:

1. installs Codoxear into an isolated venv under `~/.mindex/codoxear/venv`
2. creates a config at `~/.mindex/codoxear/config.json` if you do not already
   have one
3. prompts you for a password and stores only a salted PBKDF2 hash, never the
   plaintext password
4. if a config already exists, keeps it unchanged by default
5. prints the exact browser URL and follow-up commands you need

If you want to rotate the password or replace the existing settings, use:

```bash
mindex ui setup --reset-config
```

What `mindex ui serve` does:

1. loads the secure config
2. asks for the password again, unless you explicitly pass `--password`
3. verifies that password against the stored salted hash
4. starts Codoxear with the right environment variables for Mindex

Most important commands:

- First-time setup: `mindex ui setup`
- Start the UI later: `mindex ui serve`
- Change password or rotate settings: `mindex ui reset-config`
- Register a terminal-owned Codex session: `mindex ui broker -- <codex args>`
- Low-level commands are also available under `mindex codoxear ...`
- Shortcut executable: `mindex-ui-setup`

Implemented behavior:

- installs Codoxear into an isolated venv at `~/.mindex/codoxear/venv` (no
  reliance on system `pip`)
- pins Codoxear to a known-good commit by default for reproducible installs
  (override with `mindex codoxear install --source <pip-target>` if you want
  to track upstream)
- stores configuration under `~/.mindex/codoxear/config.json` (or
  `MINDEX_CODOXEAR_CONFIG_PATH`), not inside any repository
- never stores the Codoxear password in plaintext; only a salted PBKDF2 hash is
  persisted
- prompts for the password at serve time (or accepts `--password`) and then
  passes it to Codoxear via the `CODEX_WEB_PASSWORD` environment variable for
  the duration of the server process
- defaults to localhost-only binding (`127.0.0.1`) and refuses to bind to
  `0.0.0.0` / `::` unless `allow_remote=true` is explicitly configured
- defaults `CODEX_BIN` to `mindex` so Codoxear-launched sessions inherit
  Mindex-managed behavior by default
- redacts `--password ...` values from Mindex logs (still avoid using
  `--password` if shell history is a concern)

Beginner guide:

1. Install Mindex itself.
   Example:
   ```bash
   python3 -m pip install -e .
   ```
2. Run the one-step UI setup:
   ```bash
   mindex ui setup
   ```
3. Start the UI:
   ```bash
   mindex ui serve
   ```
4. Open the URL printed by Mindex in your browser.
5. If you want your terminal-started sessions to appear in the UI, use the
   broker:
   ```bash
   mindex ui broker -- <codex args>
   ```

Where things live:

- Codoxear venv: `~/.mindex/codoxear/venv`
- Secure config: `~/.mindex/codoxear/config.json`
- Mindex-managed Codex home: `~/.mindex/codex-home`
- Mindex logs: `~/.mindex/logs` when no project-local logs directory applies

Password and security model:

- the password hash is stored in config
- the plaintext password is not stored in config
- the plaintext password is passed to Codoxear only when the server process is
  started
- `mindex ui serve` verifies the password against the stored hash before
  launching the server
- localhost-only is the default
- public binding requires explicit opt-in
- Codoxear upstream does not provide TLS, so network security is still your
  responsibility if you expose it beyond localhost

Using the broker:

- Codoxear also provides a broker for registering terminal-owned sessions.
  Mindex exposes it as `mindex ui broker` so you do not need to put the venv
  binary on your `PATH`.
- If you want a short shell helper, add this function:

  ```sh
  codox() {
    mindex ui broker -- "$@"
  }
  ```

  Brokered sessions also use `CODEX_BIN=mindex` by default, so they inherit
  the Mindex-managed behavior.

Remote access (explicit opt-in):

- Localhost-only is the default. If you really want LAN access, rotate the
  config with explicit public binding:

  ```bash
  mindex ui reset-config --allow-remote --host 0.0.0.0
  ```

  Then start the UI again:

  ```bash
  mindex ui serve
  ```

Security warning:

- Codoxear's upstream security model is intentionally minimal (password gating
  only, no TLS). If you enable remote binding, use a secure transport (VPN,
  SSH port-forward, or TLS reverse proxy).

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
  launcher code, Codoxear integration, and the Codex wrapper policy
- `tests/` - automated validation for the package behavior
- `setup.py` - packaging plus editable-install hook entry

## Validation

Current automated validation includes:

- `python3 -m unittest discover -s tests -v`
- editable-install validation with `MINDEX_SKIP_AUTO_CONFIGURE=1 python3 -m pip install -e .`
- dry-run configure validation through the installed `mindex` command
- Codoxear install/setup/config/serve/broker security and CLI routing tests in
  `tests/test_codoxear.py`
- publish workflow validation with fake GitHub CLI responses and local Git
  remotes

## Development note

The implementation work for the configure skill, runtime logging, repo skill,
and `mindex` launcher is now started in-repo and will continue through the
project's PR-based workflow until the remaining integration gaps are closed.
