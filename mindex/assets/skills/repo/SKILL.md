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
- create a fresh feature branch by default for a new feature, even when the user only asked for code or UI changes
- make the corresponding commits as part of the default workflow instead of leaving feature work uncommitted
- do not bundle independent features into one branch or PR
- avoid direct work on `main`, `master`, `production`, or other release branches
- if work starts from a protected branch, create a fresh feature branch before continuing
- never touch another person's branch unless the user explicitly asks
- when multiple agents are active in the same repository, give each agent a
  separate goal, branch, and PR by default
- do not wait for the user to mention branches or PRs before isolating
  concurrent agent work
- verify the PR exists on GitHub and capture its URL before considering publication complete
- ensure the PR title and description cover the cumulative branch scope, including every commit in that PR
- treat automatic GitHub publication as the default; create a new branch and PR for a new feature, and only keep adding to the current branch when the work is clearly a continuation of that branch's feature
- keep `logs/` local and uncommitted
- use the packaged `multi-agent` skill whenever work is being split across
  multiple coding agents in the same repository

## Typical workflow

1. Inspect `README.md` and `HISTORY.md` before changing behavior.
2. Create a fresh feature branch for the specific change; if this repo is not the user's own, prefer a fork owned by the user and open the PR from there.
3. If the work is being split across multiple agents, assign a different branch
   and PR to each goal before implementation starts.
4. Implement the smallest change that satisfies the documented requirement.
5. Run the relevant tests and keep the logging trail intact.
6. Simplify the implementation after tests pass, rerun the tests, make the corresponding feature commit, then use `mindex publish-pr` or an equivalent verified workflow to push the feature branch, create the PR, and confirm the PR URL on GitHub.
