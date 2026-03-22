from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from mindex import codoxear


class CodoxearConfigTests(unittest.TestCase):
    def test_url_prefix_normalization(self) -> None:
        self.assertEqual(codoxear._normalize_url_prefix(""), "")
        self.assertEqual(codoxear._normalize_url_prefix("/codoxear"), "/codoxear")
        self.assertEqual(codoxear._normalize_url_prefix("/codoxear/"), "/codoxear")
        with self.assertRaises(ValueError):
            codoxear._normalize_url_prefix("codoxear")

    def test_bind_policy_requires_explicit_allow_remote(self) -> None:
        codoxear._assert_bind_is_allowed("127.0.0.1", allow_remote=False)
        codoxear._assert_bind_is_allowed("localhost", allow_remote=False)
        with self.assertRaises(ValueError):
            codoxear._assert_bind_is_allowed("0.0.0.0", allow_remote=False)
        with self.assertRaises(ValueError):
            codoxear._assert_bind_is_allowed("::", allow_remote=False)
        codoxear._assert_bind_is_allowed("0.0.0.0", allow_remote=True)

    def test_allow_remote_defaults_follow_host(self) -> None:
        self.assertTrue(codoxear._resolve_allow_remote_argument("0.0.0.0", None))
        self.assertTrue(codoxear._resolve_allow_remote_argument("192.168.1.10", None))
        self.assertFalse(codoxear._resolve_allow_remote_argument("127.0.0.1", None))
        self.assertFalse(codoxear._resolve_allow_remote_argument("localhost", None))

    def test_init_writes_hash_no_plaintext_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "codoxear.json"
            env = {"MINDEX_CODOXEAR_CONFIG_PATH": str(config_path), **os.environ}

            password = "abc123"
            payload = codoxear._build_config_payload(
                config_path=config_path,
                host="0.0.0.0",
                port=8743,
                url_prefix="",
                allow_remote=True,
                password=password,
                codex_home=str(Path(tmp) / "codex-home"),
                codex_bin="mindex",
            )
            codoxear.write_config(payload, env=env)

            raw = config_path.read_text(encoding="utf-8")
            self.assertNotIn(password, raw)
            self.assertIn("password_hash", raw)
            self.assertIn("password_salt", raw)

            loaded = codoxear.load_config(env=env)
            self.assertTrue(
                codoxear._verify_password(
                    password,
                    expected_hash=loaded.password_hash,
                    salt_hex=loaded.password_salt,
                    iterations=loaded.password_iterations,
                )
            )
            if os.name == "posix":
                mode = stat.S_IMODE(config_path.stat().st_mode)
                self.assertEqual(mode, 0o600)

    def test_build_server_env(self) -> None:
        config = codoxear.CodoxearConfig(
            host="0.0.0.0",
            port=8743,
            url_prefix="/codoxear",
            allow_remote=True,
            password_hash="",
            password_salt="",
            password_iterations=codoxear.PASSWORD_ITERATIONS,
            codex_home="/tmp/codex-home",
            codex_bin="mindex",
            config_path=Path("/tmp/nowhere.json"),
        )
        env = codoxear._build_server_env(config, password="pw")
        self.assertEqual(env["CODEX_WEB_PASSWORD"], "pw")
        self.assertEqual(env["CODEX_WEB_HOST"], "0.0.0.0")
        self.assertEqual(env["CODEX_WEB_PORT"], "8743")
        self.assertEqual(env["CODEX_WEB_URL_PREFIX"], "/codoxear")
        self.assertEqual(env["CODEX_HOME"], "/tmp/codex-home")
        self.assertEqual(env["CODEX_BIN"], "mindex")

    def test_load_config_defaults_remote_for_wildcard_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            codoxear._write_private_json(
                config_path,
                {
                    "auth": {
                        "password_hash": "x",
                        "password_salt": "00",
                        "password_iterations": codoxear.PASSWORD_ITERATIONS,
                    },
                    "server": {
                        "host": "0.0.0.0",
                        "port": 8743,
                        "url_prefix": "",
                    },
                    "codex": {"home": "/tmp/codex-home", "bin": "mindex"},
                },
            )
            loaded = codoxear.load_config(env={"MINDEX_CODOXEAR_CONFIG_PATH": str(config_path)})
            self.assertTrue(loaded.allow_remote)

    def test_find_broker_prefers_mindex_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv_dir = Path(tmp) / "venv"
            broker = venv_dir / "bin" / "codoxear-broker"
            broker.parent.mkdir(parents=True, exist_ok=True)
            broker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            broker.chmod(0o755)
            found = codoxear._find_codoxear_broker(env={"MINDEX_CODOXEAR_VENV_DIR": str(venv_dir)})
            self.assertEqual(found, str(broker))

    def test_sanitize_argv_redacts_password(self) -> None:
        argv = ["serve", "--password", "sekret", "--password=sekret2", "--no-verify"]
        sanitized = codoxear._sanitize_argv_for_logging(argv)
        self.assertNotIn("sekret", sanitized)
        self.assertNotIn("sekret2", sanitized)
        self.assertIn("--password", sanitized)

    def test_ui_legacy_args_are_ignored_or_mapped(self) -> None:
        args, warnings = codoxear._normalize_legacy_ui_args(
            ["--init-only", "--project-root", "/tmp/project", "--dev", "--password", "pw"]
        )
        self.assertIn("init-config", args[0:1])
        self.assertNotIn("--project-root", args)
        self.assertNotIn("--dev", args)
        self.assertTrue(any("init-only" in w for w in warnings))

    def test_serve_exports_expected_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            venv_dir = tmp_path / "venv"
            server_bin = venv_dir / "bin" / "codoxear-server"
            server_bin.parent.mkdir(parents=True, exist_ok=True)

            capture = tmp_path / "capture.json"
            server_bin.write_text(
                """#!/usr/bin/env python3
import json, os, pathlib
path = os.environ.get("MINDEX_TEST_CAPTURE_PATH")
if path:
    keys = ["CODEX_WEB_PASSWORD", "CODEX_WEB_HOST", "CODEX_WEB_PORT", "CODEX_WEB_URL_PREFIX", "CODEX_HOME", "CODEX_BIN"]
    data = {k: os.environ.get(k) for k in keys}
    pathlib.Path(path).write_text(json.dumps(data), encoding="utf-8")
""",
                encoding="utf-8",
            )
            server_bin.chmod(0o755)

            config_path = tmp_path / "config.json"
            codoxear._write_private_json(
                config_path,
                {
                    "auth": {
                        "password_hash": "x",
                        "password_salt": "00",
                        "password_iterations": codoxear.PASSWORD_ITERATIONS,
                    },
                    "server": {
                        "host": "0.0.0.0",
                        "port": 9999,
                        "url_prefix": "/x",
                        "allow_remote": True,
                    },
                    "codex": {
                        "home": "/tmp/codex-home",
                        "bin": "mindex",
                    },
                },
            )

            env = {
                "MINDEX_CODOXEAR_CONFIG_PATH": str(config_path),
                "MINDEX_CODOXEAR_VENV_DIR": str(venv_dir),
                "MINDEX_TEST_CAPTURE_PATH": str(capture),
            }
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with mock.patch.dict(os.environ, env, clear=False):
                    rc = codoxear.main(["serve", "--password", "pw", "--no-verify"], invoked_as="codoxear")
            self.assertEqual(rc, 0)
            payload = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(payload["CODEX_WEB_PASSWORD"], "pw")
            self.assertEqual(payload["CODEX_WEB_HOST"], "0.0.0.0")
            self.assertEqual(payload["CODEX_WEB_PORT"], "9999")
            self.assertEqual(payload["CODEX_WEB_URL_PREFIX"], "/x")
            self.assertEqual(payload["CODEX_HOME"], "/tmp/codex-home")
            self.assertEqual(payload["CODEX_BIN"], "mindex")

    def test_broker_passes_codex_env_and_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            venv_dir = tmp_path / "venv"
            broker_bin = venv_dir / "bin" / "codoxear-broker"
            broker_bin.parent.mkdir(parents=True, exist_ok=True)

            capture = tmp_path / "broker.txt"
            broker_bin.write_text(
                """#!/usr/bin/env python3
import os, pathlib, sys
path = os.environ.get("MINDEX_TEST_CAPTURE_PATH")
if path:
    msg = []
    msg.append("argv=" + " ".join(sys.argv[1:]))
    msg.append("CODEX_HOME=" + str(os.environ.get("CODEX_HOME")))
    msg.append("CODEX_BIN=" + str(os.environ.get("CODEX_BIN")))
    pathlib.Path(path).write_text("\\n".join(msg), encoding="utf-8")
""",
                encoding="utf-8",
            )
            broker_bin.chmod(0o755)

            config_path = tmp_path / "config.json"
            codoxear._write_private_json(
                config_path,
                {
                    "auth": {
                        "password_hash": "x",
                        "password_salt": "00",
                        "password_iterations": codoxear.PASSWORD_ITERATIONS,
                    },
                    "server": {"host": "0.0.0.0", "port": 8743, "url_prefix": "", "allow_remote": True},
                    "codex": {"home": "/tmp/codex-home-broker", "bin": "mindex"},
                },
            )

            env = {
                "MINDEX_CODOXEAR_CONFIG_PATH": str(config_path),
                "MINDEX_CODOXEAR_VENV_DIR": str(venv_dir),
                "MINDEX_TEST_CAPTURE_PATH": str(capture),
            }
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with mock.patch.dict(os.environ, env, clear=False):
                    rc = codoxear.main(["broker", "--", "/new"], invoked_as="codoxear")
            self.assertEqual(rc, 0)
            text = capture.read_text(encoding="utf-8")
            self.assertIn("argv=-- /new", text)
            self.assertIn("CODEX_HOME=/tmp/codex-home-broker", text)
            self.assertIn("CODEX_BIN=mindex", text)

    def test_password_is_not_logged_in_prompt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            logs_root = tmp_path / "logs"
            config_path = tmp_path / "config.json"

            env = {
                "MINDEX_LOGS_ROOT": str(logs_root),
                "MINDEX_CODOXEAR_CONFIG_PATH": str(config_path),
            }
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with mock.patch.dict(os.environ, env, clear=False):
                    rc = codoxear.main(["init-config", "--password", "sekret"], invoked_as="codoxear")
            self.assertEqual(rc, 0)
            prompt_files = list(logs_root.rglob("prompt.txt"))
            self.assertTrue(prompt_files)
            combined = "\\n".join(p.read_text(encoding="utf-8") for p in prompt_files)
            self.assertNotIn("sekret", combined)
            self.assertIn("****", combined)

    def test_setup_installs_and_creates_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            logs_root = tmp_path / "logs"
            config_path = tmp_path / "config.json"
            venv_dir = tmp_path / "venv"

            env = {
                "MINDEX_LOGS_ROOT": str(logs_root),
                "MINDEX_CODOXEAR_CONFIG_PATH": str(config_path),
                "MINDEX_CODOXEAR_VENV_DIR": str(venv_dir),
            }
            with mock.patch("mindex.codoxear._run_install", return_value=0) as run_install:
                with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
                    with mock.patch.dict(os.environ, env, clear=False):
                        rc = codoxear.main(["setup", "--password", "sekret"], invoked_as="ui")
            self.assertEqual(rc, 0)
            self.assertTrue(config_path.exists())
            self.assertIn("Codoxear UI setup complete.", stdout.getvalue())
            self.assertIn("mindex ui serve", stdout.getvalue())
            run_install.assert_called_once()
            raw = config_path.read_text(encoding="utf-8")
            self.assertNotIn("sekret", raw)

    def test_setup_keeps_existing_config_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            original = codoxear._build_config_payload(
                config_path=config_path,
                host="0.0.0.0",
                port=8743,
                url_prefix="",
                allow_remote=True,
                password="first",
                codex_home="/tmp/codex-home",
                codex_bin="mindex",
            )
            codoxear._write_private_json(config_path, original)

            env = {"MINDEX_CODOXEAR_CONFIG_PATH": str(config_path)}
            with mock.patch("mindex.codoxear._run_install", return_value=0):
                with mock.patch("mindex.codoxear._prompt_password") as prompt_password:
                    with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
                        with mock.patch.dict(os.environ, env, clear=False):
                            rc = codoxear.main(["setup"], invoked_as="ui")
            self.assertEqual(rc, 0)
            self.assertIn("keeping it unchanged", stdout.getvalue())
            prompt_password.assert_not_called()

    def test_setup_can_force_local_only_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            env = {"MINDEX_CODOXEAR_CONFIG_PATH": str(config_path)}
            with mock.patch("mindex.codoxear._run_install", return_value=0):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with mock.patch.dict(os.environ, env, clear=False):
                        rc = codoxear.main(
                            ["setup", "--reset-config", "--host", "127.0.0.1", "--local-only", "--password", "sekret"],
                            invoked_as="ui",
                        )
            self.assertEqual(rc, 0)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["server"]["host"], "127.0.0.1")
            self.assertFalse(payload["server"]["allow_remote"])

    def test_setup_can_serve_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            env = {"MINDEX_CODOXEAR_CONFIG_PATH": str(config_path)}
            with mock.patch("mindex.codoxear._run_install", return_value=0):
                with mock.patch("mindex.codoxear._serve", return_value=0) as serve:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        with mock.patch.dict(os.environ, env, clear=False):
                            rc = codoxear.main(["setup", "--password", "sekret", "--serve"], invoked_as="ui")
            self.assertEqual(rc, 0)
            serve.assert_called_once()
            self.assertEqual(serve.call_args.kwargs["invoked_as"], "ui")
            self.assertEqual(serve.call_args.args[0], ["--password", "sekret"])

if __name__ == "__main__":
    unittest.main()
