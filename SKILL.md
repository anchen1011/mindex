---
name: repo-local
description: Use this repository-local skill when changing the `mindex` project itself rather than configuring another repository with the packaged Mindex skills.
metadata:
  short-description: Develop the Mindex repository itself
---

# mindex project development skill

Use this repository-local skill when the agent is changing the `mindex` project
itself rather than configuring some other repository with the packaged Mindex
skills.

Mindex is a managed Codex wrapper, so changes in this repository shape the
standing instructions that govern how Codex behaves on future work across
projects launched through `mindex`.

## Scope

This skill is for development work inside this repository only.

- shape the Python package, packaged skills, logging flow, and launcher behavior
- keep the project aligned with `README.md` and `HISTORY.md`
- treat this repository as the source of truth for how Mindex should evolve
- do not use this skill as a generic setup guide for unrelated projects

## Development rules

- start by reviewing `README.md` and `HISTORY.md` for the documented behavior
- preserve the testing-first rule: implement, run tests, simplify, rerun tests
- record validation commands and results under `logs/`
- update `README.md` whenever a meaningful workflow or feature changes
- keep the packaged skills under `mindex/assets/skills/` in sync with shipped behavior
- prefer focused changes that strengthen the `mindex` package instead of adding repo-only hacks
- keep `codex` unchanged; Mindex-managed behavior belongs in `mindex`
- treat branch-per-feature and PR-first development as the default workflow
- preserve the rule that meaningful AI-generated work is pushed to GitHub through feature branches and PRs
- use `mindex publish-pr` or an equivalent verified workflow before considering meaningful repository work complete
- require Mindex-managed publication to verify the PR URL on GitHub before the task is considered published
- require Mindex-managed PR titles and bodies to describe the complete branch scope, not just the newest commit
- treat automatic GitHub publication as the default completion path for each meaningful interaction, using a new branch/PR for new feature work and the existing PR only for true follow-up work on that branch
- when multiple agents or parallel efforts are working toward different goals, keep each goal on its own branch and PR instead of mixing them together
- apply that publication default even when the user only asks for code, docs, tests, or behavior changes and does not explicitly mention repo workflow, Git, GitHub, branches, or PRs
- never allow Mindex-managed behavior to push directly to `main`, `master`, `production`, or another person's branch

## Expected validation

Run the smallest useful set of validations for the change, and prefer the
project's standard checks:

1. `python3 -m unittest discover -s tests -v`
2. `MINDEX_SKIP_AUTO_CONFIGURE=1 pip install -e .`
3. `mindex configure --project-root <repo-root> --dry-run`

If a change affects packaging, installation, or launcher behavior, add the
relevant validation and record the result in `logs/`.

## Completion checklist

1. The code matches the documented behavior.
2. The repo-local skill and packaged skills still agree on the important rules.
3. Tests or validation commands were run and recorded.
4. `README.md` reflects the current feature set and workflow.
5. Meaningful repository work was published with `mindex publish-pr` or an equivalent verified branch-and-PR workflow, and the PR URL was confirmed.
