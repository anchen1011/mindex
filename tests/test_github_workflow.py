from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from mindex.github_workflow import publish_pull_request


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
            "    branch = head.split(':', 1)[-1]\n"
            "    url = state['repo_url'] + '/pull/1'\n"
            "    payload = {\n"
            "        'number': 1,\n"
            "        'url': url,\n"
            "        'state': 'OPEN',\n"
            "        'title': title,\n"
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
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["FAKE_GH_STATE"] = str(state_path)

            result = publish_pull_request(
                project_root=root,
                commit_message="Automate GitHub publication",
                title="Automate GitHub publication",
                body="Adds automated branch, push, and PR verification.",
                env=env,
            )

            self.assertTrue(result.branch_name.startswith("mindex/automate-github-publication"))
            self.assertEqual(result.base_branch, "main")
            self.assertEqual(result.push_remote, "origin")
            self.assertFalse(result.used_fork)
            self.assertTrue(result.commit_created)
            self.assertEqual(result.pr_number, 1)
            self.assertEqual(result.pr_url, "https://github.com/anchen1011/mindex/pull/1")
            self.assertEqual(result.pr_state, "OPEN")
            self.assertTrue(result.log_dir.is_dir())

            current_branch = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root).stdout.strip()
            self.assertEqual(current_branch, result.branch_name)
            remote_branches = self._run(["git", "--git-dir", str(remote_root), "branch", "--list"], cwd=root).stdout
            self.assertIn(result.branch_name, remote_branches)

            status_payload = json.loads((result.log_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status_payload["status"], "success")
            self.assertEqual(status_payload["pr_url"], result.pr_url)

            gh_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(gh_state["pr"]["payload"]["headRefName"], result.branch_name)


if __name__ == "__main__":
    unittest.main()
