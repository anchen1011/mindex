from pathlib import Path

from setuptools import find_packages, setup
from setuptools.command.develop import develop
from setuptools.command.install import install

from mindex.install_hooks import run_post_install


ROOT = Path(__file__).resolve().parent


class MindexInstall(install):
    def run(self):
        super().run()
        run_post_install('install', project_root=ROOT)


class MindexDevelop(develop):
    def run(self):
        super().run()
        run_post_install('editable', project_root=ROOT)


setup(
    name='mindex',
    version='0.1.0',
    description='Project-specific Codex bootstrap and launcher for Mindex.',
    packages=find_packages(exclude=('tests', 'tests.*')),
    include_package_data=True,
    package_data={
        'mindex': [
            'assets/skills/*/SKILL.md',
            'assets/skills/*/agents/openai.yaml',
        ]
    },
    entry_points={
        'console_scripts': [
            'mindex=mindex.cli:main',
        ]
    },
    cmdclass={
        'install': MindexInstall,
        'develop': MindexDevelop,
    },
)
