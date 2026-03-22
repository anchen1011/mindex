from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from mindex.ui_setup import main as ui_setup_main


class UiSetupTests(unittest.TestCase):
    def test_ui_setup_creates_config_and_prints_next_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "README.md").write_text("# repo\n", encoding="utf-8")
            (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            original_cwd = Path.cwd()
            try:
                import os

                os.chdir(root)
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = ui_setup_main([])
            finally:
                os.chdir(original_cwd)

            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["project_root"], str(root.resolve()))
            self.assertTrue((root / ".mindex" / "ui_config.json").exists())
            self.assertIn("Mindex UI config ready.", stderr.getvalue())
            self.assertIn("mindex ui serve", stderr.getvalue())

    def test_ui_setup_rejects_positional_args(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = ui_setup_main(["unexpected"])
        self.assertEqual(rc, 2)
        self.assertIn("does not accept positional arguments", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

