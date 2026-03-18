from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from mindex.logging_utils import append_action, create_log_run, record_validation, write_status


class LoggingUtilsTests(unittest.TestCase):
    def test_create_log_run_uses_session_timestamp_and_prompt_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_run = create_log_run(Path(tmpdir), "Configure Task", prompt_text="hello", metadata={"task": "configure"})
            self.assertTrue(log_run.run_dir.is_dir())
            self.assertEqual(log_run.prompt_path.read_text(encoding="utf-8"), "hello")
            append_action(log_run, "step one")
            write_status(log_run, "success", ok=True)
            record_validation(log_run, command=["python3", "-m", "unittest"], returncode=0, passed=True)

            metadata = json.loads(log_run.metadata_path.read_text(encoding="utf-8"))
            status = json.loads(log_run.status_path.read_text(encoding="utf-8"))
            validation = json.loads(log_run.validation_path.read_text(encoding="utf-8"))

            self.assertEqual(metadata["task"], "configure")
            self.assertEqual(status["status"], "success")
            self.assertTrue(validation["passed"])
            self.assertIn("step one", log_run.actions_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
