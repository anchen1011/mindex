from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from mindex.github_workflow import ensure_feature_branch, publish_pull_request


class GitHubWorkflowTests(unittest.TestCase):
    def _run(self, command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=str(cwd), env=env, check=True, text=True, capture_output=True)

    def _init_repo(self, root: Path, remote_root: Path) -> None:
        self._run(["git", "init", "--bare", str(remote_root)], cwd=root.parent)
        self._run(["git", "init", "-b", "main"], cwd=root)
        self._run(["git", "config", "user.name", "Test User"], cwd=root)
        self._run(["git", "config", "user.email", "test@example.com"], cwd=root)
        (root / "README.md").write_text("# repo\n", encoding="utf-8")
        (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")
        self._run(["git", "add", "README.md", "HISTORY.md"], cwd=root)
        self._run(["git", "commit", "-m", "Initial commit"], cwd=root)
        self._run(["git", "remote", "add", "origin", str(remote_root)], cwd=root)
        self._run(["git", "push", "-u", "origin", "main"], cwd=root)

    def _write_fake_gh(self, script_path: Path) -> None:
        script_path.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "\n"
            "state_path = Path(os.environ['FAKE_GH_STATE'])\n"
            "state = json.loads(state_path.read_text(encoding='utf-8'))\n"
            "args = sys.argv[1:]\n"
            "\n"
            "def write_state() -> None:\n"
            "    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')\n"
            "\n"
            "if args[:2] == ['repo', 'view']:\n"
            "    payload = {\n"
            "        'nameWithOwner': state['repo_name_with_owner'],\n"
            "        'isFork': False,\n"
            "        'url': state['repo_url'],\n"
            "        'defaultBranchRef': {'name': state['default_branch']},\n"
            "        'parent': None,\n"
            "        'owner': {'login': state['repo_owner']},\n"
            "    }\n"
            "    print(json.dumps(payload))\n"
            "elif args[:2] == ['api', 'user']:\n"
            "    print(json.dumps({'login': state['viewer_login']}))\n"
            "elif len(args) >= 5 and args[0] == 'api' and args[1].startswith('repos/') and args[2] == '--method' and args[3] == 'PATCH':\n"
            "    pr = state.get('pr')\n"
            "    if not pr:\n"
            "        print('missing pr', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            "    pr['payload']['title'] = args[args.index('-f') + 1].split('=', 1)[1]\n"
            "    pr['payload']['body'] = args[args.index('-f', args.index('-f') + 1) + 1].split('=', 1)[1]\n"
            "    write_state()\n"
            "    print(json.dumps(pr['payload']))\n"
            "elif args[:2] == ['pr', 'list']:\n"
            "    selector = args[args.index('--head') + 1]\n"
            "    pr = state.get('pr')\n"
            "    if pr and selector in pr['selectors']:\n"
            "        print(json.dumps([pr['payload']]))\n"
            "    else:\n"
            "        print('[]')\n"
            "elif args[:2] == ['pr', 'create']:\n"
            "    base = args[args.index('--base') + 1]\n"
            "    head = args[args.index('--head') + 1]\n"
            "    title = args[args.index('--title') + 1]\n"
            "    body = args[args.index('--body') + 1]\n"
            "    branch = head.split(':', 1)[-1]\n"
            "    url = state['repo_url'] + '/pull/1'\n"
            "    payload = {\n"
            "        'number': 1,\n"
            "        'url': url,\n"
            "        'state': 'OPEN',\n"
            "        'title': title,\n"
            "        'body': body,\n"
            "        'headRefName': branch,\n"
            "        'baseRefName': base,\n"
            "    }\n"
            "    state['pr'] = {\n"
            "        'payload': payload,\n"
            "        'selectors': [head, branch],\n"
            "    }\n"
            "    write_state()\n"
            "    print(url)\n"
            "elif args[:2] == ['pr', 'view']:\n"
            "    pr = state.get('pr')\n"
            "    if not pr:\n"
            "        print('missing pr', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            "    print(json.dumps(pr['payload']))\n"
            "else:\n"
            "    print('unsupported gh command: ' + ' '.join(args), file=sys.stderr)\n"
            "    sys.exit(1)\n",
            encoding="utf-8",
        )
        script_path.chmod(0o755)

    def test_publish_pull_request_creates_branch_push_and_verified_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            remote_root = Path(tmpdir) / "remote.git"
            bin_dir = Path(tmpdir) / "bin"
            state_path = Path(tmpdir) / "fake-gh-state.json"
            root.mkdir()
            bin_dir.mkdir()
            self._init_repo(root, remote_root)

            fake_gh = bin_dir / "gh"
            self._write_fake_gh(fake_gh)
            state_path.write_text(
                json.dumps(
                    {
                        "viewer_login": "anchen1011",
                        "repo_name_with_owner": "anchen1011/mindex",
                        "repo_owner": "anchen1011",
                        "repo_url": "https://github.com/anchen1011/mindex",
                        "default_branch": "main",
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            (root / "feature.txt").write_text("automated PR publication\n", encoding="utf-8")
            self._run(["git", "checkout", "-b", "mindex/feature-scope"], cwd=root)
            self._run(["git", "add", "feature.txt"], cwd=root)
            self._run(["git", "commit", "-m", "Add GitHub publication helpers"], cwd=root)

            (root / "README.md").write_text("# repo\n\nUpdated workflow docs.\n", encoding="utf-8")
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["FAKE_GH_STATE"] = str(state_path)

            result = publish_pull_request(
                project_root=root,
                commit_message="Document cumulative PR scope handling",
                title="Document cumulative PR scope handling",
                body="Make sure the PR description reflects the entire feature branch.",
                env=env,
            )

            self.assertEqual(result.branch_name, "mindex/feature-scope")
            self.assertEqual(result.base_branch, "main")
            self.assertEqual(result.push_remote, "origin")
            self.assertFalse(result.used_fork)
            self.assertTrue(result.commit_created)
            self.assertEqual(result.pr_number, 1)
            self.assertEqual(result.pr_title, "Feature Scope")
            self.assertEqual(result.pr_url, "https://github.com/anchen1011/mindex/pull/1")
            self.assertEqual(result.pr_state, "OPEN")
            self.assertTrue(result.log_dir.is_dir())

            current_branch = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root).stdout.strip()
            self.assertEqual(current_branch, result.branch_name)
            remote_branches = self._run(["git", "--git-dir", str(remote_root), "branch", "--list"], cwd=root).stdout
            self.assertIn(result.branch_name, remote_branches)

            status_payload = json.loads((result.log_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status_payload["status"], "success")
            self.assertEqual(status_payload["pr_title"], result.pr_title)
            self.assertEqual(status_payload["pr_url"], result.pr_url)

            gh_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(gh_state["pr"]["payload"]["headRefName"], result.branch_name)
            self.assertEqual(gh_state["pr"]["payload"]["title"], "Feature Scope")
            self.assertIn("Add GitHub publication helpers", gh_state["pr"]["payload"]["body"])
            self.assertIn("Document cumulative PR scope handling", gh_state["pr"]["payload"]["body"])
            self.assertIn("`README.md`", gh_state["pr"]["payload"]["body"])
            self.assertIn("`feature.txt`", gh_state["pr"]["payload"]["body"])
            self.assertIn("Make sure the PR description reflects the entire feature branch.", gh_state["pr"]["payload"]["body"])

    def test_publish_pull_request_isolates_multi_agent_work_on_a_distinct_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            remote_root = Path(tmpdir) / "remote.git"
            bin_dir = Path(tmpdir) / "bin"
            state_path = Path(tmpdir) / "fake-gh-state.json"
            root.mkdir()
            bin_dir.mkdir()
            self._init_repo(root, remote_root)

            fake_gh = bin_dir / "gh"
            self._write_fake_gh(fake_gh)
            state_path.write_text(
                json.dumps(
                    {
                        "viewer_login": "anchen1011",
                        "repo_name_with_owner": "anchen1011/mindex",
                        "repo_owner": "anchen1011",
                        "repo_url": "https://github.com/anchen1011/mindex",
                        "default_branch": "main",
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            self._run(["git", "checkout", "-b", "mindex/existing-feature"], cwd=root)
            (root / "feature.txt").write_text("parallel work\n", encoding="utf-8")

            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["FAKE_GH_STATE"] = str(state_path)
            env["MINDEX_MULTI_AGENT"] = "1"
            env["MINDEX_AGENT_ID"] = "agent-42"
            env["MINDEX_AGENT_NAME"] = "Docs Agent"
            env["MINDEX_AGENT_GOAL"] = "Document parallel publication"

            result = publish_pull_request(
                project_root=root,
                commit_message="Document parallel publication",
                title="Document parallel publication",
                body="Keep this agent's branch separate from the existing feature branch.",
                env=env,
            )

            self.assertNotEqual(result.branch_name, "mindex/existing-feature")
            self.assertTrue(result.branch_name.startswith("mindex/document-parallel-publication"))
            self.assertIn("docs-agent", result.branch_name)
            self.assertEqual(result.pr_url, "https://github.com/anchen1011/mindex/pull/1")

            registry = json.loads((root / ".mindex" / "agent-branches.json").read_text(encoding="utf-8"))
            branch_metadata = registry["branches"][result.branch_name]
            self.assertEqual(branch_metadata["agent_id"], "agent-42")
            self.assertEqual(branch_metadata["agent_name"], "Docs Agent")
            self.assertEqual(branch_metadata["goal"], "Document parallel publication")

            gh_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(gh_state["pr"]["payload"]["headRefName"], result.branch_name)

    def test_ensure_feature_branch_reuses_same_agent_branch_but_not_other_feature_branches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            remote_root = Path(tmpdir) / "remote.git"
            root.mkdir()
            self._init_repo(root, remote_root)

            self._run(["git", "checkout", "-b", "mindex/existing-feature"], cwd=root)
            env = {
                "MINDEX_MULTI_AGENT": "1",
                "MINDEX_AGENT_ID": "agent-11",
                "MINDEX_AGENT_NAME": "Review Agent",
                "MINDEX_AGENT_GOAL": "Review release notes",
            }

            first_branch = ensure_feature_branch(root, summary="review-release-notes", env=env)
            self.assertNotEqual(first_branch, "mindex/existing-feature")
            self.assertIn("review-agent", first_branch or "")

            second_branch = ensure_feature_branch(root, summary="review-release-notes", env=env)
            self.assertEqual(second_branch, first_branch)


if __name__ == "__main__":
    unittest.main()
