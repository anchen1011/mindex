from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

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

    def test_init_writes_hash_no_plaintext_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "codoxear.json"
            env = {"MINDEX_CODOXEAR_CONFIG_PATH": str(config_path), **os.environ}

            password = "abc123"
            payload = codoxear._build_config_payload(
                config_path=config_path,
                host="127.0.0.1",
                port=8743,
                url_prefix="",
                allow_remote=False,
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

    def test_build_server_env(self) -> None:
        config = codoxear.CodoxearConfig(
            host="127.0.0.1",
            port=8743,
            url_prefix="/codoxear",
            allow_remote=False,
            password_hash="",
            password_salt="",
            password_iterations=codoxear.PASSWORD_ITERATIONS,
            codex_home="/tmp/codex-home",
            codex_bin="mindex",
            config_path=Path("/tmp/nowhere.json"),
        )
        env = codoxear._build_server_env(config, password="pw")
        self.assertEqual(env["CODEX_WEB_PASSWORD"], "pw")
        self.assertEqual(env["CODEX_WEB_HOST"], "127.0.0.1")
        self.assertEqual(env["CODEX_WEB_PORT"], "8743")
        self.assertEqual(env["CODEX_WEB_URL_PREFIX"], "/codoxear")
        self.assertEqual(env["CODEX_HOME"], "/tmp/codex-home")
        self.assertEqual(env["CODEX_BIN"], "mindex")


if __name__ == "__main__":
    unittest.main()

