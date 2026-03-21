from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from typing import Any, Iterable

from mindex.codex_home import default_managed_codex_home, default_managed_logs_root
from mindex.logging_utils import append_action, create_log_run, record_validation, write_status


PASSWORD_ITERATIONS = 390000
DEFAULT_HOST_LOCAL = "127.0.0.1"
DEFAULT_PORT = 8743
DEFAULT_CODEX_BIN = "mindex"
# Pin to a known-good commit by default for reproducible installs.
# Users can override with `--source` if they want to track upstream.
DEFAULT_INSTALL_URL = "git+https://github.com/yiwenlu66/codoxear@8ba8690a4badaf7a5cff3c5a00e9f6ab39647770"


@dataclass(frozen=True)
class CodoxearConfig:
    host: str
    port: int
    url_prefix: str
    allow_remote: bool
    password_hash: str
    password_salt: str
    password_iterations: int
    codex_home: str
    codex_bin: str
    config_path: Path


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


def _hash_password(password: str, *, salt: bytes, iterations: int) -> str:
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return derived.hex()


def _verify_password(password: str, *, expected_hash: str, salt_hex: str, iterations: int) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    actual = _hash_password(password, salt=salt, iterations=iterations)
    return hmac.compare_digest(actual, expected_hash)


def _default_config_path(env: dict[str, str] | None = None) -> Path:
    environ = env if env is not None else os.environ
    configured = environ.get("MINDEX_CODOXEAR_CONFIG_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".mindex" / "codoxear" / "config.json").resolve()


def _default_venv_dir(env: dict[str, str] | None = None) -> Path:
    environ = env if env is not None else os.environ
    configured = environ.get("MINDEX_CODOXEAR_VENV_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".mindex" / "codoxear" / "venv").resolve()


def _normalize_url_prefix(value: str) -> str:
    prefix = value.strip()
    if not prefix:
        return ""
    if not prefix.startswith("/"):
        raise ValueError("url_prefix must be empty or start with '/'")
    return prefix.rstrip("/")


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1", "[::1]"}


def _assert_bind_is_allowed(host: str, *, allow_remote: bool) -> None:
    if allow_remote:
        return
    normalized = host.strip().lower()
    if normalized in {"0.0.0.0", "::", "[::]"}:
        raise ValueError("refusing to bind to all interfaces without allow_remote=true")
    if not _is_loopback_host(normalized):
        raise ValueError("refusing to bind to a non-loopback host without allow_remote=true")


def _build_config_payload(
    *,
    config_path: Path,
    host: str,
    port: int,
    url_prefix: str,
    allow_remote: bool,
    password: str,
    codex_home: str,
    codex_bin: str,
) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(password, salt=salt, iterations=PASSWORD_ITERATIONS)
    return {
        "auth": {
            "password_hash": password_hash,
            "password_salt": salt.hex(),
            "password_iterations": PASSWORD_ITERATIONS,
        },
        "server": {
            "host": host,
            "port": port,
            "url_prefix": url_prefix,
            "allow_remote": allow_remote,
        },
        "codex": {
            "home": codex_home,
            "bin": codex_bin,
        },
        "_meta": {
            "config_path": str(config_path),
        },
    }


def _sanitize_argv_for_logging(argv: list[str]) -> list[str]:
    """Redact secrets from argv before writing to logs."""
    sanitized: list[str] = []
    idx = 0
    while idx < len(argv):
        item = argv[idx]
        if item == "--password":
            sanitized.append("--password")
            if idx + 1 < len(argv):
                sanitized.append("****")
                idx += 2
                continue
            idx += 1
            continue
        if item.startswith("--password="):
            sanitized.append("--password=****")
            idx += 1
            continue
        sanitized.append(item)
        idx += 1
    return sanitized


def _normalize_legacy_ui_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Best-effort compatibility for older `mindex ui` flags."""
    warnings: list[str] = []
    args = list(argv)

    # Historical flags we ignore (they were repo-root/UI-dev specifics for the removed UI).
    def _drop_flag(flag: str, *, takes_value: bool) -> None:
        nonlocal args
        if flag not in args:
            return
        out: list[str] = []
        skip_next = False
        for token in args:
            if skip_next:
                skip_next = False
                continue
            if token == flag:
                warnings.append(f"ignoring legacy flag: {flag}")
                if takes_value:
                    skip_next = True
                continue
            out.append(token)
        args = out

    _drop_flag("--project-root", takes_value=True)
    _drop_flag("--dev", takes_value=False)

    # Older versions supported `mindex ui --init-only` as a shortcut for init-config.
    if "--init-only" in args:
        warnings.append("treating legacy `--init-only` as `init-config`")
        args = [token for token in args if token != "--init-only"]
        if not args or args[0].startswith("-"):
            args = ["init-config", *args]
        elif args[0] not in {"install", "init-config", "reset-config", "serve", "broker"}:
            args = ["init-config", *args]

    return args, warnings


def load_config(*, env: dict[str, str] | None = None) -> CodoxearConfig:
    config_path = _default_config_path(env)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    auth = payload.get("auth") or {}
    server = payload.get("server") or {}
    codex = payload.get("codex") or {}
    return CodoxearConfig(
        host=str(server.get("host") or DEFAULT_HOST_LOCAL),
        port=int(server.get("port") or DEFAULT_PORT),
        url_prefix=str(server.get("url_prefix") or ""),
        allow_remote=bool(server.get("allow_remote") or False),
        password_hash=str(auth.get("password_hash") or ""),
        password_salt=str(auth.get("password_salt") or ""),
        password_iterations=int(auth.get("password_iterations") or PASSWORD_ITERATIONS),
        codex_home=str(codex.get("home") or str(default_managed_codex_home())),
        codex_bin=str(codex.get("bin") or DEFAULT_CODEX_BIN),
        config_path=config_path,
    )


def write_config(payload: dict[str, Any], *, env: dict[str, str] | None = None) -> Path:
    config_path = _default_config_path(env)
    _write_private_json(config_path, payload)
    return config_path


def _build_server_env(config: CodoxearConfig, *, password: str) -> dict[str, str]:
    env = os.environ.copy()
    env["CODEX_WEB_PASSWORD"] = password
    env["CODEX_WEB_HOST"] = config.host
    env["CODEX_WEB_PORT"] = str(config.port)
    env["CODEX_WEB_URL_PREFIX"] = config.url_prefix
    env["CODEX_HOME"] = config.codex_home
    env["CODEX_BIN"] = config.codex_bin
    return env


def _codoxear_server_command() -> list[str]:
    return ["codoxear-server"]


def _find_codoxear_server(*, env: dict[str, str] | None = None) -> str | None:
    venv_bin = _default_venv_dir(env) / "bin" / "codoxear-server"
    if venv_bin.exists():
        return str(venv_bin)
    return shutil.which("codoxear-server")


def _find_codoxear_broker(*, env: dict[str, str] | None = None) -> str | None:
    venv_bin = _default_venv_dir(env) / "bin" / "codoxear-broker"
    if venv_bin.exists():
        return str(venv_bin)
    return shutil.which("codoxear-broker")


def _run_install(argv: list[str], *, env: dict[str, str] | None, log_run) -> int:
    parser = argparse.ArgumentParser(prog="mindex codoxear install")
    parser.add_argument(
        "--venv",
        default=str(_default_venv_dir(env)),
        help="Install into this venv (default: ~/.mindex/codoxear/venv).",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_INSTALL_URL,
        help="pip install target (default: git+https://github.com/yiwenlu66/codoxear).",
    )
    args = parser.parse_args(argv)

    venv_dir = Path(args.venv).expanduser().resolve()
    venv_python = venv_dir / "bin" / "python"

    if not venv_python.exists():
        create_venv = [sys.executable, "-m", "venv", str(venv_dir)]
        append_action(log_run, f"run: {' '.join(create_venv)}")
        completed = subprocess.run(create_venv, text=True, capture_output=True, check=False)
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            record_validation(
                log_run,
                command=create_venv,
                returncode=completed.returncode,
                passed=False,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            write_status(log_run, "error", message="Failed to create venv for Codoxear install")
            return completed.returncode

    upgrade_pip = [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"]
    append_action(log_run, f"run: {' '.join(upgrade_pip)}")
    subprocess.run(upgrade_pip, check=False)

    install = [str(venv_python), "-m", "pip", "install", "--upgrade", args.source]
    append_action(log_run, f"run: {' '.join(install)}")
    completed = subprocess.run(install, text=True, capture_output=True, check=False)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    record_validation(
        log_run,
        command=install,
        returncode=completed.returncode,
        passed=completed.returncode == 0,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if completed.returncode != 0:
        write_status(log_run, "error", message="Codoxear install failed")
        return completed.returncode

    server_bin = venv_dir / "bin" / "codoxear-server"
    broker_bin = venv_dir / "bin" / "codoxear-broker"
    print(f"Installed Codoxear into venv: {venv_dir}")
    if server_bin.exists():
        print(f"Server binary: {server_bin}")
    if broker_bin.exists():
        print(f"Broker binary: {broker_bin}")
    return 0


def _prompt_password(*, label: str = "Password") -> str:
    value = getpass.getpass(f"{label}: ")
    if not value:
        raise ValueError("password cannot be empty")
    return value


def _init_or_reset_config(
    *,
    argv: list[str],
    env: dict[str, str] | None,
    overwrite: bool,
    invoked_as: str,
) -> int:
    parser = argparse.ArgumentParser(prog=f"mindex {invoked_as} {'reset-config' if overwrite else 'init-config'}")
    parser.add_argument("--host", default=DEFAULT_HOST_LOCAL)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--url-prefix", default="")
    parser.add_argument("--allow-remote", action="store_true", default=False)
    parser.add_argument("--password", default="")
    parser.add_argument("--codex-home", default=str(default_managed_codex_home()))
    parser.add_argument("--codex-bin", default=DEFAULT_CODEX_BIN)
    args = parser.parse_args(argv)

    config_path = _default_config_path(env)
    if config_path.exists() and not overwrite:
        raise SystemExit(f"Config already exists at {config_path}. Use reset-config to overwrite.")

    url_prefix = _normalize_url_prefix(args.url_prefix)
    _assert_bind_is_allowed(args.host, allow_remote=args.allow_remote)

    if args.password:
        # Even though we redact logs, avoid encouraging plaintext secrets in shell history.
        print("warning: avoid using --password on a shared machine; prefer the interactive prompt.", file=sys.stderr)
    password = args.password or _prompt_password(label="Codoxear UI password")
    payload = _build_config_payload(
        config_path=config_path,
        host=args.host,
        port=args.port,
        url_prefix=url_prefix,
        allow_remote=args.allow_remote,
        password=password,
        codex_home=args.codex_home,
        codex_bin=args.codex_bin,
    )
    write_config(payload, env=env)
    print(f"Wrote Codoxear config to {config_path}")
    return 0


def _serve(argv: list[str], *, env: dict[str, str] | None, invoked_as: str) -> int:
    parser = argparse.ArgumentParser(prog=f"mindex {invoked_as} serve")
    parser.add_argument("--password", default="", help="Codoxear UI password (not stored).")
    parser.add_argument("--no-verify", action="store_true", default=False, help="Skip password hash verification.")
    args = parser.parse_args(argv)

    config = load_config(env=env)
    config = replace(config, url_prefix=_normalize_url_prefix(config.url_prefix))
    _assert_bind_is_allowed(config.host, allow_remote=config.allow_remote)

    server_bin = _find_codoxear_server(env=env)
    if server_bin is None:
        raise SystemExit(
            "codoxear-server not found. Run `mindex codoxear install` first (installs into ~/.mindex/codoxear/venv)."
        )

    if args.password:
        print("warning: avoid using --password on a shared machine; prefer the interactive prompt.", file=sys.stderr)
    password = args.password or _prompt_password(label="Codoxear UI password")
    if not args.no_verify:
        if not _verify_password(
            password,
            expected_hash=config.password_hash,
            salt_hex=config.password_salt,
            iterations=config.password_iterations,
        ):
            raise SystemExit("Invalid password for Codoxear config.")

    env_map = _build_server_env(config, password=password)
    return subprocess.run([server_bin], env=env_map, check=False).returncode


def _broker(argv: list[str], *, env: dict[str, str] | None, invoked_as: str) -> int:
    parser = argparse.ArgumentParser(prog=f"mindex {invoked_as} broker")
    parser.add_argument(
        "codex_args",
        nargs=argparse.REMAINDER,
        help="Arguments for Codex (prefix with `--` to match codoxear-broker usage).",
    )
    args = parser.parse_args(argv)

    broker_bin = _find_codoxear_broker(env=env)
    if broker_bin is None:
        raise SystemExit(
            "codoxear-broker not found. Run `mindex codoxear install` first (installs into ~/.mindex/codoxear/venv)."
        )

    config = load_config(env=env)
    codex_args = list(args.codex_args)
    if codex_args and codex_args[0] == "--":
        codex_args = codex_args[1:]

    # Broker does not need the web password; it just registers the terminal
    # session so Codoxear can discover and attach to it.
    env_map = os.environ.copy()
    env_map["CODEX_HOME"] = config.codex_home
    env_map["CODEX_BIN"] = config.codex_bin
    return subprocess.run([broker_bin, "--", *codex_args], env=env_map, check=False).returncode


def main(argv: Iterable[str] | None = None, *, invoked_as: str = "codoxear") -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    env = None

    if invoked_as == "ui":
        # Keep backwards compatibility for older workflows, but shift everything
        # to Codoxear since the in-tree Mindex UI has been removed.
        args, legacy_warnings = _normalize_legacy_ui_args(args)
        for warning in legacy_warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if args and args[0] in {"init-config", "reset-config", "serve", "install", "broker"}:
            pass
        elif args:
            raise SystemExit(
                "mindex ui now proxies to Codoxear. Use: mindex ui serve|init-config|reset-config|install|broker"
            )
        else:
            args = ["serve"]

    if not args:
        raise SystemExit(f"Usage: mindex {invoked_as} <install|init-config|reset-config|serve|broker>")

    sub = args[0]
    tail = args[1:]

    logs_root = default_managed_logs_root()
    sanitized_args = _sanitize_argv_for_logging(args)
    log_run = create_log_run(
        logs_root,
        f"{invoked_as} {sub}",
        prompt_text=f"mindex {invoked_as} {' '.join(sanitized_args)}",
        metadata={"invoked_as": invoked_as, "subcommand": sub},
    )

    try:
        write_status(log_run, "running")
        if sub == "install":
            rc = _run_install(tail, env=env, log_run=log_run)
        elif sub == "init-config":
            rc = _init_or_reset_config(argv=tail, env=env, overwrite=False, invoked_as=invoked_as)
        elif sub == "reset-config":
            rc = _init_or_reset_config(argv=tail, env=env, overwrite=True, invoked_as=invoked_as)
        elif sub == "serve":
            rc = _serve(tail, env=env, invoked_as=invoked_as)
        elif sub == "broker":
            rc = _broker(tail, env=env, invoked_as=invoked_as)
        else:
            raise SystemExit(f"Unknown subcommand: {sub}")
        write_status(log_run, "ok", returncode=rc)
        return rc
    except SystemExit as exc:
        message = str(exc)
        write_status(log_run, "error", message=message)
        raise
    except Exception as exc:  # pragma: no cover - defensive
        write_status(log_run, "error", message=str(exc))
        raise
