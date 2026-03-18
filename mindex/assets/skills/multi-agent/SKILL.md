---
name: multi-agent
description: Use this skill when multiple Mindex-managed coding agents are working in the same repository at the same time.
metadata:
  short-description: Coordinate multiple agents in one repository
---

# multi-agent skill

Use this skill whenever multiple Mindex-managed coding agents are working in the
same repository, even if the user did not explicitly mention branches or pull
requests.

Mindex is a Codex wrapper, so concurrent work still has to follow the same
branch, logging, and GitHub publication rules by default.

## Coordination rules

- treat one agent as the owner of one goal, feature branch, and PR
- create a separate branch for every independent goal before editing files
- create or update one PR per branch; do not bundle unrelated goals into one PR
- never let two agents share the same in-flight branch unless the user
  explicitly asks for coordinated work on that branch
- never reuse another agent's branch or PR for a different goal
- inspect the active branch, local branches, and any known in-flight PRs before
  choosing a branch name
- choose branch names that encode the specific goal so ownership is obvious
- keep each agent's edits, tests, commits, and PR description limited to that
  branch's goal
- record the agent identity, assigned goal, branch, base branch, and PR status
  in the local logs whenever available
- merge or integrate agents through reviewed PRs instead of ad hoc branch
  sharing

## Default workflow

1. Split the requested work into independent goals before launching agents.
2. Assign one agent and one fresh branch to each goal.
3. Confirm the branch is not already carrying unrelated in-flight work from a
   different agent.
4. Implement, test, and log the work inside that branch only.
5. Make the corresponding commit for that branch's goal, then publish or update the matching PR and capture the PR URL.
6. Return a coordination summary that names each goal, owning agent, branch,
   PR, and any dependency or merge-order constraint.
