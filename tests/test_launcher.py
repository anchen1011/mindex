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
                "    json.dump({'cwd': os.getcwd(), 'args': sys.argv[1:]}, handle)\n",
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


if __name__ == "__main__":
    unittest.main()
