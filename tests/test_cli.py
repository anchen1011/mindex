from __future__ import annotations

import unittest
from unittest import mock

from mindex import cli


class CliTests(unittest.TestCase):
    def test_ui_path_does_not_import_configure_module(self) -> None:
        with mock.patch("mindex.cli.find_project_root", return_value="/tmp/project"):
            with mock.patch("mindex.cli.codoxear_main", return_value=0) as codoxear_main:
                with mock.patch("mindex.cli.import_module") as import_module:
                    rc = cli.main(["ui", "setup"])
        self.assertEqual(rc, 0)
        codoxear_main.assert_called_once_with(["setup"], invoked_as="ui")
        import_module.assert_not_called()

    def test_configure_path_imports_module_lazily(self) -> None:
        fake_module = mock.Mock()
        fake_module.main.return_value = 7
        with mock.patch("mindex.cli.find_project_root", return_value="/tmp/project"):
            with mock.patch("mindex.cli.import_module", return_value=fake_module) as import_module:
                rc = cli.main(["configure", "--dry-run"])
        self.assertEqual(rc, 7)
        import_module.assert_called_once_with("mindex.configure")
        fake_module.main.assert_called_once_with(["configure", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
