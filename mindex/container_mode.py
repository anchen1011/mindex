from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from typing import Sequence

from mindex.mindex_config import ContainerConfig, MindexConfig, PortMappingConfig, SharedFolderMount, load_mindex_config


DEFAULT_CONTAINER_WORKDIR = "/workspace"
CONTAINER_LABEL_MANAGED = "mindex.managed"
CONTAINER_LABEL_PROJECT_HASH = "mindex.project_hash"
MAX_PUBLISHED_PORTS = 32


class ContainerError(RuntimeError):
    pass


def in_container(env: dict[str, str] | None = None) -> bool:
    environ = env if env is not None else os.environ
    if environ.get("MINDEX_IN_CONTAINER") == "1":
        return True
    # Docker sets /.dockerenv for typical Linux containers.
    return Path("/.dockerenv").exists()


def container_name_for_project(project_root: Path | str, *, prefix: str = "mindex") -> str:
    resolved = Path(project_root).resolve()
    normalized = resolved.as_posix().rstrip("/")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    project_name = resolved.name or "workspace"
    # Keep it Docker-name friendly.
    project_name = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in project_name)
    return f"{prefix}-{project_name}-{digest}"


def _volume_name(container_name: str, suffix: str) -> str:
    return f"{container_name}-{suffix}"


