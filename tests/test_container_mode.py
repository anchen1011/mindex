from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from mindex.container_mode import (
    candidate_host_port_bases,
    container_name_for_project,
    create_container,
    docker_publish_args,
    exec_container_mindex_then_shell,
    should_default_to_container,
)
from mindex.mindex_config import ContainerConfig, PortMappingConfig, SharedFolderMount, load_mindex_config


class ContainerModeTests(unittest.TestCase):
    def test_container_name_is_stable_and_hashes_project_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            name1 = container_name_for_project(root)
            name2 = container_name_for_project(root)
            self.assertEqual(name1, name2)
            self.assertTrue(name1.startswith("mindex-repo-"))
            self.assertEqual(len(name1.split("-")[-1]), 8)

    def test_load_mindex_config_creates_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            env = {"MINDEX_CONFIG_PATH": str(config_path)}

            config = load_mindex_config(env=env, create_if_missing=True)

            self.assertTrue(config_path.exists())
            self.assertFalse(config.container.enabled_by_default)
            self.assertEqual(config.container.port_mapping.mode, "block")
            self.assertIn(8765, config.container.port_mapping.container_ports())
            shared = config.container.shared_folders
            self.assertEqual(len(shared), 1)
            self.assertTrue(str(shared[0].host).endswith("shared"))
            self.assertEqual(shared[0].container, "/shared")

    def test_should_default_to_container_respects_in_container_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            env = {"MINDEX_CONFIG_PATH": str(config_path), "MINDEX_IN_CONTAINER": "1"}
            self.assertFalse(should_default_to_container(env=env))

    def test_docker_publish_args_block_maps_contiguous_host_ports(self) -> None:
        port_config = PortMappingConfig(
            mode="block",
            host_ip="127.0.0.1",
            container_port_range_start=3000,
            container_port_count=2,
            extra_container_ports=(8765,),
            host_port_base=40000,
            host_port_range_start=41000,
            host_port_range_end=49000,
            static_host_ports={},
        )

        args = docker_publish_args(port_config, host_port_base=45000)

        self.assertEqual(
            args,
            [
                "-p",
                "127.0.0.1:45000:3000",
                "-p",
                "127.0.0.1:45001:3001",
                "-p",
                "127.0.0.1:45002:8765",
            ],
        )

    def test_candidate_host_port_bases_produces_valid_blocks(self) -> None:
        bases = candidate_host_port_bases(
            container_name="mindex-demo-deadbeef",
            pool_start=41000,
            pool_end=41039,
            block_size=10,
        )
        self.assertTrue(bases)
        self.assertEqual(len(bases), len(set(bases)))
        for base in bases:
            self.assertGreaterEqual(base, 41000)
            self.assertLessEqual(base + 9, 41039)
            self.assertEqual((base - 41000) % 10, 0)

    def test_create_container_retries_on_port_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "repo"
            project_root.mkdir()

            port_config = PortMappingConfig(
                mode="block",
                host_ip="127.0.0.1",
                container_port_range_start=3000,
                container_port_count=2,
                extra_container_ports=(),
                host_port_base=None,
                host_port_range_start=41000,
                host_port_range_end=41019,
                static_host_ports={},
            )
            config = ContainerConfig(
                enabled_by_default=False,
                image_name="mindex-container",
                image_tag="latest",
                shared_folders=(SharedFolderMount(host=Path(tmpdir), container="/shared"),),
                port_mapping=port_config,
            )

            calls: list[list[str]] = []

            def fake_run_docker(args, *, capture=False, check=False, env=None):
                # Only intercept `docker run` calls; everything else should not happen here.
                calls.append(list(args))
                if args and args[0] == "run":
                    if len([part for part in args if part.startswith("127.0.0.1:")]) == 0:
                        return SimpleNamespace(returncode=1, stdout="", stderr="missing publish")
                    # First attempt fails with port allocation, second succeeds.
                    if len([c for c in calls if c and c[0] == "run"]) == 1:
                        return SimpleNamespace(
                            returncode=1,
                            stdout="",
                            stderr="Error starting userland proxy: listen tcp4 0.0.0.0:0: bind: address already in use (port is already allocated)",
                        )
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch("mindex.container_mode.image_exists", return_value=True), mock.patch(
                "mindex.container_mode.ensure_container_image"
            ), mock.patch("mindex.container_mode._run_docker", side_effect=fake_run_docker), mock.patch(
                "mindex.container_mode.container_exists", return_value=False
            ):
                create_container(container_name="mindex-repo-deadbeef", config=config, project_root=project_root)

            run_calls = [call for call in calls if call and call[0] == "run"]
            self.assertEqual(len(run_calls), 2)
            # Ensure host port base differs between attempts.
            first = " ".join(run_calls[0])
            second = " ".join(run_calls[1])
            self.assertNotEqual(first, second)

    def test_exec_container_mindex_then_shell_invokes_docker_exec(self) -> None:
        with mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = SimpleNamespace(returncode=0)

            exec_container_mindex_then_shell("mindex-test", mindex_args=("status", "--json"))

            self.assertTrue(run_mock.called)
            args = run_mock.call_args[0][0]
            self.assertEqual(args[0:2], ["docker", "exec"])
            self.assertIn("mindex", " ".join(args))


if __name__ == "__main__":
    unittest.main()
