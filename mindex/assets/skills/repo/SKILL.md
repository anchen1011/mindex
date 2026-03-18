---
name: repo
description: Use this skill when working on the Mindex repository itself.
metadata:
  short-description: Work on the Mindex repository
---

# repo skill

Use this skill when working on the Mindex repository itself.

Mindex is a Codex wrapper, so this skill defines the ongoing executive behavior
for how Codex should work in this repository.

## Rules

- run explicit tests for every meaningful change
- record validation results under `logs/`
- keep `README.md` aligned with shipped behavior
- publish meaningful work to GitHub through a PR workflow
- use one branch per feature and one PR per feature
- do not bundle independent features into one branch or PR
- avoid direct work on `main`, `master`, `production`, or other release branches
- if work starts from a protected branch, create a fresh feature branch before continuing
- never touch another person's branch unless the user explicitly asks
- verify the PR exists on GitHub and capture its URL before considering publication complete
- keep `logs/` local and uncommitted

## Typical workflow

1. Inspect `README.md` and `HISTORY.md` before changing behavior.
2. Create a fresh feature branch for the specific change; if this repo is not the user's own, prefer a fork owned by the user and open the PR from there.
3. Implement the smallest change that satisfies the documented requirement.
4. Run the relevant tests and keep the logging trail intact.
5. Simplify the implementation after tests pass, rerun the tests, then use `mindex publish-pr` or an equivalent verified workflow to push the feature branch, create the PR, and confirm the PR URL on GitHub.
