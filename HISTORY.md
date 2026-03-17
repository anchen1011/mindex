# HISTORY

## 2026-03-17

### Active project requirements

### Project-wide operating rules

#### 0. Comprehensive Testing

- Status: active
- No task is complete when code is merely written; completion requires explicit
  testing and documented results.
- Test results must be recorded in the logging system with a clear record of
  what was executed and whether it succeeded or failed.
- Each task must be evaluated by the successful completion of its
  corresponding tests.
- After implementation and passing tests, Codex should do a simplification
  pass over the modifications and rerun the relevant tests to keep the project
  simple.

#### 0. Git Management and Pull Requests

- Status: active
- All changes must be managed under Git.
- Changes should be committed systematically as each sub-task is finished.
- Because the repository is hosted on GitHub, all contributions must go
  through a Pull Request workflow.
- Direct pushes are not allowed.
- Codex must not push code directly.
- Meaningful AI-generated work must be published to GitHub and must not remain
  only on the local machine.
- For non-personal repositories, use a fork-and-PR workflow and avoid direct
  changes to the original upstream repository.
- For personal repositories, use a branch-and-PR workflow in the same
  repository unless instructed otherwise.
- Changes should be prepared on branches and merged only through PR review.

#### 0. README Documentation

- Status: active
- All meaningful project features and workflows must be documented in
  `README.md`.
- README updates should be treated as part of feature completion, not as an
  optional follow-up task.

#### 1. Configure Skill Development

- Status: in progress
- Implement a `configure` skill that acts as the central hub for project
  configuration details and setup behavior.
- Support a new-install workflow where a user clones the project and runs
  `pip install -e .`.
- During that installation flow, automatically install required dependencies,
  including Miniconda, Codex, NPM, and Tmux.
- During that same installation flow, use the newly installed Codex to
  configure project settings.
- Support an existing-Codex workflow where a user who already has Codex can
  call the `configure` skill directly inside Codex to set up the environment
  and required parameters.

#### 2. Logging System Specification

- Status: in progress
- Define and implement a standardized logging system for critical Codex
  interactions.
- Capture input prompts together with the resulting actions and outputs.
- Store all logging artifacts under a `logs` directory.
- Organize the `logs` directory by session, timestamp, and specific prompt.
- Ensure Codex proactively records its own activity and internal processes into
  this logging structure.
- Keep `logs/` as a local working artifact rather than committing it to Git or
  GitHub.

#### 3. Command Alias (`mindex`)

- Status: in progress
- Implement `mindex` as the primary command for launching this project's Codex
  configuration.
- Keep the original `codex` command unchanged so its normal behavior remains
  intact outside the project-specific workflow.

#### 4. Repo Skill Under `mindex/`

- Status: in progress
- Create a dedicated `repo` skill under `mindex/` for working on this repository.
- Ensure the repo skill is documented in `README.md` alongside the other
  project features.

### Notes

- This file is being maintained as the current project history and requirement
  tracker for the initial implementation phase.
