from __future__ import annotations

from importlib import import_module
import sys
from typing import Iterable

from mindex import __version__
from mindex.container_mode import container_main, should_default_to_container
from mindex.codoxear import main as codoxear_main
from mindex.launcher import find_project_root, launch_codex


def main(argv: Iterable[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    project_root = find_project_root()
    if not args:
        if should_default_to_container():
            return container_main([], project_root=project_root)
        return launch_codex([], project_root=project_root)
    if args[0] in {"-V", "--version"}:
        print(__version__)
        return 0
    if args[0] == "container":
        return container_main(args[1:], project_root=project_root)
    if args[0] == "configure":
        configure_main = import_module("mindex.configure").main
        return configure_main(args)
    if args[0] == "publish-pr":
        github_workflow_main = import_module("mindex.github_workflow").main
        publish_args = list(args[1:])
        if "--project-root" not in publish_args:
            publish_args.extend(["--project-root", str(project_root)])
        return github_workflow_main(publish_args)
    if args[0] in {"codoxear", "ui"}:
        return codoxear_main(args[1:], invoked_as=args[0])
    return launch_codex(args, project_root=project_root)


if __name__ == "__main__":
    raise SystemExit(main())
