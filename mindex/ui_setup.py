from __future__ import annotations

import json
from pathlib import Path
import sys

from mindex.launcher import find_project_root
from mindex.ui import load_or_create_ui_config


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("mindex-ui-setup does not accept positional arguments. Use `mindex ui ...` for advanced control.", file=sys.stderr)
        return 2

    project_root = find_project_root()
    bootstrap = load_or_create_ui_config(project_root=project_root)
    payload = {
        "project_root": str(Path(project_root).resolve()),
        "config_path": str(bootstrap.config.config_path),
        "host": bootstrap.config.host,
        "port": bootstrap.config.port,
        "username": bootstrap.config.username,
    }
    if bootstrap.generated_password:
        print(f"Generated UI password: {bootstrap.generated_password}", file=sys.stderr)
    print("Mindex UI config ready.", file=sys.stderr)
    print("Next step: run `mindex ui serve` and open the printed URL in your browser.", file=sys.stderr)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

