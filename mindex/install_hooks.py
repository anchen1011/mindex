import os
import subprocess
import sys
from pathlib import Path


def run_post_install(
    install_mode: str,
    project_root: Path,
    env: dict[str, str] | None = None,
    runner=subprocess.check_call,
) -> list[str] | None:
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    if merged_env.get('MINDEX_SKIP_AUTO_CONFIGURE') == '1':
        return None

    command = [
        sys.executable,
        '-m',
        'mindex.configure',
        'install-hook',
        '--install-mode',
        install_mode,
        '--project-root',
        str(project_root),
    ]
    runner(command, env=merged_env)
    return command
