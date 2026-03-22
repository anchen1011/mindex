#!/usr/bin/env python3
"""
Tiny fake `docker` CLI for unit/integration testing without Docker installed.

This script is NOT a real container runtime. It emulates just enough behavior
for `mindex.container_mode` tests:
  - docker info
  - docker build -t IMAGE CONTEXT
  - docker image inspect IMAGE
  - docker run -d --name NAME ... -p ... --mount type=volume,... -v ... IMAGE sleep infinity
  - docker container inspect [-f "{{.State.Running}}"] NAME
  - docker start/stop/rm NAME
  - docker exec ... NAME /bin/bash [-lc CMD]

State is stored in JSON under FAKE_DOCKER_ROOT.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import sys
from typing import Any


def _root() -> Path:
    root = os.environ.get("FAKE_DOCKER_ROOT")
    if not root:
        print("FAKE_DOCKER_ROOT is required", file=sys.stderr)
        raise SystemExit(2)
    return Path(root).resolve()


def _state_path() -> Path:
    return _root() / "state.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"images": [], "containers": {}, "used_host_ports": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _container_root(name: str) -> Path:
    return _root() / "containers" / name


def _volume_root(volume_name: str) -> Path:
    return _root() / "volumes" / volume_name


def _ensure_symlink(target: Path, source: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            # Best-effort cleanup for test fixtures.
            for child in sorted(target.rglob("*"), reverse=True):
                if child.is_symlink() or child.is_file():
                    child.unlink(missing_ok=True)
                else:
                    child.rmdir()
            target.rmdir()
    target.symlink_to(source, target_is_directory=source.is_dir())


def _parse_run_args(args: list[str]) -> dict[str, Any]:
    name = None
    publish: list[str] = []
    mounts: list[str] = []
    volumes: list[dict[str, str]] = []
    workdir = None
    env: dict[str, str] = {}

    i = 0
    while i < len(args):
        token = args[i]
        if token == "--name":
            name = args[i + 1]
            i += 2
            continue
        if token == "-p":
            publish.append(args[i + 1])
            i += 2
            continue
        if token == "-v":
            mounts.append(args[i + 1])
            i += 2
            continue
        if token == "--mount":
            volumes.append({"spec": args[i + 1]})
            i += 2
            continue
        if token == "-w":
            workdir = args[i + 1]
            i += 2
            continue
        if token == "-e":
            raw = args[i + 1]
            if "=" in raw:
                key, value = raw.split("=", 1)
                env[key] = value
            i += 2
            continue
        i += 1

    # Image is the first positional arg after options; we don't strictly need it.
    return {"name": name, "publish": publish, "mounts": mounts, "volumes": volumes, "workdir": workdir, "env": env}


def _alloc_random_port(state: dict[str, Any]) -> int:
    used = set(state.get("used_host_ports", []))
    for _ in range(1000):
        candidate = random.randint(41000, 49000)
        if candidate not in used:
            return candidate
    raise RuntimeError("unable to allocate random port")


def _parse_publish_spec(spec: str, state: dict[str, Any]) -> tuple[int, int]:
    """
    Returns (container_port, host_port).
    Accepts:
      - 127.0.0.1:45000:3000
      - 127.0.0.1::3000  (random host port)
    """
    # Remove optional host ip prefix.
    if spec.count(":") >= 2:
        # ip:host:container OR ip::container
        parts = spec.split(":")
        host_part = parts[1]
        container_part = parts[2]
    else:
        raise ValueError(f"unsupported publish spec: {spec}")

    container_port = int(container_part, 10)
    if host_part == "":
        host_port = _alloc_random_port(state)
    else:
        host_port = int(host_part, 10)
    return container_port, host_port


def _make_container_filesystem(name: str, mounts: list[str], volumes: list[str]) -> None:
    root = _container_root(name)
    (root / "usr" / "local" / "bin").mkdir(parents=True, exist_ok=True)
    (root / "opt").mkdir(parents=True, exist_ok=True)

    # Provide a dummy `mindex` that writes evidence into /root/.mindex.
    # This makes it possible to test "entering the container starts mindex"
    # and that shared mounts are readable from inside.
    mindex_path = root / "usr" / "local" / "bin" / "mindex"
    mindex_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "ROOT=\"${FAKE_CONTAINER_ROOT:-}\"\n"
        "if [ -z \"$ROOT\" ]; then\n"
        "  echo \"FAKE_CONTAINER_ROOT is required for fake docker exec\" >&2\n"
        "  exit 2\n"
        "fi\n"
        "mkdir -p \"$ROOT/root/.mindex\"\n"
        "echo \"ran\" > \"$ROOT/root/.mindex/ran.txt\"\n"
        "if [ -f \"$ROOT/shared/test_shared.txt\" ]; then\n"
        "  cat \"$ROOT/shared/test_shared.txt\" > \"$ROOT/root/.mindex/shared_seen.txt\"\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    os.chmod(mindex_path, 0o755)

    # Volume mounts: create volume dirs and link into container root.
    for volume_spec in volumes:
        # spec example: type=volume,source=name,target=/root/.mindex
        parts = dict(item.split("=", 1) for item in volume_spec.split(",") if "=" in item)
        source = parts.get("source")
        target = parts.get("target")
        if not source or not target:
            continue
        volume_dir = _volume_root(source)
        volume_dir.mkdir(parents=True, exist_ok=True)
        _ensure_symlink(root / target.lstrip("/"), volume_dir)

    # Bind mounts: link host path into container root at target.
    for bind_spec in mounts:
        host_raw, target_raw, *_rest = bind_spec.split(":")
        host_path = Path(host_raw).expanduser().resolve()
        target = target_raw
        host_path.mkdir(parents=True, exist_ok=True)
        _ensure_symlink(root / target.lstrip("/"), host_path)


def _cmd_info() -> int:
    sys.stdout.write("Fake Docker\n")
    return 0


def _cmd_image_inspect(state: dict[str, Any], image_ref: str) -> int:
    if image_ref in state.get("images", []):
        sys.stdout.write("[]\n")
        return 0
    return 1


def _cmd_build(state: dict[str, Any], args: list[str]) -> int:
    # docker build -t IMAGE CONTEXT
    if "-t" not in args:
        print("missing -t", file=sys.stderr)
        return 2
    image_ref = args[args.index("-t") + 1]
    images = set(state.get("images", []))
    images.add(image_ref)
    state["images"] = sorted(images)
    _save_state(state)
    return 0


def _cmd_run(state: dict[str, Any], args: list[str]) -> int:
    parsed = _parse_run_args(args)
    name = parsed.get("name")
    if not name:
        print("missing --name", file=sys.stderr)
        return 2

    used = set(state.get("used_host_ports", []))
    ports: dict[str, list[dict[str, str]]] = {}
    publish_specs = parsed.get("publish", [])
    for spec in publish_specs:
        container_port, host_port = _parse_publish_spec(spec, state)
        if host_port in used:
            print("Bind failed: port is already allocated", file=sys.stderr)
            return 1
        used.add(host_port)
        ports[f"{container_port}/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]

    container = {
        "Name": name,
        "State": {"Running": True},
        "NetworkSettings": {"Ports": ports},
    }
    containers = state.get("containers", {})
    containers[name] = container
    state["containers"] = containers
    state["used_host_ports"] = sorted(used)
    _save_state(state)

    # Create fake container filesystem for exec tests.
    mounts = parsed.get("mounts", [])
    volume_specs = [entry["spec"] for entry in parsed.get("volumes", [])]
    _make_container_filesystem(name, mounts, volume_specs)
    return 0


def _cmd_container_inspect(state: dict[str, Any], args: list[str]) -> int:
    containers = state.get("containers", {})
    if args[:2] == ["-f", "{{.State.Running}}"]:
        name = args[2] if len(args) > 2 else ""
        container = containers.get(name)
        if not container:
            return 1
        sys.stdout.write("true\n" if container["State"]["Running"] else "false\n")
        return 0

    name = args[0] if args else ""
    container = containers.get(name)
    if not container:
        return 1
    sys.stdout.write(json.dumps([container]) + "\n")
    return 0


def _cmd_start(state: dict[str, Any], name: str) -> int:
    container = state.get("containers", {}).get(name)
    if not container:
        return 1
    container["State"]["Running"] = True
    _save_state(state)
    return 0


def _cmd_stop(state: dict[str, Any], args: list[str]) -> int:
    # docker stop --timeout 3 NAME
    name = args[-1] if args else ""
    container = state.get("containers", {}).get(name)
    if not container:
        return 1
    container["State"]["Running"] = False
    _save_state(state)
    return 0


def _cmd_rm(state: dict[str, Any], name: str) -> int:
    containers = state.get("containers", {})
    container = containers.pop(name, None)
    if not container:
        return 1
    used = set(state.get("used_host_ports", []))
    ports = container.get("NetworkSettings", {}).get("Ports", {}) or {}
    for value in ports.values():
        if not value:
            continue
        host_port = int(value[0]["HostPort"], 10)
        used.discard(host_port)
    state["used_host_ports"] = sorted(used)
    state["containers"] = containers
    _save_state(state)
    return 0


def _cmd_exec(state: dict[str, Any], args: list[str]) -> int:
    # docker exec ... NAME /bin/bash [-lc CMD]
    # Find container name as the arg just before "/bin/bash".
    if "/bin/bash" not in args:
        return 0
    bash_index = args.index("/bin/bash")
    if bash_index == 0:
        return 2
    name = args[bash_index - 1]
    root = _container_root(name)
    if not root.exists():
        print("missing container root", file=sys.stderr)
        return 1

    # Extract workdir if present.
    workdir = "/"
    if "-w" in args:
        workdir = args[args.index("-w") + 1]

    # Prepare environment; make the container's /usr/local/bin visible.
    env = dict(os.environ)
    env["PATH"] = f"{root / 'usr' / 'local' / 'bin'}:{env.get('PATH','')}"
    env["FAKE_CONTAINER_ROOT"] = str(root)

    # Run the requested command (if any) in a chroot-like way by prefixing paths.
    cmd = None
    if "-lc" in args:
        cmd = args[args.index("-lc") + 1]

    # If the command string tries to drop into an interactive shell, ignore it.
    if cmd and "exec /bin/bash" in cmd:
        cmd = cmd.split("exec /bin/bash", 1)[0].strip(" ;\n\t")

    if not cmd:
        return 0

    # Run with the working directory inside fake container root.
    cwd = root / workdir.lstrip("/")
    cwd.mkdir(parents=True, exist_ok=True)
    # `bash -lc` on the host may override PATH via /etc/profile; force our PATH in-command.
    cmd_with_path = f"export PATH={shlex.quote(env['PATH'])}; {cmd}"
    completed = subprocess.run(["bash", "-lc", cmd_with_path], cwd=str(cwd), env=env, check=False)
    return int(completed.returncode)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 2
    state = _load_state()
    cmd = argv[1]

    if cmd == "info":
        return _cmd_info()
    if cmd == "build":
        return _cmd_build(state, argv[2:])
    if cmd == "image" and len(argv) >= 4 and argv[2] == "inspect":
        return _cmd_image_inspect(state, argv[3])
    if cmd == "run":
        return _cmd_run(state, argv[2:])
    if cmd == "container" and len(argv) >= 4 and argv[2] == "inspect":
        return _cmd_container_inspect(state, argv[3:])
    if cmd == "start":
        return _cmd_start(state, argv[2] if len(argv) > 2 else "")
    if cmd == "stop":
        return _cmd_stop(state, argv[2:])
    if cmd == "rm":
        return _cmd_rm(state, argv[2] if len(argv) > 2 else "")
    if cmd == "exec":
        return _cmd_exec(state, argv[2:])

    print("unsupported fake docker command: " + " ".join(shlex.quote(x) for x in argv[1:]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
