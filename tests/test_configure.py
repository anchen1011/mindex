from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from mindex.codex_home import default_managed_logs_root
from mindex.configure import configure_project


class ConfigureTests(unittest.TestCase):
    def _create_repo(self, root: Path) -> None:
        (root / "README.md").write_text("# repo\n", encoding="utf-8")
        (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")

    def test_configure_dry_run_records_plan_without_writing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            self._create_repo(root)
            codex_home = Path(tmpdir) / "codex-home"
            logs_root = root / "logs"

            result = configure_project(project_root=root, codex_home=codex_home, logs_root=logs_root, dry_run=True)

            self.assertTrue(result.log_dir.is_dir())
            self.assertFalse(result.instructions_path.exists())
            self.assertFalse(codex_home.exists())
            self.assertFalse((codex_home / "skills" / "configure").exists())
            self.assertFalse(result.codex_config_path.exists())
            plan = json.loads((result.log_dir / "configure_plan.json").read_text(encoding="utf-8"))
            self.assertTrue(plan["dry_run"])
            self.assertIn("configure", plan["packaged_skills"])
            self.assertIn("multi-agent", plan["packaged_skills"])
            self.assertIn("repo", plan["packaged_skills"])
            self.assertTrue(any("rtk-ai/rtk" in command for command in plan["dependency_commands"]))
            self.assertIn("rtk --version", plan["dependency_commands"])

    def test_configure_defaults_to_global_managed_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            self._create_repo(root)

            result = configure_project(project_root=root, dry_run=True)

            self.assertEqual(result.codex_home, (Path.home() / ".mindex" / "codex-home").resolve())
            self.assertEqual(result.logs_root, default_managed_logs_root())

    def test_configure_defaults_project_root_to_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "workspace"
            root.mkdir()
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                result = configure_project(dry_run=True)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(result.project_root, root.resolve())

    def test_configure_writes_instructions_skills_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            self._create_repo(root)
            codex_home = Path(tmpdir) / "codex-home"

            result = configure_project(project_root=root, codex_home=codex_home, dry_run=False)

            instructions_text = result.instructions_path.read_text(encoding="utf-8")
            config_text = result.codex_config_path.read_text(encoding="utf-8")
            self.assertIn("Mindex Codex Instructions", instructions_text)
            self.assertIn("Mindex is a managed Codex wrapper", instructions_text)
            self.assertIn("any future task or repository work launched through `mindex`", instructions_text)
            self.assertIn("Installing Mindex through `pip install` prepares `mindex`", instructions_text)
            self.assertIn("Mindex keeps its managed Codex home under `~/.mindex/codex-home`", instructions_text)
            self.assertIn("Mindex configures the managed Codex home to load RTK", instructions_text)
            self.assertIn("Plain `codex` remains", instructions_text)
            self.assertIn("plain vanilla Codex command", instructions_text)
            self.assertIn("Load Mindex-managed skills from `~/.mindex/codex-home/skills`", instructions_text)
            self.assertIn("If a user asks Codex to configure Mindex", instructions_text)
            self.assertIn("Use one branch per feature and one PR per feature.", instructions_text)
            self.assertIn("assign each agent", instructions_text)
            self.assertIn("Treat that branch and PR isolation as the default", instructions_text)
            self.assertIn("Never push directly to `main`, `master`, `production`", instructions_text)
            self.assertIn("multiple agents or parallel efforts pursue different goals", instructions_text)
            self.assertIn("fork it to the user's own GitHub account whenever possible", instructions_text)
            self.assertIn("does not explicitly mention repo workflow, Git, GitHub, branches, or PRs", instructions_text)
            self.assertEqual(result.instructions_path, codex_home / "mindex_instructions.md")
            self.assertTrue((codex_home / "skills" / "configure" / "SKILL.md").exists())
            self.assertTrue((codex_home / "skills" / "multi-agent" / "SKILL.md").exists())
            self.assertTrue((codex_home / "skills" / "repo" / "SKILL.md").exists())
            self.assertTrue((codex_home / "skills" / "configure").is_symlink())
            self.assertTrue((codex_home / "skills" / "repo").is_symlink())
            self.assertIn("[profiles.mindex]", config_text)
            self.assertIn('approval_policy = "never"', config_text)
            self.assertIn('sandbox_mode = "danger-full-access"', config_text)
            self.assertIn(f'CODEX_HOME = "{codex_home.as_posix()}"', config_text)
            self.assertIn("MINDEX_INSTRUCTIONS_FILE", config_text)

    def test_configure_initializes_rtk_for_managed_codex_home_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            self._create_repo(root)
            codex_home = Path(tmpdir) / "codex-home"
            fake_bin_dir = Path(tmpdir) / "bin"
            fake_bin_dir.mkdir()
            fake_rtk = fake_bin_dir / "rtk"
            fake_rtk.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                "if sys.argv[1:] != ['init', '--codex']:\n"
                "    raise SystemExit(1)\n"
                "cwd = Path.cwd()\n"
                "(cwd / 'AGENTS.md').write_text('@RTK.md\\n', encoding='utf-8')\n"
                "(cwd / 'RTK.md').write_text('# fake rtk\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fake_rtk.chmod(0o755)

            with mock.patch.dict(os.environ, {"PATH": f"{fake_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
                result = configure_project(project_root=root, codex_home=codex_home, dry_run=False)

            self.assertTrue((codex_home / "AGENTS.md").exists())
            self.assertTrue((codex_home / "RTK.md").exists())
            plan = json.loads((result.log_dir / "configure_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["rtk_status"], "configured")
            self.assertEqual(plan["rtk_init_command"], f"{fake_rtk} init --codex")

    def test_module_cli_supports_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            self._create_repo(root)
            codex_home = Path(tmpdir) / "codex-home"
            logs_root = root / "logs"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mindex.configure",
                    "configure",
                    "--project-root",
                    str(root),
                    "--codex-home",
                    str(codex_home),
                    "--logs-root",
                    str(logs_root),
                    "--dry-run",
                ],
                cwd=str(root),
                env={
                    **dict(os.environ),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["project_root"], str(root.resolve()))
            self.assertIn("Mindex configure (dry-run)", completed.stderr)
            self.assertIn(f"CODEX_HOME: {codex_home.resolve()}", completed.stderr)
            self.assertIn(f"Instructions file: {codex_home.resolve() / 'mindex_instructions.md'}", completed.stderr)
            self.assertIn(f"Logs root: {logs_root.resolve()}", completed.stderr)

    def test_module_cli_defaults_project_root_to_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "workspace"
            root.mkdir()
            codex_home = Path(tmpdir) / "codex-home"
            logs_root = Path(tmpdir) / "logs"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mindex.configure",
                    "configure",
                    "--codex-home",
                    str(codex_home),
                    "--logs-root",
                    str(logs_root),
                    "--dry-run",
                ],
                cwd=str(root),
                env={
                    **dict(os.environ),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["project_root"], str(root.resolve()))
            self.assertIn("Mindex configure (dry-run)", completed.stderr)
            self.assertIn(f"CODEX_HOME: {codex_home.resolve()}", completed.stderr)
            self.assertIn(f"Instructions file: {codex_home.resolve() / 'mindex_instructions.md'}", completed.stderr)
            self.assertIn(f"Logs root: {logs_root.resolve()}", completed.stderr)


if __name__ == "__main__":
    unittest.main()
