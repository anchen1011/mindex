from __future__ import annotations

from contextlib import redirect_stdout
import json
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from mindex.task_queue import QueueStore, QueueStoreError, ensure_ui_config
from mindex.ui import AuthSessionStore, UIRequestHandler, main as ui_main


class QueueStoreTests(unittest.TestCase):
    def _create_repo(self, root: Path) -> None:
        (root / "README.md").write_text("# repo\n", encoding="utf-8")
        (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")

    def test_queue_store_persists_ordered_sessions_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            self._create_repo(root)
            _, config = ensure_ui_config(root)
            store = QueueStore.from_config(root, config)

            queue = store.create_queue(name="Release prep", description="Wrap up docs and code paths")
            queue_id = queue["id"]

            queue = store.add_task(queue_id, title="Update release notes", instructions="Add the missing bullet points.")
            first_task_id = queue["tasks"][0]["id"]
            queue = store.add_task(queue_id, title="Cut the tag", instructions="Only after the docs are reviewed.")
            second_task_id = queue["tasks"][1]["id"]

            queue = store.reorder_tasks(queue_id, [second_task_id, first_task_id])
            self.assertEqual(queue["current_task_id"], second_task_id)

            with self.assertRaises(QueueStoreError):
                store.set_task_completion(queue_id, first_task_id, completed=True)

            queue = store.set_task_completion(queue_id, second_task_id, completed=True)
            self.assertEqual(queue["completed_count"], 1)
            self.assertEqual(queue["current_task_id"], first_task_id)

            queue = store.set_task_completion(queue_id, first_task_id, completed=True)
            self.assertEqual(queue["status"], "completed")
            self.assertEqual(queue["completed_count"], 2)
            self.assertIsNotNone(queue["completed_at"])

            persisted = store.snapshot()["queues"][0]
            self.assertEqual(persisted["status"], "completed")
            self.assertEqual(len(persisted["events"]), 7)

            log_path = root / ".mindex" / "queue_logs" / f"{queue_id}.jsonl"
            self.assertTrue(log_path.exists())
            event_types = [json.loads(line)["event_type"] for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertIn("queue.completed", event_types)

    def test_ui_init_only_creates_default_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            self._create_repo(root)

            output = StringIO()
            with redirect_stdout(output):
                returncode = ui_main(["--project-root", str(root), "--init-only"])

            self.assertEqual(returncode, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["server"]["host"], "0.0.0.0")
            self.assertEqual(payload["server"]["port"], 8000)
            self.assertTrue((root / ".mindex" / "ui_config.json").exists())


class UIHelpersTests(unittest.TestCase):
    def _create_repo(self, root: Path) -> None:
        (root / "README.md").write_text("# repo\n", encoding="utf-8")
        (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")

    def test_auth_session_store_tracks_tokens(self) -> None:
        sessions = AuthSessionStore()
        token = sessions.create()
        self.assertTrue(sessions.contains(token))
        sessions.delete(token)
        self.assertFalse(sessions.contains(token))

    def test_ui_handler_builds_state_and_html_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            self._create_repo(root)
            config_path, config = ensure_ui_config(root)
            queue_store = QueueStore.from_config(root, config)
            queue = queue_store.create_queue(name="Session One", description="Drive the release checklist")
            queue = queue_store.add_task(queue["id"], title="Review docs", instructions="Update the README before shipping.")

            fake_server = type(
                "FakeServer",
                (),
                {
                    "config": config,
                    "config_path": config_path,
                    "project_root": root,
                    "queue_store": queue_store,
                    "sessions": AuthSessionStore(),
                },
            )()
            handler = UIRequestHandler.__new__(UIRequestHandler)
            handler.server = fake_server

            login_html = handler._login_page("No entry.")
            app_html = handler._app_page()
            state_payload = handler._state_payload()

            self.assertIn("No entry.", login_html)
            self.assertIn("MindX Session Director", app_html)
            self.assertIn(str(config_path), app_html)
            self.assertNotIn("{{", app_html)
            self.assertEqual(state_payload["queues"][0]["name"], "Session One")
            self.assertEqual(state_payload["queues"][0]["tasks"][0]["title"], "Review docs")
            self.assertEqual(handler._path_segments("/api/queues/test/tasks"), ["api", "queues", "test", "tasks"])


if __name__ == "__main__":
    unittest.main()
