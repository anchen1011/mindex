from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from mindex.logging_utils import create_log_paths, slugify


class LoggingUtilsTests(unittest.TestCase):
    def test_slugify_falls_back_for_empty_text(self):
        self.assertEqual(slugify(''), 'interactive')

    def test_create_log_paths_uses_expected_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 3, 17, 18, 0, 0, tzinfo=timezone.utc)
            paths = create_log_paths(Path(tmp), prompt='Configure Mindex', session_id='session-test', now=now)
            self.assertTrue(paths.root.exists())
            self.assertIn('2026-03-17', str(paths.root))
            self.assertIn('session-test', str(paths.root))
            self.assertTrue(str(paths.root).endswith('configure-mindex'))


if __name__ == '__main__':
    unittest.main()
