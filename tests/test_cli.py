from pathlib import Path
import tempfile
import unittest
from unittest import mock

from mindex.cli import build_codex_command, run_logged_codex


class CliTests(unittest.TestCase):
    def test_build_codex_command_uses_instructions_file_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_root = root / '.mindex'
            state_root.mkdir()
            (state_root / 'codex_instructions.md').write_text('instructions', encoding='utf-8')
            command = build_codex_command(root, ['hello'])
            self.assertEqual(command[0], 'codex')
            self.assertIn('-c', command)
            self.assertIn('hello', command)

    def test_run_logged_codex_records_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'HISTORY.md').write_text('history', encoding='utf-8')
            with mock.patch('mindex.cli.shutil.which', return_value=None), mock.patch('mindex.cli.subprocess.run') as run:
                run.return_value.returncode = 0
                result = run_logged_codex(root, ['hello'])
            self.assertEqual(result, 0)
            status_files = list((root / 'logs').rglob('status.json'))
            self.assertTrue(status_files)


if __name__ == '__main__':
    unittest.main()
