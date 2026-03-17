from pathlib import Path
import tempfile
import unittest

from mindex.skill_assets import available_skill_directories, install_skills


class SkillAssetsTests(unittest.TestCase):
    def test_available_skill_directories_includes_expected_skills(self):
        names = [path.name for path in available_skill_directories()]
        self.assertIn('configure', names)
        self.assertIn('repo', names)

    def test_install_skills_copies_skill_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            installed = install_skills(codex_home)
            installed_names = [path.name for path in installed]
            self.assertIn('configure', installed_names)
            self.assertIn('repo', installed_names)
            self.assertTrue((codex_home / 'skills' / 'configure' / 'SKILL.md').exists())
            self.assertTrue((codex_home / 'skills' / 'repo' / 'SKILL.md').exists())


if __name__ == '__main__':
    unittest.main()
