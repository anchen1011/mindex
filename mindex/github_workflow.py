from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shlex
import subprocess
from typing import Iterable

from mindex.logging_utils import append_action, create_log_run, slugify, utc_timestamp, write_status


PROTECTED_BRANCHES = {"main", "master", "production"}


class WorkflowError(RuntimeError):
    """Raised when the GitHub publication workflow cannot continue."""


@dataclass(frozen=True)
class RepositoryContext:
    current_branch: str
    default_branch: str
    repo_name_with_owner: str
    repo_owner: str
    repo_url: str
    viewer_login: str


@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    url: str
    state: str
    title: str
    head_ref_name: str
    base_ref_name: str


@dataclass(frozen=True)
class CommitSummary:
    sha: str
    subject: str


@dataclass(frozen=True)
class BranchScope:
    compare_ref: str
    commits: list[CommitSummary]
    changed_files: list[str]


@dataclass(frozen=True)
class PublishResult:
    branch_name: str
    base_branch: str
    push_remote: str
    used_fork: bool
    repository: str
    commit_created: bool
    pr_number: int
    pr_title: str
    pr_url: str
    pr_state: str
    log_dir: Path

    def to_json(self) -> str:
        payload = asdict(self)
        payload["log_dir"] = str(self.log_dir)
        return json.dumps(payload, indent=2, sort_keys=True)


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    log_run=None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if log_run is not None:
        append_action(log_run, f"$ {shlex.join(command)}")
        if completed.stdout.strip():
            append_action(log_run, f"stdout: {completed.stdout.strip()}")
        if completed.stderr.strip():
            append_action(log_run, f"stderr: {completed.stderr.strip()}")
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise WorkflowError(f"{shlex.join(command)} failed: {message}")
    return completed


