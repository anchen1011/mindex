from __future__ import annotations

from contextlib import redirect_stdout
import http.cookiejar
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request

from mindex.cli import main as cli_main
from mindex.task_queue import AgentManager, TaskQueueManager
import mindex.ui as ui_module
from mindex.ui import APP_JS, MindexUiApp, create_ui_server, load_or_create_ui_config


class UiTests(unittest.TestCase):
    def _create_repo(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "README.md").write_text("# repo\n", encoding="utf-8")
        (root / "HISTORY.md").write_text("# history\n", encoding="utf-8")

    def _start_live_server(self, root: Path, *, disable_origin_checks: bool = False, disable_csrf_checks: bool = False):
        bootstrap = load_or_create_ui_config(
            project_root=root,
            password="deck-secret",
            port=0,
            disable_origin_checks=disable_origin_checks,
            disable_csrf_checks=disable_csrf_checks,
        )
        server = create_ui_server(bootstrap.config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def _build_client(self, server: ui_module.ConfiguredUiServer):
        host, port = server.server_address
        base_url = f"http://{host}:{port}"
        cookie_jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

        def request(path: str, *, method: str = "GET", payload: dict | None = None, headers: dict[str, str] | None = None):
            data = None if payload is None else json.dumps(payload).encode("utf-8")
            request_obj = urllib.request.Request(base_url + path, data=data, method=method)
            request_obj.add_header("Content-Type", "application/json")
            for key, value in (headers or {}).items():
                request_obj.add_header(key, value)
            try:
                with opener.open(request_obj, timeout=10) as response:
                    body = response.read().decode("utf-8")
                    return response.status, json.loads(body) if body else None
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8")
                payload_body = json.loads(body) if body else None
                return exc.code, payload_body

        return base_url, request

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

    def test_disable_origin_checks_bypasses_origin_validation(self) -> None:
        handler = ui_module.UiRequestHandler.__new__(ui_module.UiRequestHandler)
        handler.command = "POST"
        handler.headers = {"Origin": "https://public.example.net"}
        handler.app = SimpleNamespace(
            config=SimpleNamespace(
                disable_origin_checks=True,
                disable_csrf_checks=False,
                allowed_origins=("http://127.0.0.1:8765",),
            )
        )

        self.assertIsNone(handler._check_origin(require_auth=True))

    def test_disable_csrf_checks_bypasses_csrf_validation(self) -> None:
        session = ui_module.SessionRecord(
            token="session-token",
            username="admin",
            csrf_token="expected-token",
            expires_at=9999999999.0,
        )
        handler = ui_module.UiRequestHandler.__new__(ui_module.UiRequestHandler)
        handler.command = "POST"
        handler.headers = {"X-Mindex-CSRF-Token": "wrong-token"}
        handler.app = SimpleNamespace(
            config=SimpleNamespace(
                disable_origin_checks=True,
                disable_csrf_checks=True,
                allowed_origins=("http://127.0.0.1:8765",),
            )
        )
        handler._session_from_cookie = lambda: session

        self.assertIs(handler._require_session(require_csrf=True), session)

    def test_create_managed_session_defaults_command_args_from_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            bootstrap = load_or_create_ui_config(project_root=root, password="deck-secret")
            app = MindexUiApp(bootstrap.config)

            managed = app.create_managed_session(name="Triage flaky tests", workdir=root)

            self.assertEqual(managed["name"], "Triage flaky tests")
            self.assertEqual(managed["command_args"], ["exec", "Triage flaky tests"])
            self.assertEqual(managed["queue"]["name"], "Triage flaky tests")
            self.assertEqual(managed["queue"]["description"], "Queue for Triage flaky tests.")

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

            managed = app.create_managed_session(
                name="API agent",
                command_args=["--version"],
                workdir=root,
                queue_description="Handle the in-process UI session queue.",
            )
            agent_id = managed["agent_id"]
            queue_id = managed["queue"]["queue_id"]

            task = app.task_queue_manager.add_task(queue_id, title="Confirm output visibility", details="Ensure the session shows log text.")
            app.agent_manager.start_agent(agent_id)
            completed = app.agent_manager.wait_for_agent(agent_id, timeout=10)
            self.assertEqual(completed.status, "completed")

            payload = app.system_status()
            self.assertEqual(payload["session_count"], 1)
            self.assertEqual(payload["running_count"], 0)
            self.assertTrue(payload["security"]["csrf_protected"])
            self.assertEqual(len(payload["sessions"]), 1)
            session_payload = payload["sessions"][0]
            self.assertEqual(session_payload["agent_id"], agent_id)
            self.assertEqual(session_payload["queue"]["queue_id"], queue_id)
            self.assertEqual(session_payload["queue"]["tasks"][0]["task_id"], task.task_id)
            self.assertIn("starting --version", session_payload["output"])

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
            self.assertFalse(payload["disable_origin_checks"])
            self.assertFalse(payload["disable_csrf_checks"])

    def test_cli_routes_ui_init_config_with_disabled_origin_and_csrf_checks(self) -> None:
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
                        "--allow-remote",
                        "--disable-origin-checks",
                        "--disable-csrf-checks",
                    ]
                )

            self.assertEqual(result, 0)
            payload = json.loads(stdout_buffer.getvalue())
            self.assertTrue(payload["allow_remote"])
            self.assertTrue(payload["disable_origin_checks"])
            self.assertTrue(payload["disable_csrf_checks"])

    def test_submit_handlers_survive_current_target_clearing(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")

        script = f"""
const vm = require('vm');

let source = {APP_JS!r};
source = source.replace(/\\nloadDashboard\\(\\);\\s*$/, '\\n');

const errorNode = {{
  textContent: '',
  classList: {{
    add() {{}},
    remove() {{}},
  }},
}};

global.document = {{
  getElementById(id) {{
    if (id === 'session-form-error') {{
      return errorNode;
    }}
    return {{
      addEventListener() {{}},
      classList: {{ add() {{}}, remove() {{}} }},
      innerHTML: '',
      textContent: '',
    }};
  }},
  querySelectorAll() {{
    return [];
  }},
  createElement() {{
    return {{
      textContent: '',
      innerHTML: '',
    }};
  }},
}};

global.window = {{
  prompt() {{ return null; }},
  confirm() {{ return false; }},
}};

global.alert = () => {{}};
global.FormData = class {{
  constructor(form) {{
    this.form = form;
  }}
  get(key) {{
    return this.form.values[key];
  }}
}};

vm.runInThisContext(source, {{ filename: 'app.js' }});

let dashboardLoads = 0;
loadDashboard = async () => {{
  dashboardLoads += 1;
}};

const apiCalls = [];
api = async (path, options) => {{
  apiCalls.push({{ path, body: JSON.parse(options.body) }});
  if (path === '/api/sessions') {{
    sessionEvent.currentTarget = null;
  }}
  if (path === '/api/queues/queue-1/tasks') {{
    taskEvent.currentTarget = null;
  }}
  return {{}};
}};

const sessionForm = {{
  values: {{ name: 'Triage flaky tests', workdir: '/repo' }},
  elements: {{ workdir: {{ value: '/repo' }} }},
  resetCount: 0,
  reset() {{
    this.resetCount += 1;
    this.values = {{ name: '', workdir: '' }};
    this.elements.workdir.value = '';
  }},
}};

const taskForm = {{
  dataset: {{ taskForm: 'queue-1' }},
  values: {{ title: 'Check output', details: 'Look at errors', status: 'pending' }},
  resetCount: 0,
  reset() {{
    this.resetCount += 1;
  }},
}};

const sessionEvent = {{
  currentTarget: sessionForm,
  preventDefault() {{}},
}};

const taskEvent = {{
  currentTarget: taskForm,
  preventDefault() {{}},
}};

(async () => {{
  await submitSession(sessionEvent);
  await submitTask(taskEvent);
  if (sessionForm.resetCount !== 1) {{
    throw new Error(`session reset count: ${{sessionForm.resetCount}}`);
  }}
  if (sessionForm.elements.workdir.value !== '/repo') {{
    throw new Error(`session workdir value: ${{sessionForm.elements.workdir.value}}`);
  }}
  if (taskForm.resetCount !== 1) {{
    throw new Error(`task reset count: ${{taskForm.resetCount}}`);
  }}
  if (dashboardLoads !== 2) {{
    throw new Error(`dashboard loads: ${{dashboardLoads}}`);
  }}
  if (apiCalls.length !== 2) {{
    throw new Error(`api calls: ${{apiCalls.length}}`);
  }}
}})().catch(error => {{
  console.error(error.stack || String(error));
  process.exit(1);
}});
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout or "node submit-handler test failed")

    def test_live_ui_api_session_and_queue_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            server, thread = self._start_live_server(root)
            try:
                base_url, request = self._build_client(server)
                login_status, login_payload = request(
                    "/api/login",
                    method="POST",
                    payload={"username": "admin", "password": "deck-secret"},
                    headers={"Origin": base_url},
                )
                self.assertEqual(login_status, 200)
                csrf_token = login_payload["csrf_token"]
                auth_headers = {"Origin": base_url, "X-Mindex-CSRF-Token": csrf_token}

                create_status, create_payload = request(
                    "/api/sessions",
                    method="POST",
                    payload={"name": "API session", "workdir": str(root), "command_args": "--version"},
                    headers=auth_headers,
                )
                self.assertEqual(create_status, 201)
                session = create_payload["session"]
                session_id = session["agent_id"]
                queue_id = session["queue"]["queue_id"]

                first_status, first_payload = request(
                    f"/api/queues/{queue_id}/tasks",
                    method="POST",
                    payload={"title": "Check output", "details": "Look for start logs", "status": "pending"},
                    headers=auth_headers,
                )
                self.assertEqual(first_status, 201)
                first_task_id = first_payload["task"]["task_id"]

                second_status, second_payload = request(
                    f"/api/queues/{queue_id}/tasks",
                    method="POST",
                    payload={"title": "Reorder me", "details": "Promote this first", "status": "blocked"},
                    headers=auth_headers,
                )
                self.assertEqual(second_status, 201)
                second_task_id = second_payload["task"]["task_id"]

                edit_status, edit_payload = request(
                    f"/api/queues/{queue_id}/tasks/{second_task_id}",
                    method="PATCH",
                    payload={"title": "Reorder me first", "details": "Now in progress", "status": "in_progress"},
                    headers=auth_headers,
                )
                self.assertEqual(edit_status, 200)
                self.assertEqual(edit_payload["task"]["status"], "in_progress")

                reorder_status, reorder_payload = request(
                    f"/api/queues/{queue_id}/reorder",
                    method="POST",
                    payload={"ordered_task_ids": [second_task_id, first_task_id]},
                    headers=auth_headers,
                )
                self.assertEqual(reorder_status, 200)
                self.assertEqual(
                    [task["task_id"] for task in reorder_payload["queue"]["tasks"]],
                    [second_task_id, first_task_id],
                )

                start_status, start_payload = request(
                    f"/api/sessions/{session_id}/start",
                    method="POST",
                    payload={},
                    headers=auth_headers,
                )
                self.assertEqual(start_status, 200)
                self.assertEqual(start_payload["session"]["status"], "running")

                deadline = time.time() + 10
                while True:
                    status_status, status_payload = request("/api/status", headers={"Origin": base_url})
                    self.assertEqual(status_status, 200)
                    live_session = next(item for item in status_payload["sessions"] if item["agent_id"] == session_id)
                    if live_session["status"] != "running":
                        break
                    self.assertLess(time.time(), deadline, "timed out waiting for the managed session to finish")
                    time.sleep(0.1)

                self.assertEqual(live_session["status"], "completed")
                self.assertIn("starting --version", live_session["output"])
                self.assertEqual(
                    [task["task_id"] for task in live_session["queue"]["tasks"]],
                    [second_task_id, first_task_id],
                )

                delete_task_status, delete_task_payload = request(
                    f"/api/queues/{queue_id}/tasks/{first_task_id}",
                    method="DELETE",
                    headers=auth_headers,
                )
                self.assertEqual(delete_task_status, 200)
                self.assertTrue(delete_task_payload["ok"])

                delete_session_status, delete_session_payload = request(
                    f"/api/sessions/{session_id}",
                    method="DELETE",
                    headers=auth_headers,
                )
                self.assertEqual(delete_session_status, 200)
                self.assertTrue(delete_session_payload["ok"])

                final_status, final_payload = request("/api/status", headers={"Origin": base_url})
                self.assertEqual(final_status, 200)
                self.assertEqual(final_payload["sessions"], [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_live_ui_api_can_skip_origin_and_csrf_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            self._create_repo(root)
            server, thread = self._start_live_server(
                root,
                disable_origin_checks=True,
                disable_csrf_checks=True,
            )
            try:
                _, request = self._build_client(server)
                login_status, login_payload = request(
                    "/api/login",
                    method="POST",
                    payload={"username": "admin", "password": "deck-secret"},
                    headers={"Origin": "https://public.example.net"},
                )
                self.assertEqual(login_status, 200)

                csrf_token = login_payload["csrf_token"]
                create_status, create_payload = request(
                    "/api/sessions",
                    method="POST",
                    payload={"name": "Remote session", "workdir": str(root), "command_args": "--version"},
                    headers={
                        "Origin": "https://public.example.net",
                        "X-Mindex-CSRF-Token": f"wrong-{csrf_token}",
                    },
                )
                self.assertEqual(create_status, 201)
                self.assertEqual(create_payload["session"]["name"], "Remote session")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
