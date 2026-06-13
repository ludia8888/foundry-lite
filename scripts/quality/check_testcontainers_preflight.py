"""Fail fast when PostgreSQL Testcontainers cannot reach Docker."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

COLIMA_SOCKET = ".colima/default/docker.sock"
TESTCONTAINERS_SOCKET_OVERRIDE = "/var/run/docker.sock"

ConnectTcp = Callable[[str, int], bool]
ConnectUnix = Callable[[Path], bool]


@dataclass(frozen=True)
class PreflightResult:
    is_ok: bool
    code: str
    message: str


def check_preflight(
    env: Mapping[str, str],
    *,
    home: Path,
    connect_unix: ConnectUnix | None = None,
    connect_tcp: ConnectTcp | None = None,
) -> PreflightResult:
    unix_checker = connect_unix or _connect_unix_socket
    tcp_checker = connect_tcp or _connect_tcp_socket
    docker_host = env.get("DOCKER_HOST", "").strip()
    if docker_host:
        return _check_docker_host(docker_host, env, unix_checker, tcp_checker)
    return _check_default_socket(env, home, unix_checker)


def write_report(output: Path, result: PreflightResult) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _check_default_socket(env: Mapping[str, str], home: Path, connect_unix: ConnectUnix) -> PreflightResult:
    default_socket = Path("/var/run/docker.sock")
    if _reachable_unix_socket(default_socket, connect_unix):
        return _ok(f"Docker socket reachable at {default_socket}")
    colima_socket = home / COLIMA_SOCKET
    if _reachable_unix_socket(colima_socket, connect_unix):
        return _colima_env_required(colima_socket, env)
    return _docker_unavailable(colima_socket)


def _check_docker_host(
    docker_host: str,
    env: Mapping[str, str],
    connect_unix: ConnectUnix,
    connect_tcp: ConnectTcp,
) -> PreflightResult:
    parsed = urlparse(docker_host)
    if parsed.scheme == "unix":
        return _check_unix_docker_host(Path(parsed.path), docker_host, env, connect_unix)
    if parsed.scheme in {"tcp", "http", "https"}:
        return _check_tcp_docker_host(parsed.hostname, parsed.port, docker_host, connect_tcp)
    return _failure(
        "unsupported_docker_host",
        f"Unsupported DOCKER_HOST '{docker_host}'. Use unix://... or tcp://host:port for Testcontainers.",
    )


def _check_unix_docker_host(
    socket_path: Path,
    docker_host: str,
    env: Mapping[str, str],
    connect_unix: ConnectUnix,
) -> PreflightResult:
    if not _reachable_unix_socket(socket_path, connect_unix):
        return _failure(
            "docker_host_socket_unreachable",
            _socket_unreachable_message(socket_path, docker_host),
        )
    if _is_colima_socket(socket_path) and not _has_testcontainers_override(env):
        return _failure(
            "missing_testcontainers_socket_override",
            _colima_override_message(socket_path),
        )
    return _ok(f"Docker socket reachable through DOCKER_HOST={docker_host}")


def _check_tcp_docker_host(
    host: str | None,
    port: int | None,
    docker_host: str,
    connect_tcp: ConnectTcp,
) -> PreflightResult:
    if host is None or port is None:
        return _failure("invalid_tcp_docker_host", f"DOCKER_HOST '{docker_host}' must include host and port.")
    if not connect_tcp(host, port):
        return _failure("tcp_docker_unreachable", f"Cannot connect to Docker at DOCKER_HOST={docker_host}.")
    return _ok(f"Docker TCP endpoint reachable through DOCKER_HOST={docker_host}")


def _reachable_unix_socket(path: Path, connect_unix: ConnectUnix) -> bool:
    return path.exists() and connect_unix(path)


def _connect_unix_socket(path: Path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(str(path))
        return True
    except OSError:
        return False


def _connect_tcp_socket(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _has_testcontainers_override(env: Mapping[str, str]) -> bool:
    return env.get("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE") == TESTCONTAINERS_SOCKET_OVERRIDE


def _is_colima_socket(path: Path) -> bool:
    return ".colima" in path.parts


def _colima_env_required(colima_socket: Path, env: Mapping[str, str]) -> PreflightResult:
    if _has_testcontainers_override(env):
        return _failure("missing_docker_host", _colima_docker_host_message(colima_socket))
    return _failure("missing_colima_env", _colima_full_env_message(colima_socket))


def _docker_unavailable(colima_socket: Path) -> PreflightResult:
    return _failure(
        "docker_unavailable",
        "\n".join(
            [
                "PostgreSQL Testcontainers preflight failed before running pytest.",
                "Docker is not reachable from this shell.",
                "If you use Colima, start it and run:",
                f"  export DOCKER_HOST=unix://{colima_socket}",
                f"  export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE={TESTCONTAINERS_SOCKET_OVERRIDE}",
                "  pnpm --silent ci:gate",
            ]
        ),
    )


def _colima_full_env_message(colima_socket: Path) -> str:
    return "\n".join(
        [
            "PostgreSQL Testcontainers preflight failed before running pytest.",
            f"Found a reachable Colima socket at {colima_socket}, but this shell is not configured for it.",
            "Run:",
            f"  export DOCKER_HOST=unix://{colima_socket}",
            f"  export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE={TESTCONTAINERS_SOCKET_OVERRIDE}",
            "  pnpm --silent ci:gate",
        ]
    )


def _colima_docker_host_message(colima_socket: Path) -> str:
    return "\n".join(
        [
            "PostgreSQL Testcontainers preflight failed before running pytest.",
            "TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE is set, but DOCKER_HOST is missing.",
            "Run:",
            f"  export DOCKER_HOST=unix://{colima_socket}",
            "  pnpm --silent ci:gate",
        ]
    )


def _colima_override_message(socket_path: Path) -> str:
    return "\n".join(
        [
            "PostgreSQL Testcontainers preflight failed before running pytest.",
            f"DOCKER_HOST points at Colima ({socket_path}), but Testcontainers also needs:",
            f"  export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE={TESTCONTAINERS_SOCKET_OVERRIDE}",
            "Then rerun:",
            "  pnpm --silent ci:gate",
        ]
    )


def _socket_unreachable_message(socket_path: Path, docker_host: str) -> str:
    return "\n".join(
        [
            "PostgreSQL Testcontainers preflight failed before running pytest.",
            f"DOCKER_HOST is set to {docker_host}, but {socket_path} is not reachable.",
            "Start Docker or Colima, then rerun the gate.",
        ]
    )


def _ok(message: str) -> PreflightResult:
    return PreflightResult(is_ok=True, code="ok", message=message)


def _failure(code: str, message: str) -> PreflightResult:
    return PreflightResult(is_ok=False, code=code, message=message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Docker access before PostgreSQL Testcontainers run.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/quality/testcontainers_preflight.json"))
    args = parser.parse_args()
    result = check_preflight(os.environ, home=Path.home())
    write_report(args.output, result)
    if result.is_ok:
        print(f"PostgreSQL Testcontainers preflight OK: {result.message}")
        return 0
    print(result.message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
