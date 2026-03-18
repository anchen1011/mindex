from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess

from mindex.launcher import find_project_root, launch_codex


class LauncherTests(unittest.TestCase):
    def _init_git_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(root), check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(["git", "add", "README.md", "HISTORY.md"], cwd=str(root), check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(root), check=True, capture_output=True, text=True)

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
            "    print(json.dumps({\n"
            "        'nameWithOwner': state['repo_name_with_owner'],\n"
            "        'isFork': False,\n"
            "        'url': state['repo_url'],\n"
            "        'defaultBranchRef': {'name': state['default_branch']},\n"
            "        'parent': None,\n"
            "        'owner': {'login': state['repo_owner']},\n"
            "    }))\n"
            "elif args[:2] == ['api', 'user']:\n"
            "    print(json.dumps({'login': state['viewer_login']}))\n"
            "elif len(args) >= 5 and args[0] == 'api' and args[1].startswith('repos/') and args[2] == '--method' and args[3] == 'PATCH':\n"
            "    pr = state['pr']\n"
            "    fields = [value for index, value in enumerate(args) if args[index - 1:index] == ['-f']]\n"
            "    for field in fields:\n"
            "        key, value = field.split('=', 1)\n"
            "        pr['payload'][key] = value\n"
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
            "    state['pr'] = {\n"
            "        'payload': {\n"
            "            'number': 1,\n"
            "            'url': url,\n"
            "            'state': 'OPEN',\n"
            "            'title': title,\n"
            "            'body': body,\n"
            "            'headRefName': branch,\n"
            "            'baseRefName': base,\n"
            "        },\n"
            "        'selectors': [head, branch],\n"
            "    }\n"
            "    write_state()\n"
            "    print(url)\n"
            "elif args[:2] == ['pr', 'view']:\n"
            "    print(json.dumps(state['pr']['payload']))\n"
            "else:\n"
            "    print('unsupported gh command: ' + ' '.join(args), file=sys.stderr)\n"
            "    sys.exit(1)\n",
            encoding="utf-8",
        )
        script_path.chmod(0o755)

    def test_find_project_root_walks_up_from_nested_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            (root / "README.md").write_text("# repo\n", encoding="utf-8")
            (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")

            self.assertEqual(find_project_root(nested), root.resolve())

    def test_launch_codex_proxies_from_repo_root_and_logs_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            nested = root / "nested"
            fake_bin_dir = Path(tmpdir) / "bin"
            logs_root = root / "logs"
            nested.mkdir(parents=True)
            fake_bin_dir.mkdir()
            (root / "README.md").write_text("# repo\n", encoding="utf-8")
            (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")

            fake_codex = fake_bin_dir / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "path = os.environ['MINDEX_FAKE_OUTPUT']\n"
                "with open(path, 'w', encoding='utf-8') as handle:\n"
                "    json.dump({'cwd': os.getcwd(), 'args': sys.argv[1:], 'codex_home': os.environ.get('CODEX_HOME')}, handle)\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            output_path = Path(tmpdir) / "codex-output.json"

            env = {
                "MINDEX_CODEX_BIN": str(fake_codex),
                "MINDEX_DISABLE_SCRIPT": "1",
                "MINDEX_FAKE_OUTPUT": str(output_path),
            }
            original_cwd = Path.cwd()
            try:
                os.chdir(nested)
                returncode = launch_codex(["status", "--json"], project_root=find_project_root(), logs_root=logs_root, env=env)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(returncode, 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["cwd"], str(root.resolve()))
            self.assertEqual(payload["args"], ["status", "--json"])
            self.assertEqual(payload["codex_home"], str((root / ".mindex" / "codex-home").resolve()))

            status_files = list(logs_root.glob("**/status.json"))
            self.assertEqual(len(status_files), 1)
            status = json.loads(status_files[0].read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "success")
            self.assertEqual(status["returncode"], 0)

    def test_launch_codex_creates_feature_branch_before_running_from_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            fake_bin_dir = Path(tmpdir) / "bin"
            logs_root = root / "logs"
            root.mkdir()
            fake_bin_dir.mkdir()
            (root / "README.md").write_text("# repo\n", encoding="utf-8")
            (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")
            self._init_git_repo(root)

            fake_codex = fake_bin_dir / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, subprocess, sys\n"
                "path = os.environ['MINDEX_FAKE_OUTPUT']\n"
                "branch = subprocess.run(\n"
                "    ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],\n"
                "    check=True,\n"
                "    capture_output=True,\n"
                "    text=True,\n"
                ").stdout.strip()\n"
                "with open(path, 'w', encoding='utf-8') as handle:\n"
                "    json.dump({'branch': branch, 'args': sys.argv[1:]}, handle)\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            output_path = Path(tmpdir) / "codex-output.json"
            feature_branch = "mindex/launcher-session"

            returncode = launch_codex(
                ["status"],
                project_root=root,
                logs_root=logs_root,
                env={
                    "MINDEX_CODEX_BIN": str(fake_codex),
                    "MINDEX_DISABLE_SCRIPT": "1",
                    "MINDEX_FAKE_OUTPUT": str(output_path),
                    "MINDEX_FEATURE_BRANCH": feature_branch,
                },
            )

            self.assertEqual(returncode, 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["branch"], feature_branch)
            self.assertEqual(payload["args"], ["status"])
            current_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(root),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(current_branch, feature_branch)

    def test_launch_codex_auto_publishes_session_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            remote_root = Path(tmpdir) / "remote.git"
            bin_dir = Path(tmpdir) / "bin"
            logs_root = root / "logs"
            state_path = Path(tmpdir) / "fake-gh-state.json"
            root.mkdir()
            bin_dir.mkdir()
            (root / "README.md").write_text("# repo\n", encoding="utf-8")
            (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")
            self._init_git_repo(root)
            subprocess.run(["git", "init", "--bare", str(remote_root)], cwd=str(root.parent), check=True, capture_output=True, text=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote_root)], cwd=str(root), check=True, capture_output=True, text=True)
            subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(root), check=True, capture_output=True, text=True)

            fake_codex = bin_dir / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "Path('session-note.txt').write_text('launched via codex\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)

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

            returncode = launch_codex(
                [],
                project_root=root,
                logs_root=logs_root,
                env={
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "FAKE_GH_STATE": str(state_path),
                    "MINDEX_CODEX_BIN": str(fake_codex),
                    "MINDEX_DISABLE_SCRIPT": "1",
                },
            )

            self.assertEqual(returncode, 0)
            current_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(root),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(current_branch, "mindex/codex-session")

            status_files = list(logs_root.glob("**/status.json"))
            self.assertTrue(status_files)
            latest_status = json.loads(sorted(status_files)[-1].read_text(encoding="utf-8"))
            self.assertEqual(latest_status["published_pr_url"], "https://github.com/anchen1011/mindex/pull/1")
            self.assertEqual(latest_status["published_branch"], "mindex/codex-session")

            gh_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(gh_state["pr"]["payload"]["headRefName"], "mindex/codex-session")
            self.assertIn("Automatic Session Publication", gh_state["pr"]["payload"]["body"])
            self.assertIn("`session-note.txt`", gh_state["pr"]["payload"]["body"])


if __name__ == "__main__":
    unittest.main()
