from pathlib import Path
import os
import subprocess
import sys

from setuptools import find_packages, setup
from setuptools.command.develop import develop
try:
    from setuptools.command.editable_wheel import editable_wheel
except ImportError:  # pragma: no cover - older setuptools
    editable_wheel = None
from setuptools.command.install import install


REPO_ROOT = Path(__file__).resolve().parent


def _maybe_run_auto_configure() -> None:
    if os.environ.get("MINDEX_SKIP_AUTO_CONFIGURE") == "1":
        return

    command = [
        sys.executable,
        "-m",
        "mindex.configure",
        "configure",
        "--project-root",
        str(REPO_ROOT),
    ]
    env = os.environ.copy()
    env.setdefault("MINDEX_AUTO_CONFIGURE_SOURCE", "setup.py")

    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "auto-configure failed"
        print(f"warning: {message}", file=sys.stderr)


class AutoConfigureDevelop(develop):
    def run(self):
        super().run()
        _maybe_run_auto_configure()


class AutoConfigureInstall(install):
    def run(self):
        super().run()
        _maybe_run_auto_configure()


if editable_wheel is not None:
    class AutoConfigureEditableWheel(editable_wheel):
        def run(self):
            super().run()
            _maybe_run_auto_configure()
else:  # pragma: no cover - older setuptools
    AutoConfigureEditableWheel = None


cmdclass = {"develop": AutoConfigureDevelop, "install": AutoConfigureInstall}
if AutoConfigureEditableWheel is not None:
    cmdclass["editable_wheel"] = AutoConfigureEditableWheel


setup(
    name="mindex",
    version="0.1.0",
    description="Managed Codex wrapper and configuration layer for running Mindex across projects.",
    long_description=(REPO_ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    license="MIT",
    packages=find_packages(),
    include_package_data=True,
    package_data={"mindex": ["assets/skills/*/SKILL.md"]},
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "mindex=mindex.cli:main",
            "mindex-ui-setup=mindex.codoxear:setup_entrypoint",
        ]
    },
    cmdclass=cmdclass,
)
