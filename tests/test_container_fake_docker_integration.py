from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from mindex.container_mode import ContainerError, container_main


class FakeDockerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.home = self.root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.fake_docker_root = self.root / "fake-docker"
        self.fake_docker_root.mkdir(parents=True, exist_ok=True)

        # Prepare a fake `docker` binary in PATH.
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        fake_docker_src = Path(__file__).resolve().parent / "fake_docker.py"
        docker_path = self.bin_dir / "docker"
        shutil.copy2(fake_docker_src, docker_path)
        docker_path.chmod(0o755)

        # A shared folder mount root.
        self.shared_host = self.root / "shared-host"
        self.shared_host.mkdir(parents=True, exist_ok=True)
        (self.shared_host / "test_shared.txt").write_text("shared-ok\n", encoding="utf-8")

        # Point Mindex to a temp config file so tests do not touch real ~/.mindex.
        self.config_path = self.root / "mindex-config.json"
        self.env = dict(os.environ)
        self.env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin_dir}:{self.env.get('PATH','')}",
                "FAKE_DOCKER_ROOT": str(self.fake_docker_root),
                "MINDEX_CONFIG_PATH": str(self.config_path),
            }
        )

        self.project_root = self.root / "project"
        self.project_root.mkdir(parents=True, exist_ok=True)
        # Make it look like a project root so find_project_root isn't needed.
        (self.project_root / "README.md").write_text("# project\n", encoding="utf-8")
        (self.project_root / "HISTORY.md").write_text("# history\n", encoding="utf-8")

        # Write container config with explicit shared folder.
        payload = {
            "container": {
                "enabled_by_default": False,
                "image": {"name": "mindex-container", "tag": "latest"},
                "shared_folders": [
                    {"host": str(self.shared_host), "container": "/shared", "read_only": False},
                ],
                "port_mapping": {
                    "mode": "block",
                    "host_ip": "127.0.0.1",
                    "container_port_range_start": 3000,
                    "container_port_count": 2,
                    "extra_container_ports": [8765],
                    "host_port_base": None,
                    "host_port_range_start": 41000,
                    "host_port_range_end": 41029,
                    "static_host_ports": {},
                },
            }
        }
        self.config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _read_fake_state(self) -> dict:
        return json.loads((self.fake_docker_root / "state.json").read_text(encoding="utf-8"))

    def test_container_mode_isolated_state_and_shared_folder_access(self) -> None:
        """
        Covers:
        1) Independent env/state: container writes to its own /root/.mindex volume, not host ~/.mindex.
        2) Shared folder access: file in shared mount is readable and observed by in-container `mindex`.
        3) Entering container starts mindex: `mindex container` triggers `docker exec ... mindex`.
        """
        out = io.StringIO()
        with redirect_stdout(out):
            rc = container_main([], project_root=self.project_root, env=self.env)
        self.assertEqual(rc, 0)

        # Host should not see container evidence in its own ~/.mindex.
        self.assertFalse((self.home / ".mindex" / "ran.txt").exists())

        state = self._read_fake_state()
        self.assertIn("mindex-container:latest", state["images"])
        self.assertEqual(len(state["containers"]), 1)

        container_name = next(iter(state["containers"].keys()))
        volume_dir = self.fake_docker_root / "volumes" / f"{container_name}-mindex"
        self.assertTrue((volume_dir / "ran.txt").exists())
        self.assertEqual((volume_dir / "ran.txt").read_text(encoding="utf-8").strip(), "ran")

        # Shared folder evidence: fake in-container mindex copies /shared/test_shared.txt into /root/.mindex/shared_seen.txt.
        self.assertTrue((volume_dir / "shared_seen.txt").exists())
        self.assertEqual((volume_dir / "shared_seen.txt").read_text(encoding="utf-8"), "shared-ok\n")

        # Port mapping hint is printed for external testing.
        self.assertIn("[mindex container] Port mappings", out.getvalue())

    def test_port_mapping_dynamic_allocation_supports_multiple_containers(self) -> None:
        project2 = self.root / "project2"
        project2.mkdir(parents=True, exist_ok=True)
        (project2 / "README.md").write_text("# project2\n", encoding="utf-8")
        (project2 / "HISTORY.md").write_text("# history\n", encoding="utf-8")

        sink = io.StringIO()
        with redirect_stdout(sink):
            rc1 = container_main(["ports"], project_root=self.project_root, env=self.env)
            rc2 = container_main(["ports"], project_root=project2, env=self.env)
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)

        state = self._read_fake_state()
        self.assertEqual(len(state["containers"]), 2)
        # Ensure the host ports do not overlap across containers.
        used = state.get("used_host_ports", [])
        self.assertEqual(len(used), len(set(used)))

    def test_port_mapping_retries_on_conflict_and_picks_another_block(self) -> None:
        """
        Force both containers to attempt the same base first, then ensure the second retries.
        """
        project2 = self.root / "project2"
        project2.mkdir(parents=True, exist_ok=True)
        (project2 / "README.md").write_text("# project2\n", encoding="utf-8")
        (project2 / "HISTORY.md").write_text("# history\n", encoding="utf-8")

        with mock.patch(
            "mindex.container_mode.candidate_host_port_bases",
            return_value=[41000, 41003, 41006],
        ):
            # First container consumes 41000..41002.
            sink = io.StringIO()
            with redirect_stdout(sink):
                self.assertEqual(container_main(["ports"], project_root=self.project_root, env=self.env), 0)
            # Second container will collide on 41000..41002 then retry 41003..41005.
            with redirect_stdout(sink):
                self.assertEqual(container_main(["ports"], project_root=project2, env=self.env), 0)

        state = self._read_fake_state()
        self.assertEqual(len(state["containers"]), 2)
        names = sorted(state["containers"].keys())
        ports1 = state["containers"][names[0]]["NetworkSettings"]["Ports"]
        ports2 = state["containers"][names[1]]["NetworkSettings"]["Ports"]

        host_ports_1 = {int(value[0]["HostPort"], 10) for value in ports1.values()}
        host_ports_2 = {int(value[0]["HostPort"], 10) for value in ports2.values()}
        self.assertTrue(host_ports_1.isdisjoint(host_ports_2))

        # The second container should have used the second block start.
        self.assertIn(41003, host_ports_2)

    def test_port_mapping_guardrails_refuse_too_many_ports(self) -> None:
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        payload["container"]["port_mapping"]["container_port_count"] = 40
        self.config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        with self.assertRaises(ContainerError):
            container_main(["ports"], project_root=self.project_root, env=self.env)

    def test_port_mapping_docker_random_avoids_collisions_across_containers(self) -> None:
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        payload["container"]["port_mapping"]["mode"] = "docker-random"
        payload["container"]["port_mapping"]["container_port_count"] = 1
        payload["container"]["port_mapping"]["extra_container_ports"] = []
        self.config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        project2 = self.root / "project2"
        project2.mkdir(parents=True, exist_ok=True)
        (project2 / "README.md").write_text("# project2\n", encoding="utf-8")
        (project2 / "HISTORY.md").write_text("# history\n", encoding="utf-8")

        sink = io.StringIO()
        with redirect_stdout(sink):
            self.assertEqual(container_main(["ports"], project_root=self.project_root, env=self.env), 0)
            self.assertEqual(container_main(["ports"], project_root=project2, env=self.env), 0)

        state = self._read_fake_state()
        self.assertEqual(len(state["containers"]), 2)
        used = state.get("used_host_ports", [])
        self.assertEqual(len(used), len(set(used)))
        for port in used:
            self.assertGreaterEqual(port, 41000)
            self.assertLessEqual(port, 49000)

    def test_port_mapping_static_maps_exact_ports(self) -> None:
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        payload["container"]["port_mapping"]["mode"] = "static"
        payload["container"]["port_mapping"]["container_port_count"] = 1
        payload["container"]["port_mapping"]["extra_container_ports"] = []
        payload["container"]["port_mapping"]["static_host_ports"] = {"3000": 45678}
        self.config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        sink = io.StringIO()
        with redirect_stdout(sink):
            self.assertEqual(container_main(["ports"], project_root=self.project_root, env=self.env), 0)

        state = self._read_fake_state()
        container_name = next(iter(state["containers"].keys()))
        ports = state["containers"][container_name]["NetworkSettings"]["Ports"]
        self.assertIn("3000/tcp", ports)
        self.assertEqual(ports["3000/tcp"][0]["HostPort"], "45678")


if __name__ == "__main__":
    unittest.main()