def _run_docker(
    args: Sequence[str],
    *,
    capture: bool = False,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    completed = subprocess.run(
        ["docker", *args],
        check=check,
        text=True,
        capture_output=capture,
        env=run_env,
    )
    return completed


def check_docker_available(*, env: dict[str, str] | None = None) -> None:
    completed = _run_docker(["info"], capture=True, check=False, env=env)
    if completed.returncode != 0:
        raise ContainerError(
            "Docker is not available. Install and start Docker, then retry.\n"
            f"docker info failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )


def image_exists(image_ref: str, *, env: dict[str, str] | None = None) -> bool:
    completed = _run_docker(["image", "inspect", image_ref], capture=True, check=False, env=env)
    return completed.returncode == 0


def _packaged_dockerfile_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / "container" / "Dockerfile"


def _discover_mindex_source_root() -> Path | None:
    """
    Best-effort: locate a source checkout that contains setup.py.

    When Mindex is installed from a wheel without source, image building must be
    handled externally (or via a future PyPI install flow).
    """
    module_dir = Path(__file__).resolve().parent
    for candidate in (module_dir.parent, *module_dir.parent.parents):
        if (candidate / "setup.py").exists() and (candidate / "mindex").is_dir():
            return candidate
    return None


def _prepare_image_build_context(*, source_root: Path, context_dir: Path) -> None:
    """
    Writes a minimal docker build context:
    - Dockerfile
    - mindex_src (setup.py + mindex package + minimal docs for setup.py)
    """
    context_dir.mkdir(parents=True, exist_ok=True)
    dockerfile_source = _packaged_dockerfile_path()
    if not dockerfile_source.exists():
        raise ContainerError(f"Packaged Dockerfile not found at {dockerfile_source}")
    shutil.copy2(dockerfile_source, context_dir / "Dockerfile")

    src_dir = context_dir / "mindex_src"
    if src_dir.exists():
        shutil.rmtree(src_dir)
    src_dir.mkdir(parents=True, exist_ok=True)

    # Minimal files needed for pip install.
    for name in ("setup.py", "README.md", "HISTORY.md", "LICENSE"):
        path = source_root / name
        if path.exists():
            shutil.copy2(path, src_dir / name)
    shutil.copytree(source_root / "mindex", src_dir / "mindex")


def build_container_image(image_ref: str, *, source_root: Path | None = None, env: dict[str, str] | None = None) -> None:
    check_docker_available(env=env)
    resolved_source = source_root or _discover_mindex_source_root()
    if resolved_source is None:
        raise ContainerError(
            "Unable to locate a Mindex source checkout (setup.py not found). "
            "Set container.image to a prebuilt image, or run from a source checkout."
        )

    with tempfile.TemporaryDirectory(prefix="mindex-container-build-") as tmpdir:
        context_dir = Path(tmpdir)
        _prepare_image_build_context(source_root=resolved_source, context_dir=context_dir)
        completed = subprocess.run(
            ["docker", "build", "-t", image_ref, str(context_dir)],
            check=False,
            env={**os.environ, **(env or {})},
        )
        if completed.returncode != 0:
            raise ContainerError(
                "Failed to build container image.\n"
                f"docker build -t {image_ref} {context_dir}\n"
                "See Docker build output for details."
            )


def ensure_container_image(config: ContainerConfig, *, env: dict[str, str] | None = None) -> None:
    image_ref = f"{config.image_name}:{config.image_tag}"
    if image_exists(image_ref, env=env):
        return
    build_container_image(image_ref, env=env)


def container_exists(container_name: str, *, env: dict[str, str] | None = None) -> bool:
    completed = _run_docker(["container", "inspect", container_name], capture=True, check=False, env=env)
    return completed.returncode == 0


def container_running(container_name: str, *, env: dict[str, str] | None = None) -> bool:
    completed = _run_docker(
        ["container", "inspect", "-f", "{{.State.Running}}", container_name],
        capture=True,
        check=False,
        env=env,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def start_container(container_name: str, *, env: dict[str, str] | None = None) -> None:
    _run_docker(["start", container_name], capture=False, check=False, env=env)


def stop_container(container_name: str, *, timeout_seconds: int = 3, env: dict[str, str] | None = None) -> None:
    _run_docker(["stop", "--timeout", str(timeout_seconds), container_name], capture=False, check=False, env=env)


def remove_container(container_name: str, *, env: dict[str, str] | None = None) -> None:
    _run_docker(["rm", container_name], capture=False, check=False, env=env)


def list_container_port_mappings(container_name: str, *, env: dict[str, str] | None = None) -> dict[int, int]:
    """
    Returns {container_port: host_port} for TCP mappings.
    """
    completed = _run_docker(["container", "inspect", container_name], capture=True, check=False, env=env)
    if completed.returncode != 0:
        return {}
    try:
        import json

        payload = json.loads(completed.stdout)[0]
        ports = payload.get("NetworkSettings", {}).get("Ports", {}) or {}
    except Exception:
        return {}

    mapping: dict[int, int] = {}
    for key, value in ports.items():
        # Key format: "3000/tcp"
        if not isinstance(key, str) or not key.endswith("/tcp"):
            continue
        try:
            container_port = int(key.split("/", 1)[0], 10)
        except ValueError:
            continue
        if not value:
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            host_port_raw = value[0].get("HostPort")
            try:
                host_port = int(str(host_port_raw), 10)
            except ValueError:
                continue
            mapping[container_port] = host_port
    return mapping


def _validate_ports(ports: Sequence[int]) -> None:
    if not ports:
        raise ContainerError("Port mapping configuration has no container ports to expose.")
    if len(ports) > MAX_PUBLISHED_PORTS:
        raise ContainerError(
            f"Refusing to publish {len(ports)} ports (max {MAX_PUBLISHED_PORTS}). "
            "Reduce container_port_count / extra_container_ports in ~/.mindex/config.json."
        )
    for port in ports:
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise ContainerError(f"Invalid port: {port!r}")
    if len(set(ports)) != len(list(ports)):
        raise ContainerError("Port mapping configuration contains duplicate container ports.")


def docker_publish_args(port_config: PortMappingConfig, *, host_port_base: int | None = None) -> list[str]:
    """
    Builds `docker run` publish args (`-p ...`) for the configured container ports.
    """
    container_ports = port_config.container_ports()
    _validate_ports(container_ports)

    mode = port_config.mode.strip().lower()
    host_ip = port_config.host_ip
    publish: list[str] = []

    if mode in {"docker-random", "random", "docker"}:
        for container_port in container_ports:
            publish.extend(["-p", f"{host_ip}::{container_port}"])
        return publish

    if mode in {"static"}:
        seen_host_ports: set[int] = set()
        for container_port in container_ports:
            if container_port not in port_config.static_host_ports:
                raise ContainerError(f"Missing static host port mapping for container port {container_port}.")
            host_port = port_config.static_host_ports[container_port]
            if host_port < 1 or host_port > 65535:
                raise ContainerError(f"Invalid host port for static mapping: {host_port!r}")
            if host_port in seen_host_ports:
                raise ContainerError("Static port mapping reuses the same host port for multiple container ports.")
            seen_host_ports.add(host_port)
            publish.extend(["-p", f"{host_ip}:{host_port}:{container_port}"])
        return publish

    if mode not in {"block"}:
        raise ContainerError(f"Unknown port mapping mode: {port_config.mode!r}")

    base = host_port_base if host_port_base is not None else port_config.host_port_base
    if base is None:
        raise ContainerError("block mode requires a resolved host_port_base (this should be picked dynamically).")
    if base < 1 or base > 65535:
        raise ContainerError(f"Invalid host_port_base: {base!r}")
    if base + len(container_ports) - 1 > 65535:
        raise ContainerError("host_port_base is too high for the configured published port block size.")
    for index, container_port in enumerate(container_ports):
        publish.extend(["-p", f"{host_ip}:{base + index}:{container_port}"])
    return publish


def candidate_host_port_bases(
    *,
    container_name: str,
    pool_start: int,
    pool_end: int,
    block_size: int,
) -> list[int]:
    if pool_start < 1 or pool_end > 65535 or pool_start >= pool_end:
        raise ContainerError("Invalid host port pool range.")
    if block_size < 1:
        raise ContainerError("Invalid block size.")
    if pool_end - pool_start + 1 < block_size:
        raise ContainerError("Host port pool range is smaller than the requested mapping block.")

    total_blocks = (pool_end - pool_start + 1) // block_size
    digest = hashlib.sha256(container_name.encode("utf-8")).hexdigest()
    start_offset = int(digest[:8], 16) % total_blocks

    bases: list[int] = []
    for i in range(total_blocks):
        block_index = (start_offset + i) % total_blocks
        base = pool_start + block_index * block_size
        if base + block_size - 1 <= pool_end:
            bases.append(base)
    return bases


def _looks_like_port_conflict(stderr: str) -> bool:
    lowered = stderr.lower()
    return "port is already allocated" in lowered or "address already in use" in lowered


def create_container(
    *,
    container_name: str,
    config: ContainerConfig,
    project_root: Path,
    env: dict[str, str] | None = None,
) -> None:
    """
    Creates a new long-running container (sleep infinity) with the right mounts/ports.
    """
    container_ports = config.port_mapping.container_ports()
    _validate_ports(container_ports)
    block_size = len(container_ports)

    base_candidates: list[int | None]
    mode = config.port_mapping.mode.strip().lower()
    if mode in {"block"}:
        if config.port_mapping.host_port_base is not None:
            base_candidates = [config.port_mapping.host_port_base]
        else:
            base_candidates = list(
                candidate_host_port_bases(
                    container_name=container_name,
                    pool_start=config.port_mapping.host_port_range_start,
                    pool_end=config.port_mapping.host_port_range_end,
                    block_size=block_size,
                )
            )
    else:
        base_candidates = [None]

    mounts = build_mount_args(project_root, config.shared_folders)
    volume_args = [
        "--mount",
        f"type=volume,source={_volume_name(container_name, 'codex')},target=/root/.codex",
        "--mount",
        f"type=volume,source={_volume_name(container_name, 'mindex')},target=/root/.mindex",
    ]

    image_ref = f"{config.image_name}:{config.image_tag}"
    ensure_container_image(config, env=env)

    for candidate_base in base_candidates:
        publish_args = (
            docker_publish_args(config.port_mapping, host_port_base=candidate_base)
            if mode in {"block"}
            else docker_publish_args(config.port_mapping)
        )
        args = [
            "run",
            "-d",
            "--name",
            container_name,
            "--label",
            f"{CONTAINER_LABEL_MANAGED}=1",
            "--label",
            f"{CONTAINER_LABEL_PROJECT_HASH}={hashlib.sha1(project_root.as_posix().encode('utf-8')).hexdigest()[:8]}",
            "-e",
            "TERM=xterm-256color",
            "-w",
            DEFAULT_CONTAINER_WORKDIR,
            *publish_args,
            *volume_args,
            *mounts,
            image_ref,
            "sleep",
            "infinity",
        ]
        completed = _run_docker(args, capture=True, check=False, env=env)
        if completed.returncode == 0:
            return
        if mode in {"block"} and _looks_like_port_conflict(completed.stderr or ""):
            # Retry with a different base.
            if container_exists(container_name, env=env):
                remove_container(container_name, env=env)
            continue
        raise ContainerError(
            "Failed to create container.\n"
            f"docker {' '.join(shlex.quote(part) for part in args)}\n"
            f"{(completed.stderr or completed.stdout).strip()}"
        )
    raise ContainerError("Failed to allocate a free host port block for this container.")


def build_mount_args(project_root: Path, shared_mounts: Sequence[SharedFolderMount]) -> list[str]:
    mounts: list[str] = []
    mounts.extend(["-v", f"{project_root.as_posix()}:{DEFAULT_CONTAINER_WORKDIR}"])
    for mount in shared_mounts:
        host_path = mount.host.expanduser().resolve()
        # Create missing shared folder directories (default ~/shared should "just work").
        if not host_path.exists():
            host_path.mkdir(parents=True, exist_ok=True)
        spec = f"{host_path.as_posix()}:{mount.container}"
        if mount.read_only:
            spec += ":ro"
        mounts.extend(["-v", spec])
    return mounts


def ensure_container_running(
    *,
    project_root: Path,
    config: ContainerConfig,
    container_name: str,
    env: dict[str, str] | None = None,
) -> None:
    check_docker_available(env=env)
    ensure_container_image(config, env=env)
    if not container_exists(container_name, env=env):
        create_container(container_name=container_name, config=config, project_root=project_root, env=env)
    if not container_running(container_name, env=env):
        start_container(container_name, env=env)


def exec_container_shell(
    container_name: str,
    *,
    workdir: str = DEFAULT_CONTAINER_WORKDIR,
    env: dict[str, str] | None = None,
) -> int:
    completed = subprocess.run(
        [
            "docker",
            "exec",
            "-it",
            "-e",
            "TERM=xterm-256color",
            "-e",
            "MINDEX_IN_CONTAINER=1",
            "-w",
            workdir,
            container_name,
            "/bin/bash",
        ],
        check=False,
        env={**os.environ, **(env or {})},
    )
    return completed.returncode


def exec_container_mindex_then_shell(
    container_name: str,
    *,
    workdir: str = DEFAULT_CONTAINER_WORKDIR,
    mindex_args: Sequence[str] = (),
    env: dict[str, str] | None = None,
) -> int:
    # Run mindex first, then keep the user in the container shell.
    quoted = " ".join(shlex.quote(part) for part in ["mindex", *mindex_args])
    command = f"{quoted}; exec /bin/bash"
    completed = subprocess.run(
        [
            "docker",
            "exec",
            "-it",
            "-e",
            "TERM=xterm-256color",
            "-e",
            "MINDEX_IN_CONTAINER=1",
            "-w",
            workdir,
            container_name,
            "/bin/bash",
            "-lc",
            command,
        ],
        check=False,
        env={**os.environ, **(env or {})},
    )
    return completed.returncode


def container_main(argv: Sequence[str] | None, *, project_root: Path | None = None, env: dict[str, str] | None = None) -> int:
    """
    `mindex container [shell|ports|stop]`
    - `mindex container` enters the project's container and launches `mindex` immediately.
    """
    args = list(argv or [])
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    config = load_mindex_config(env=run_env).container
    resolved_root = (project_root or Path.cwd()).resolve()
    name = container_name_for_project(resolved_root)

    if args and args[0] == "stop":
        if container_exists(name, env=run_env) and container_running(name, env=run_env):
            stop_container(name, env=run_env)
        return 0

    if args and args[0] == "build":
        image_ref = f"{config.image_name}:{config.image_tag}"
        build_container_image(image_ref, env=run_env)
        print(f"[mindex container] Built image {image_ref}")
        return 0

    if args and args[0] == "ports":
        ensure_container_running(project_root=resolved_root, config=config, container_name=name, env=run_env)
        mapping = list_container_port_mappings(name, env=run_env)
        for container_port in sorted(mapping):
            print(f"{container_port} -> {mapping[container_port]}")
        return 0

    if args and args[0] == "shell":
        ensure_container_running(project_root=resolved_root, config=config, container_name=name, env=run_env)
        return exec_container_shell(name, env=run_env)

    # Default: enter container and run mindex, then keep the user in a container shell.
    ensure_container_running(project_root=resolved_root, config=config, container_name=name, env=run_env)
    mapping = list_container_port_mappings(name, env=run_env)
    if mapping:
        # Print a compact mapping hint for external testing.
        pairs = ", ".join(f"{cp}->{mapping[cp]}" for cp in sorted(mapping))
        print(f"[mindex container] Port mappings (container->host): {pairs}")
    return exec_container_mindex_then_shell(name, mindex_args=(), env=run_env)


def should_default_to_container(*, env: dict[str, str] | None = None) -> bool:
    if in_container(env=env):
        return False
    config = load_mindex_config(env=env, create_if_missing=False).container
    return config.enabled_by_default
