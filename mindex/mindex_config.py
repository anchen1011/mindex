from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


def default_mindex_config_path(*, env: dict[str, str] | None = None) -> Path:
    environ = env if env is not None else os.environ
    configured = environ.get("MINDEX_CONFIG_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".mindex" / "config.json").resolve()


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_private_directory(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


@dataclass(frozen=True)
class SharedFolderMount:
    host: Path
    container: str
    read_only: bool = False


@dataclass(frozen=True)
class PortMappingConfig:
    """
    Port mapping strategy.

    - mode=block: map container ports to a contiguous block on the host.
    - mode=docker-random: let Docker pick random host ports for each container port.
    - mode=static: require explicit host_port mapping for each container port.
    """

    mode: str
    host_ip: str
    container_port_range_start: int
    container_port_count: int
    extra_container_ports: tuple[int, ...]
    host_port_base: int | None
    host_port_range_start: int
    host_port_range_end: int
    static_host_ports: dict[int, int]

    def container_ports(self) -> tuple[int, ...]:
        ports = list(range(self.container_port_range_start, self.container_port_range_start + self.container_port_count))
        ports.extend(self.extra_container_ports)
        # Preserve order but de-dupe.
        seen: set[int] = set()
        ordered: list[int] = []
        for value in ports:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return tuple(ordered)


@dataclass(frozen=True)
class ContainerConfig:
    enabled_by_default: bool
    image_name: str
    image_tag: str
    shared_folders: tuple[SharedFolderMount, ...]
    port_mapping: PortMappingConfig


@dataclass(frozen=True)
class MindexConfig:
    container: ContainerConfig
    config_path: Path


def _parse_shared_mount(value: Any) -> SharedFolderMount | None:
    if isinstance(value, str):
        host_path = Path(value).expanduser()
        return SharedFolderMount(host=host_path, container="/shared", read_only=False)
    if isinstance(value, dict):
        host_raw = value.get("host")
        container_raw = value.get("container")
        if not isinstance(host_raw, str) or not host_raw.strip():
            return None
        if not isinstance(container_raw, str) or not container_raw.strip():
            return None
        ro = bool(value.get("read_only", False))
        return SharedFolderMount(host=Path(host_raw).expanduser(), container=container_raw, read_only=ro)
    return None


def _parse_int(value: Any, *, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError:
            return default
    return default


def _parse_port_list(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    ports: list[int] = []
    for item in value:
        if isinstance(item, int):
            ports.append(item)
        elif isinstance(item, str):
            try:
                ports.append(int(item.strip(), 10))
            except ValueError:
                continue
    return tuple(ports)


def _parse_static_ports(value: Any) -> dict[int, int]:
    if not isinstance(value, dict):
        return {}
    mapping: dict[int, int] = {}
    for key, raw_value in value.items():
        try:
            container_port = int(str(key).strip(), 10)
        except ValueError:
            continue
        if isinstance(raw_value, int):
            host_port = raw_value
        else:
            try:
                host_port = int(str(raw_value).strip(), 10)
            except ValueError:
                continue
        mapping[container_port] = host_port
    return mapping


def _default_payload() -> dict[str, Any]:
    return {
        "container": {
            # Container mode is opt-in.
            "enabled_by_default": False,
            "image": {"name": "mindex-container", "tag": "latest"},
            "shared_folders": [
                # Default shared path: host ~/shared -> container /shared
                {"host": "~/shared", "container": "/shared", "read_only": False},
            ],
            "port_mapping": {
                # "block" keeps ports predictable while still dynamically picking a free block.
                "mode": "block",
                "host_ip": "127.0.0.1",
                # Container-side ports to expose for local dev/testing.
                "container_port_range_start": 3000,
                "container_port_count": 10,
                # Also expose Mindex UI's default port.
                "extra_container_ports": [8765],
                # If set, forces the host port base (base + 0..N-1).
                "host_port_base": None,
                # Dynamic allocation pool for block mode.
                "host_port_range_start": 41000,
                "host_port_range_end": 49000,
                # Only for mode="static": {container_port: host_port}.
                "static_host_ports": {},
            },
        }
    }


def load_mindex_config(
    *,
    config_path: Path | str | None = None,
    env: dict[str, str] | None = None,
    create_if_missing: bool = True,
) -> MindexConfig:
    resolved_path = Path(config_path).expanduser().resolve() if config_path else default_mindex_config_path(env=env)
    payload: dict[str, Any]
    if resolved_path.exists():
        try:
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}
        if create_if_missing:
            _write_private_json(resolved_path, _default_payload())

    defaults = _default_payload()
    container_payload = dict(defaults["container"])
    container_payload.update(payload.get("container", {}) if isinstance(payload.get("container"), dict) else {})

    image_payload = dict(container_payload.get("image", {}) if isinstance(container_payload.get("image"), dict) else {})
    default_image_payload = defaults["container"]["image"]
    image_name = str(image_payload.get("name", default_image_payload["name"])).strip() or default_image_payload["name"]
    image_tag = str(image_payload.get("tag", default_image_payload["tag"])).strip() or default_image_payload["tag"]

    shared_payload = container_payload.get("shared_folders")
    shared_mounts: list[SharedFolderMount] = []
    if isinstance(shared_payload, list):
        for item in shared_payload:
            mount = _parse_shared_mount(item)
            if mount is not None:
                shared_mounts.append(mount)
    if not shared_mounts:
        for item in defaults["container"]["shared_folders"]:
            mount = _parse_shared_mount(item)
            if mount is not None:
                shared_mounts.append(mount)

    port_payload = dict(
        defaults["container"]["port_mapping"]
        | (container_payload.get("port_mapping", {}) if isinstance(container_payload.get("port_mapping"), dict) else {})
    )
    mode = str(port_payload.get("mode", "block")).strip() or "block"
    host_ip = str(port_payload.get("host_ip", "127.0.0.1")).strip() or "127.0.0.1"
    container_range_start = _parse_int(port_payload.get("container_port_range_start"), default=3000)
    container_count = _parse_int(port_payload.get("container_port_count"), default=10)
    extra_ports = _parse_port_list(port_payload.get("extra_container_ports"))
    host_port_base = port_payload.get("host_port_base")
    if host_port_base is not None:
        host_port_base = _parse_int(host_port_base, default=0) or None
    host_pool_start = _parse_int(port_payload.get("host_port_range_start"), default=41000)
    host_pool_end = _parse_int(port_payload.get("host_port_range_end"), default=49000)
    static_ports = _parse_static_ports(port_payload.get("static_host_ports"))

    port_config = PortMappingConfig(
        mode=mode,
        host_ip=host_ip,
        container_port_range_start=container_range_start,
        container_port_count=container_count,
        extra_container_ports=extra_ports,
        host_port_base=host_port_base,
        host_port_range_start=host_pool_start,
        host_port_range_end=host_pool_end,
        static_host_ports=static_ports,
    )

    container_config = ContainerConfig(
        enabled_by_default=bool(container_payload.get("enabled_by_default", False)),
        image_name=image_name,
        image_tag=image_tag,
        shared_folders=tuple(shared_mounts),
        port_mapping=port_config,
    )
    return MindexConfig(container=container_config, config_path=resolved_path)

