from pathlib import Path
import unittest

from mindex.install_hooks import run_post_install


class InstallHooksTests(unittest.TestCase):
    def test_skip_auto_configure_short_circuits_runner(self):
        calls = []

        def fake_runner(command, env=None):
            calls.append((command, env))

        result = run_post_install(
            'editable',
            project_root=Path('/tmp/mindex'),
            env={'MINDEX_SKIP_AUTO_CONFIGURE': '1'},
            runner=fake_runner,
        )
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_run_post_install_builds_module_command(self):
        calls = []

        def fake_runner(command, env=None):
            calls.append((command, env))

        command = run_post_install('editable', project_root=Path('/tmp/mindex'), runner=fake_runner)
        self.assertIn('mindex.configure', command)
        self.assertEqual(calls[0][0], command)


if __name__ == '__main__':
    unittest.main()
