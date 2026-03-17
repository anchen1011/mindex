from pathlib import Path
import tempfile
import unittest
from unittest import mock

from mindex.configure import (
    MANAGED_PROFILE_END,
    MANAGED_PROFILE_START,
    configure_project,
    dependency_plan,
    update_codex_config,
)


class ConfigureTests(unittest.TestCase):
    def test_update_codex_config_writes_managed_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'config.toml'
            instructions = Path(tmp) / 'instructions.md'
            instructions.write_text('hello', encoding='utf-8')
            update_codex_config(config_path, instructions)
            content = config_path.read_text(encoding='utf-8')
            self.assertIn(MANAGED_PROFILE_START, content)
            self.assertIn(MANAGED_PROFILE_END, content)
            self.assertIn(str(instructions), content)

    def test_dependency_plan_contains_required_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = [item['name'] for item in dependency_plan(Path(tmp))]
            self.assertEqual(names, ['miniconda', 'npm', 'tmux', 'codex'])

    def test_configure_project_dry_run_creates_log_and_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / 'HISTORY.md').write_text('history', encoding='utf-8')
            (project_root / 'README.md').write_text('readme', encoding='utf-8')
            codex_home = project_root / '.codex-home'
            with mock.patch('mindex.configure.run_codex_bootstrap', return_value={'status': 'dry-run'}):
                report = configure_project(project_root_value=project_root, codex_home_value=codex_home, dry_run=True)
            self.assertEqual(report['codex_result']['status'], 'dry-run')
            self.assertTrue((project_root / '.mindex' / 'codex_instructions.md').exists())
            self.assertTrue((codex_home / 'skills' / 'configure' / 'SKILL.md').exists())
            self.assertTrue((codex_home / 'config.toml').exists())
            self.assertTrue((project_root / 'logs').exists())


if __name__ == '__main__':
    unittest.main()
