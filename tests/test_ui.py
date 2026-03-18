from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from mindex.cli import main as cli_main
from mindex.task_queue import AgentManager, TaskQueueManager
from mindex.ui import MindexUiApp, load_or_create_ui_config, reset_ui_config


class UiTests(unittest.TestCase):
    def _create_repo(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "README.md").write_text("# repo\n", encoding="utf-8")
        (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")

    def test_load_or_create_ui_config_hashes_password_and_migrates_legacy_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            config_path = root / ".mindex" / "ui_config.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "project_root": str(root),
                        "auth": {"username": "admin", "password": "legacy-secret"},
                        "server": {"host": "0.0.0.0", "port": 8123},
                        "storage": {
                            "state_file": str(root / ".mindex" / "task_queues.json"),
                            "queue_log_dir": str(root / ".mindex" / "queue_logs"),
                        },
                        "ui": {"title": "Legacy UI"},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            bootstrap = load_or_create_ui_config(project_root=root, config_path=config_path)

            self.assertTrue(bootstrap.migrated_legacy_config)
            self.assertEqual(bootstrap.config.host, "127.0.0.1")
            written = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("password", written["auth"])
            self.assertIn("password_hash", written["auth"])
            self.assertIn("session_secret", written["auth"])
            self.assertFalse(bootstrap.generated_password)

    def test_reset_ui_config_rewrites_existing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            config_path = root / ".mindex" / "ui_config.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "project_root": str(root),
                        "auth": {
                            "username": "operator",
                            "password_hash": "old-hash",
                            "password_salt": "deadbeef",
                            "password_iterations": 1000,
                            "session_secret": "legacy-session",
                            "session_ttl_seconds": 60,
                            "login_attempts": 1,
                            "login_window_seconds": 30,
                        },
                        "server": {
                            "host": "0.0.0.0",
                            "port": 9999,
                            "allow_remote": True,
                            "allowed_origins": ["https://example.com"],
                        },
                        "storage": {
                            "state_file": str(root / ".mindex" / "custom_state.json"),
                            "queue_log_dir": str(root / ".mindex" / "custom_logs"),
                        },
                        "ui": {"title": "Legacy UI"},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            bootstrap = reset_ui_config(project_root=root, password="new-secret")

            self.assertFalse(bootstrap.generated_password)
            self.assertEqual(bootstrap.config.username, "admin")
            self.assertEqual(bootstrap.config.host, "127.0.0.1")
            self.assertEqual(bootstrap.config.port, 8765)
            self.assertEqual(bootstrap.config.title, "Mindex Control Deck")
            self.assertFalse(bootstrap.config.allow_remote)
            self.assertEqual(bootstrap.config.state_file, root / ".mindex" / "task_queues.json")
            self.assertEqual(bootstrap.config.queue_log_dir, root / ".mindex" / "queue_logs")
            app = MindexUiApp(bootstrap.config)
            self.assertTrue(app.verify_password("new-secret"))
            written = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(written["auth"]["username"], "admin")
            self.assertNotEqual(written["auth"]["session_secret"], "legacy-session")
            self.assertEqual(written["server"]["host"], "127.0.0.1")
            self.assertEqual(written["storage"]["state_file"], str(root / ".mindex" / "task_queues.json"))

    def test_agent_manager_runs_python_module_agents_without_shelling_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            manager = AgentManager(
                project_root=root,
                state_file=root / ".mindex" / "task_queues.json",
                queue_log_dir=root / ".mindex" / "queue_logs",
            )
            agent = manager.create_agent(
                name="Version check",
                description="Smoke-test the packaged CLI",
                command_args=["--version"],
                workdir=root,
                auto_publish=False,
            )

            started = manager.start_agent(agent.agent_id)
            completed = manager.wait_for_agent(started.agent_id, timeout=10)

            self.assertEqual(completed.status, "completed")
            self.assertEqual(completed.returncode, 0)
            self.assertTrue(Path(completed.log_path or "").exists())
            log_text = Path(completed.log_path or "").read_text(encoding="utf-8")
            self.assertIn("starting --version", log_text)

    def test_task_queue_manager_supports_add_edit_delete_and_reorder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            manager = TaskQueueManager(project_root=root, state_file=root / ".mindex" / "task_queues.json")

            default_queue = manager.ensure_default_queue()
            self.assertEqual(default_queue.name, "Current session queue")

            queue = manager.create_queue(name="Release queue", description="Session planning")
            first = manager.add_task(queue.queue_id, title="Draft release notes", details="Summarize shipped behavior")
            second = manager.add_task(queue.queue_id, title="Cut release branch", status="blocked")

            updated = manager.update_task(queue.queue_id, second.task_id, details="Wait for final validation", status="in_progress")
            self.assertEqual(updated.status, "in_progress")

            reordered = manager.reorder_tasks(queue.queue_id, [second.task_id, first.task_id])
            self.assertEqual([task.task_id for task in reordered.tasks], [second.task_id, first.task_id])

            manager.delete_task(queue.queue_id, first.task_id)
            queue_state = [item for item in manager.list_queues() if item.queue_id == queue.queue_id][0]
            self.assertEqual([task.task_id for task in queue_state.tasks], [second.task_id])

    def test_ui_app_creates_sessions_and_reports_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            bootstrap = load_or_create_ui_config(
                project_root=root,
                password="correct horse battery staple",
            )
            app = MindexUiApp(bootstrap.config)

            self.assertTrue(app.verify_password("correct horse battery staple"))
            self.assertFalse(app.verify_password("wrong password"))

            session = app.create_session()
            self.assertIsNotNone(app.sessions.get(session.token))
            self.assertTrue(session.csrf_token)

            agent = app.agent_manager.create_agent(
                name="API agent",
                description="Created from the in-process UI app",
                command_args=["--version"],
                workdir=root,
                auto_publish=False,
            )
            app.agent_manager.start_agent(agent.agent_id)
            completed = app.agent_manager.wait_for_agent(agent.agent_id, timeout=10)
            self.assertEqual(completed.status, "completed")

            payload = app.system_status()
            self.assertEqual(payload["agent_count"], 1)
            self.assertEqual(payload["running_count"], 0)
            self.assertTrue(payload["security"]["csrf_protected"])
            self.assertGreaterEqual(len(payload["queues"]), 1)

    def test_cli_routes_ui_init_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            stdout_buffer = io.StringIO()

            with redirect_stdout(stdout_buffer):
                result = cli_main(
                    [
                        "ui",
                        "init-config",
                        "--project-root",
                        str(root),
                        "--password",
                        "deck-secret",
                    ]
                )

            self.assertEqual(result, 0)
            payload = json.loads(stdout_buffer.getvalue())
            self.assertEqual(payload["project_root"], str(root.resolve()))
            self.assertEqual(payload["username"], "admin")

    def test_cli_routes_ui_reset_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            load_or_create_ui_config(
                project_root=root,
                username="operator",
                password="old-secret",
                host="localhost",
                port=9000,
                title="Old Deck",
            )
            stdout_buffer = io.StringIO()

            with redirect_stdout(stdout_buffer):
                result = cli_main(
                    [
                        "ui",
                        "reset-config",
                        "--project-root",
                        str(root),
                        "--password",
                        "deck-secret",
                    ]
                )

            self.assertEqual(result, 0)
            payload = json.loads(stdout_buffer.getvalue())
            self.assertEqual(payload["project_root"], str(root.resolve()))
            self.assertEqual(payload["username"], "admin")
            self.assertEqual(payload["host"], "127.0.0.1")
            self.assertEqual(payload["port"], 8765)
            app = MindexUiApp(load_or_create_ui_config(project_root=root).config)
            self.assertTrue(app.verify_password("deck-secret"))


if __name__ == "__main__":
    unittest.main()
