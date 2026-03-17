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

#### 0. Git Management and Pull Requests

- Status: active
- All changes must be managed under Git.
- Changes should be committed systematically as each sub-task is finished.
- Because the repository is hosted on GitHub, contributions should follow a
  Pull Request workflow rather than direct pushes to `main`.
- Direct pushes to `main` are not allowed; changes should be prepared on
  branches and merged through PR review.

#### 1. Configure Skill Development

- Status: pending
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

- Status: pending
- Define and implement a standardized logging system for critical Codex
  interactions.
- Capture input prompts together with the resulting actions and outputs.
- Store all logging artifacts under a `logs` directory.
- Organize the `logs` directory by session, timestamp, and specific prompt.
- Ensure Codex proactively records its own activity and internal processes into
  this logging structure.

#### 3. Command Alias (`mindex`)

- Status: pending
- Implement `mindex` as the primary command for launching this project's Codex
  configuration.
- Keep the original `codex` command unchanged so its normal behavior remains
  intact outside the project-specific workflow.

### Notes

- This file is being maintained as the current project history and requirement
  tracker for the initial implementation phase.