def _git(
    project_root: Path,
    *args: str,
    env: dict[str, str] | None = None,
    log_run=None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run_command(["git", *args], cwd=project_root, env=env, log_run=log_run, check=check)


def _gh(
    project_root: Path,
    *args: str,
    env: dict[str, str] | None = None,
    log_run=None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run_command(["gh", *args], cwd=project_root, env=env, log_run=log_run, check=check)


def _is_git_repository(project_root: Path, *, env: dict[str, str] | None = None) -> bool:
    completed = _git(project_root, "rev-parse", "--is-inside-work-tree", env=env, check=False)
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def _git_branch_exists(project_root: Path, branch_name: str, *, env: dict[str, str] | None = None) -> bool:
    completed = _git(project_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}", env=env, check=False)
    return completed.returncode == 0


def _git_ref_exists(project_root: Path, ref_name: str, *, env: dict[str, str] | None = None) -> bool:
    completed = _git(project_root, "rev-parse", "--verify", "--quiet", ref_name, env=env, check=False)
    return completed.returncode == 0


def _git_remote_exists(project_root: Path, remote_name: str, *, env: dict[str, str] | None = None) -> bool:
    completed = _git(project_root, "remote", "get-url", remote_name, env=env, check=False)
    return completed.returncode == 0


def _compare_ref(project_root: Path, base_branch: str, *, env: dict[str, str] | None = None) -> str:
    remote_ref = f"origin/{base_branch}"
    if _git_ref_exists(project_root, remote_ref, env=env):
        return remote_ref
    return base_branch


def _build_branch_name(summary: str) -> str:
    cleaned = slugify(summary)[:48] or "change"
    return f"mindex/{cleaned}"


def _unique_branch_name(project_root: Path, base_name: str, *, env: dict[str, str] | None = None) -> str:
    if not _git_branch_exists(project_root, base_name, env=env):
        return base_name

    timestamped = f"{base_name}-{utc_timestamp().lower()}"
    if not _git_branch_exists(project_root, timestamped, env=env):
        return timestamped

    counter = 2
    while True:
        candidate = f"{timestamped}-{counter}"
        if not _git_branch_exists(project_root, candidate, env=env):
            return candidate
        counter += 1


def _humanize_branch_name(branch_name: str) -> str:
    branch_leaf = branch_name.split("/")[-1]
    words = [word for word in branch_leaf.replace("_", "-").split("-") if word]
    formatted: list[str] = []
    acronyms = {"pr": "PR", "github": "GitHub", "api": "API", "cli": "CLI"}
    for word in words:
        formatted.append(acronyms.get(word.lower(), word.capitalize()))
    return " ".join(formatted)


def get_branch_scope(
    project_root: Path,
    *,
    base_branch: str,
    env: dict[str, str] | None = None,
    log_run=None,
) -> BranchScope:
    compare_ref = _compare_ref(project_root, base_branch, env=env)
    commit_lines = _git(
        project_root,
        "log",
        "--reverse",
        "--format=%H%x1f%s",
        f"{compare_ref}..HEAD",
        env=env,
        log_run=log_run,
    ).stdout.splitlines()
    commits = []
    for line in commit_lines:
        if not line.strip():
            continue
        sha, subject = line.split("\x1f", 1)
        commits.append(CommitSummary(sha=sha, subject=subject))

    changed_files = [
        line.strip()
        for line in _git(
            project_root,
            "diff",
            "--name-only",
            f"{compare_ref}...HEAD",
            env=env,
            log_run=log_run,
        ).stdout.splitlines()
        if line.strip()
    ]
    return BranchScope(compare_ref=compare_ref, commits=commits, changed_files=changed_files)


def build_pr_title(
    *,
    branch_name: str,
    commit_message: str,
    explicit_title: str | None,
    scope: BranchScope,
) -> str:
    cleaned_title = explicit_title.strip() if explicit_title else ""
    if cleaned_title and cleaned_title != commit_message:
        return cleaned_title
    if len(scope.commits) == 1 and scope.commits[0].subject.strip():
        return scope.commits[0].subject.strip()
    branch_title = _humanize_branch_name(branch_name)
    if branch_title:
        return branch_title
    if cleaned_title:
        return cleaned_title
    return commit_message


def default_pr_body(
    *,
    branch_name: str,
    base_branch: str,
    scope: BranchScope,
    notes: str | None = None,
) -> str:
    lines = [
        "## Summary",
        f"- Covers the full branch scope from `{base_branch}` to `{branch_name}`.",
        f"- Includes {len(scope.commits)} commit(s) across {len(scope.changed_files)} changed file(s).",
        "",
        "## Included Commits",
    ]
    if scope.commits:
        for commit in scope.commits:
            lines.append(f"- {commit.subject} (`{commit.sha[:7]}`)")
    else:
        lines.append("- No commits are ahead of the base branch yet.")

    lines.extend(["", "## Changed Files"])
    if scope.changed_files:
        file_limit = 15
        for path in scope.changed_files[:file_limit]:
            lines.append(f"- `{path}`")
        remaining = len(scope.changed_files) - file_limit
        if remaining > 0:
            lines.append(f"- Plus {remaining} more file(s)")
    else:
        lines.append("- No file changes detected.")

    if notes and notes.strip():
        lines.extend(["", "## Additional Notes", notes.strip()])

    lines.extend(["", "Generated by Mindex's automated branch and PR workflow."])
    return "\n".join(lines)


def get_current_branch(project_root: Path, *, env: dict[str, str] | None = None, log_run=None) -> str:
    return _git(project_root, "rev-parse", "--abbrev-ref", "HEAD", env=env, log_run=log_run).stdout.strip()


def get_repository_context(
    project_root: Path,
    *,
    env: dict[str, str] | None = None,
    log_run=None,
) -> RepositoryContext:
    current_branch = get_current_branch(project_root, env=env, log_run=log_run)
    repo_payload = json.loads(
        _gh(
            project_root,
            "repo",
            "view",
            "--json",
            "nameWithOwner,isFork,url,defaultBranchRef,parent,owner",
            env=env,
            log_run=log_run,
        ).stdout
    )
    viewer_payload = json.loads(_gh(project_root, "api", "user", env=env, log_run=log_run).stdout)
    return RepositoryContext(
        current_branch=current_branch,
        default_branch=repo_payload["defaultBranchRef"]["name"],
        repo_name_with_owner=repo_payload["nameWithOwner"],
        repo_owner=repo_payload["owner"]["login"],
        repo_url=repo_payload["url"],
        viewer_login=viewer_payload["login"],
    )


def ensure_feature_branch(
    project_root: Path | str,
    *,
    summary: str,
    branch_name: str | None = None,
    env: dict[str, str] | None = None,
    log_run=None,
) -> str | None:
    resolved_root = Path(project_root).resolve()
    if not _is_git_repository(resolved_root, env=env):
        if log_run is not None:
            append_action(log_run, "Git branch automation skipped because the project root is not a Git repository.")
        return None

    current_branch = get_current_branch(resolved_root, env=env, log_run=log_run)
    if branch_name is None and current_branch not in PROTECTED_BRANCHES and current_branch != "HEAD":
        if log_run is not None:
            append_action(log_run, f"Reusing existing feature branch: {current_branch}")
        return current_branch

    target_branch = branch_name or _build_branch_name(summary)
    if target_branch in PROTECTED_BRANCHES:
        raise WorkflowError(f"Refusing to use protected branch {target_branch!r} for feature work.")

    if target_branch == current_branch:
        return target_branch

    if _git_branch_exists(resolved_root, target_branch, env=env):
        if branch_name is None:
            target_branch = _unique_branch_name(resolved_root, target_branch, env=env)
            _git(resolved_root, "switch", "-c", target_branch, env=env, log_run=log_run)
        else:
            _git(resolved_root, "switch", target_branch, env=env, log_run=log_run)
    else:
        _git(resolved_root, "switch", "-c", target_branch, env=env, log_run=log_run)
    return target_branch


def _get_push_remote(
    project_root: Path,
    context: RepositoryContext,
    *,
    env: dict[str, str] | None = None,
    log_run=None,
) -> tuple[str, bool]:
    if context.repo_owner == context.viewer_login:
        return "origin", False

    remote_name = context.viewer_login
    if _git_remote_exists(project_root, remote_name, env=env):
        return remote_name, True

    _gh(
        project_root,
        "repo",
        "fork",
        "--remote",
        "--remote-name",
        remote_name,
        env=env,
        log_run=log_run,
    )
    return remote_name, True


def _staged_changes_exist(project_root: Path, *, env: dict[str, str] | None = None) -> bool:
    completed = _git(project_root, "diff", "--cached", "--quiet", env=env, check=False)
    return completed.returncode == 1


def _working_tree_has_changes(project_root: Path, *, env: dict[str, str] | None = None) -> bool:
    status = _git(project_root, "status", "--short", env=env).stdout.strip()
    return bool(status)


def _ahead_count(project_root: Path, base_branch: str, *, env: dict[str, str] | None = None) -> int:
    compare_ref = _compare_ref(project_root, base_branch, env=env)
    return int(_git(project_root, "rev-list", "--count", f"{compare_ref}..HEAD", env=env).stdout.strip())


def _find_existing_pr(
    project_root: Path,
    *,
    branch_name: str,
    viewer_login: str,
    env: dict[str, str] | None = None,
    log_run=None,
) -> PullRequestInfo | None:
    selectors = [f"{viewer_login}:{branch_name}", branch_name]
    for selector in selectors:
        payload = json.loads(
            _gh(
                project_root,
                "pr",
                "list",
                "--state",
                "open",
                "--head",
                selector,
                "--json",
                "number,url,state,title,headRefName,baseRefName",
                env=env,
                log_run=log_run,
            ).stdout
        )
        if payload:
            item = payload[0]
            return PullRequestInfo(
                number=item["number"],
                url=item["url"],
                state=item["state"],
                title=item["title"],
                head_ref_name=item["headRefName"],
                base_ref_name=item["baseRefName"],
            )
    return None


def _verify_pull_request(
    project_root: Path,
    *,
    pr_reference: str,
    env: dict[str, str] | None = None,
    log_run=None,
) -> PullRequestInfo:
    payload = json.loads(
        _gh(
            project_root,
            "pr",
            "view",
            pr_reference,
            "--json",
            "number,url,state,title,headRefName,baseRefName",
            env=env,
            log_run=log_run,
        ).stdout
    )
    return PullRequestInfo(
        number=payload["number"],
        url=payload["url"],
        state=payload["state"],
        title=payload["title"],
        head_ref_name=payload["headRefName"],
        base_ref_name=payload["baseRefName"],
    )


def _update_pull_request_metadata(
    project_root: Path,
    *,
    repository: str,
    pr_number: int,
    title: str,
    body: str,
    env: dict[str, str] | None = None,
    log_run=None,
) -> None:
    _gh(
        project_root,
        "api",
        f"repos/{repository}/pulls/{pr_number}",
        "--method",
        "PATCH",
        "-f",
        f"title={title}",
        "-f",
        f"body={body}",
        env=env,
        log_run=log_run,
    )


def publish_pull_request(
    *,
    project_root: Path | str,
    commit_message: str,
    title: str | None = None,
    body: str | None = None,
    branch_name: str | None = None,
    base_branch: str | None = None,
    draft: bool = False,
    env: dict[str, str] | None = None,
) -> PublishResult:
    resolved_root = Path(project_root).resolve()
    if not _is_git_repository(resolved_root, env=env):
        raise WorkflowError(f"{resolved_root} is not a Git repository.")

    log_run = create_log_run(
        resolved_root / "logs",
        "publish-pr",
        prompt_text=f"mindex publish-pr --project-root {resolved_root}",
        metadata={
            "project_root": str(resolved_root),
            "commit_message": commit_message,
            "title": title,
            "branch_name": branch_name,
            "base_branch": base_branch,
            "draft": draft,
        },
    )

    try:
        context = get_repository_context(resolved_root, env=env, log_run=log_run)
        branch = ensure_feature_branch(
            resolved_root,
            summary=branch_name or title or commit_message,
            branch_name=branch_name,
            env=env,
            log_run=log_run,
        )
        if branch is None:
            raise WorkflowError("Unable to determine a working branch for publication.")

        active_context = get_repository_context(resolved_root, env=env, log_run=log_run)
        push_remote, used_fork = _get_push_remote(resolved_root, active_context, env=env, log_run=log_run)
        base = base_branch or active_context.default_branch

        commit_created = False
        if _working_tree_has_changes(resolved_root, env=env):
            _git(resolved_root, "add", "-A", env=env, log_run=log_run)
            if _staged_changes_exist(resolved_root, env=env):
                _git(resolved_root, "commit", "-m", commit_message, env=env, log_run=log_run)
                commit_created = True
            else:
                append_action(log_run, "Working tree changed, but no new staged diff remained after git add -A.")
        else:
            append_action(log_run, "No uncommitted changes detected; continuing with the existing branch commits.")

        if _ahead_count(resolved_root, base, env=env) == 0:
            raise WorkflowError(
                f"No commits are ahead of {base!r}; nothing is available to publish in a pull request."
            )

        scope = get_branch_scope(resolved_root, base_branch=base, env=env, log_run=log_run)
        pr_title = build_pr_title(
            branch_name=branch,
            commit_message=commit_message,
            explicit_title=title,
            scope=scope,
        )
        pr_body = default_pr_body(
            branch_name=branch,
            base_branch=base,
            scope=scope,
            notes=body,
        )

        _git(resolved_root, "push", "--set-upstream", push_remote, branch, env=env, log_run=log_run)

        head_ref = branch if not used_fork else f"{active_context.viewer_login}:{branch}"
        existing_pr = _find_existing_pr(
            resolved_root,
            branch_name=branch,
            viewer_login=active_context.viewer_login,
            env=env,
            log_run=log_run,
        )
        if existing_pr is not None:
            _update_pull_request_metadata(
                resolved_root,
                repository=active_context.repo_name_with_owner,
                pr_number=existing_pr.number,
                title=pr_title,
                body=pr_body,
                env=env,
                log_run=log_run,
            )
            pr_info = _verify_pull_request(resolved_root, pr_reference=existing_pr.url, env=env, log_run=log_run)
        else:
            command = [
                "pr",
                "create",
                "--base",
                base,
                "--head",
                head_ref,
                "--title",
                pr_title,
                "--body",
                pr_body,
            ]
            if draft:
                command.append("--draft")
            create_completed = _gh(resolved_root, *command, env=env, log_run=log_run, check=False)
            if create_completed.returncode != 0:
                existing_pr = _find_existing_pr(
                    resolved_root,
                    branch_name=branch,
                    viewer_login=active_context.viewer_login,
                    env=env,
                    log_run=log_run,
                )
                if existing_pr is None:
                    message = create_completed.stderr.strip() or create_completed.stdout.strip() or "gh pr create failed"
                    raise WorkflowError(message)
                _update_pull_request_metadata(
                    resolved_root,
                    repository=active_context.repo_name_with_owner,
                    pr_number=existing_pr.number,
                    title=pr_title,
                    body=pr_body,
                    env=env,
                    log_run=log_run,
                )
                pr_info = _verify_pull_request(resolved_root, pr_reference=existing_pr.url, env=env, log_run=log_run)
            else:
                pr_url = create_completed.stdout.strip().splitlines()[-1].strip()
                pr_info = _verify_pull_request(resolved_root, pr_reference=pr_url, env=env, log_run=log_run)
                _update_pull_request_metadata(
                    resolved_root,
                    repository=active_context.repo_name_with_owner,
                    pr_number=pr_info.number,
                    title=pr_title,
                    body=pr_body,
                    env=env,
                    log_run=log_run,
                )
                pr_info = _verify_pull_request(resolved_root, pr_reference=pr_url, env=env, log_run=log_run)

        write_status(
            log_run,
            "success",
            branch_name=branch,
            base_branch=base,
            push_remote=push_remote,
            used_fork=used_fork,
            pr_number=pr_info.number,
            pr_title=pr_title,
            pr_url=pr_info.url,
            pr_state=pr_info.state,
        )
    except Exception as exc:
        write_status(log_run, "failure", error=str(exc))
        raise

    return PublishResult(
        branch_name=branch,
        base_branch=base,
        push_remote=push_remote,
        used_fork=used_fork,
        repository=context.repo_name_with_owner,
        commit_created=commit_created,
        pr_number=pr_info.number,
        pr_title=pr_title,
        pr_url=pr_info.url,
        pr_state=pr_info.state,
        log_dir=log_run.run_dir,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish current work through a feature branch and GitHub pull request.")
    parser.add_argument("--project-root", required=True, help="Path to the project root")
    parser.add_argument("--message", required=True, help="Commit message for the publication commit")
    parser.add_argument("--title", help="Pull request title; defaults to the commit message")
    parser.add_argument("--body", help="Additional pull request notes; Mindex still generates the full branch summary")
    parser.add_argument("--branch", help="Branch name to create or reuse for the feature")
    parser.add_argument("--base", help="Override the base branch for the pull request")
    parser.add_argument("--draft", action="store_true", help="Create the pull request as a draft")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = publish_pull_request(
        project_root=args.project_root,
        commit_message=args.message,
        title=args.title,
        body=args.body,
        branch_name=args.branch,
        base_branch=args.base,
        draft=args.draft,
    )
    print(result.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
