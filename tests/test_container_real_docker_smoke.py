from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock
import urllib.request

from mindex.container_mode import container_name_for_project, container_main


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    completed = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    return completed.returncode == 0


@unittest.skipUnless(
    os.environ.get("MINDEX_RUN_DOCKER_TESTS") == "1" and _docker_available(),
    "requires Docker + MINDEX_RUN_DOCKER_TESTS=1",
)
class RealDockerSmokeTests(unittest.TestCase):
    """
    Optional real-Docker integration tests.

    Coverage:
    - image build succeeds in a real Docker daemon
    - independent per-container Mindex/Codex state
    - shared folder visibility from inside the container
    - external web-app reachability through mapped ports
    - dynamic port allocation across multiple containers
    - block-mode retry on real host port conflicts
    - static port mappings from config
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.shared_host = self.root / "shared-host"
        self.shared_host.mkdir(parents=True, exist_ok=True)
        self.config_path = self.root / "mindex-config.json"
        self.project1 = self.root / "project1"
        self.project2 = self.root / "project2"
        for project, title in ((self.project1, "p1"), (self.project2, "p2")):
            project.mkdir(parents=True, exist_ok=True)
            (project / "README.md").write_text(f"# {title}\n", encoding="utf-8")
            (project / "HISTORY.md").write_text("# history\n", encoding="utf-8")

        self.name1 = container_name_for_project(self.project1)
        self.name2 = container_name_for_project(self.project2)
        self.addCleanup(self._cleanup_containers)

    def _cleanup_containers(self) -> None:
        for name in (self.name1, self.name2):
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, text=True)

    def _write_config(self, *, port_mapping: dict | None = None) -> None:
        payload = {
            "container": {
                "enabled_by_default": False,
                "image": {"name": "mindex-container", "tag": "latest"},
                "shared_folders": [
                    {"host": str(self.shared_host), "container": "/shared", "read_only": False},
                ],
                "port_mapping": port_mapping
                or {
                    "mode": "block",
                    "host_ip": "127.0.0.1",
                    "container_port_range_start": 3000,
                    "container_port_count": 2,
                    "extra_container_ports": [8765],
                    "host_port_base": None,
                    "host_port_range_start": 41000,
                    "host_port_range_end": 49000,
                    "static_host_ports": {},
                },
            }
        }
        self.config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update({"HOME": str(self.home), "MINDEX_CONFIG_PATH": str(self.config_path)})
        return env

    def _inspect(self, container_name: str) -> dict:
        completed = subprocess.run(
            ["docker", "container", "inspect", container_name],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)[0]

    def _host_ports(self, payload: dict) -> dict[int, int]:
        ports = payload.get("NetworkSettings", {}).get("Ports", {}) or {}
        mapping: dict[int, int] = {}
        for key, value in ports.items():
            if not value:
                continue
            mapping[int(key.split("/", 1)[0], 10)] = int(value[0]["HostPort"], 10)
        return mapping

    def _docker_exec(self, container_name: str, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "exec", "-i", container_name, "/bin/bash", "-lc", command],
            check=False,
            capture_output=True,
            text=True,
        )

    def _wait_http(self, url: str, *, timeout_seconds: float = 15.0) -> str:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    return response.read().decode("utf-8")
            except Exception:
                time.sleep(0.2)
        self.fail(f"timed out waiting for {url}")

    def test_two_projects_can_run_without_port_collisions_and_have_isolated_volumes(self) -> None:
        self._write_config()
        env = self._env()

        self.assertEqual(container_main(["ports"], project_root=self.project1, env=env), 0)
        self.assertEqual(container_main(["ports"], project_root=self.project2, env=env), 0)

        payload1 = self._inspect(self.name1)
        payload2 = self._inspect(self.name2)
        ports1 = set(self._host_ports(payload1).values())
        ports2 = set(self._host_ports(payload2).values())
        self.assertTrue(ports1)
        self.assertTrue(ports2)
        self.assertTrue(ports1.isdisjoint(ports2))

        mounts1 = payload1.get("Mounts", []) or []
        targets1 = {mount.get("Destination") for mount in mounts1 if isinstance(mount, dict)}
        types1 = {(mount.get("Destination"), mount.get("Type")) for mount in mounts1 if isinstance(mount, dict)}
        self.assertIn("/root/.mindex", targets1)
        self.assertIn("/root/.codex", targets1)
        self.assertIn(("/root/.mindex", "volume"), types1)
        self.assertIn(("/root/.codex", "volume"), types1)
        self.assertIn("/shared", targets1)

    def test_shared_folder_and_in_container_mindex_state_are_isolated_per_container(self) -> None:
        self._write_config()
        env = self._env()
        (self.shared_host / "from-host.txt").write_text("hello-from-host\n", encoding="utf-8")

        self.assertEqual(container_main(["ports"], project_root=self.project1, env=env), 0)
        self.assertEqual(container_main(["ports"], project_root=self.project2, env=env), 0)

        cat_result = self._docker_exec(self.name1, "cat /shared/from-host.txt")
        self.assertEqual(cat_result.returncode, 0, cat_result.stderr)
        self.assertEqual(cat_result.stdout, "hello-from-host\n")

        mutate_result = self._docker_exec(
            self.name1,
            r'''python - <<'PY'
import importlib
import pathlib
import mindex
path = pathlib.Path(mindex.__file__).resolve()
text = path.read_text(encoding="utf-8")
if 'TEST_MARKER = "container-1"' not in text:
    path.write_text(text + '\nTEST_MARKER = "container-1"\n', encoding="utf-8")
importlib.invalidate_caches()
reloaded = importlib.reload(mindex)
print(getattr(reloaded, "TEST_MARKER", "missing"))
PY''',
        )
        self.assertEqual(mutate_result.returncode, 0, mutate_result.stderr)
        self.assertIn("container-1", mutate_result.stdout)

        verify_same = self._docker_exec(
            self.name1,
            'python - <<\'PY\'\nimport mindex\nprint(getattr(mindex, "TEST_MARKER", "missing"))\nPY',
        )
        self.assertEqual(verify_same.returncode, 0, verify_same.stderr)
        self.assertIn("container-1", verify_same.stdout)

        verify_other = self._docker_exec(
            self.name2,
            'python - <<\'PY\'\nimport mindex\nprint(getattr(mindex, "TEST_MARKER", "missing"))\nPY',
        )
        self.assertEqual(verify_other.returncode, 0, verify_other.stderr)
        self.assertIn("missing", verify_other.stdout)
        self.assertFalse((self.home / ".mindex" / "TEST_MARKER").exists())
        self.assertNotIn('TEST_MARKER = "container-1"', (Path(__file__).resolve().parents[1] / "mindex" / "__init__.py").read_text(encoding="utf-8"))

    def test_web_app_is_reachable_from_host_through_mapped_port(self) -> None:
        self._write_config()
        env = self._env()
        (self.shared_host / "index.html").write_text("hello-web\n", encoding="utf-8")

        self.assertEqual(container_main(["ports"], project_root=self.project1, env=env), 0)
        payload = self._inspect(self.name1)
        host_port = self._host_ports(payload)[3000]

        serve_result = self._docker_exec(
            self.name1,
            "cd /shared && nohup python3 -m http.server 3000 >/tmp/mindex-http.log 2>&1 &",
        )
        self.assertEqual(serve_result.returncode, 0, serve_result.stderr)

        body = self._wait_http(f"http://127.0.0.1:{host_port}/index.html")
        self.assertEqual(body, "hello-web\n")

    def test_block_mode_retries_when_first_port_block_is_busy(self) -> None:
        self._write_config(
            port_mapping={
                "mode": "block",
                "host_ip": "127.0.0.1",
                "container_port_range_start": 3000,
                "container_port_count": 2,
                "extra_container_ports": [],
                "host_port_base": None,
                "host_port_range_start": 41000,
                "host_port_range_end": 41029,
                "static_host_ports": {},
            }
        )
        env = self._env()

        sockets: list[socket.socket] = []
        try:
            for port in (41000, 41001):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
                sock.listen(1)
                sockets.append(sock)

            with mock.patch("mindex.container_mode.candidate_host_port_bases", return_value=[41000, 41002, 41004]):
                self.assertEqual(container_main(["ports"], project_root=self.project1, env=env), 0)

            payload = self._inspect(self.name1)
            host_ports = set(self._host_ports(payload).values())
            self.assertEqual(host_ports, {41002, 41003})
        finally:
            for sock in sockets:
                sock.close()

    def test_static_port_mapping_uses_exact_host_port(self) -> None:
        self._write_config(
            port_mapping={
                "mode": "static",
                "host_ip": "127.0.0.1",
                "container_port_range_start": 3000,
                "container_port_count": 1,
                "extra_container_ports": [],
                "host_port_base": None,
                "host_port_range_start": 41000,
                "host_port_range_end": 49000,
                "static_host_ports": {"3000": 45678},
            }
        )
        env = self._env()
        (self.shared_host / "index.html").write_text("static-port\n", encoding="utf-8")

        self.assertEqual(container_main(["ports"], project_root=self.project1, env=env), 0)
        payload = self._inspect(self.name1)
        self.assertEqual(self._host_ports(payload)[3000], 45678)

        serve_result = self._docker_exec(
            self.name1,
            "cd /shared && nohup python3 -m http.server 3000 >/tmp/mindex-http-static.log 2>&1 &",
        )
        self.assertEqual(serve_result.returncode, 0, serve_result.stderr)
        body = self._wait_http("http://127.0.0.1:45678/index.html")
        self.assertEqual(body, "static-port\n")


if __name__ == "__main__":
    unittest.main()
