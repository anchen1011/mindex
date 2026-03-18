from __future__ import annotations

import sys
from typing import Iterable

from mindex import __version__
from mindex.configure import main as configure_main
from mindex.github_workflow import main as github_workflow_main
from mindex.launcher import find_project_root, launch_codex
from mindex.ui import main as ui_main


def main(argv: Iterable[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    project_root = find_project_root()
    if not args:
        return launch_codex([], project_root=project_root)
    if args[0] in {"-V", "--version"}:
        print(__version__)
        return 0
    if args[0] == "configure":
        return configure_main(args)
    if args[0] == "publish-pr":
        publish_args = list(args[1:])
        if "--project-root" not in publish_args:
            publish_args.extend(["--project-root", str(project_root)])
        return github_workflow_main(publish_args)
    if args[0] == "ui":
        return ui_main(args[1:])
    return launch_codex(args, project_root=project_root)
