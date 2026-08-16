"""Fail fast when Testcontainers cannot reach Docker."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess  # nosec B404 -- this gate invokes only the fixed Docker CLI without a shell.
import sys
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

COLIMA_SOCKET = ".colima/default/docker.sock"
TESTCONTAINERS_SOCKET_OVERRIDE = "/var/run/docker.sock"
DOCKER_CONFIG_PATH = ".docker/config.json"
DOCKER_DESKTOP_CREDENTIAL_DIRECTORY = Path("/Applications/Docker.app/Contents/Resources/bin")
TESTCONTAINERS_REAPER_IMAGE = "testcontainers/ryuk:0.8.1"
MINIMUM_DOCKER_MEMORY_BYTES = 4 * 1024**3
MINIMUM_DOCKER_AVAILABLE_STORAGE_KIB = 12 * 1024**2

ConnectTcp = Callable[[str, int], bool]
ConnectUnix = Callable[[Path], bool]
FindExecutable = Callable[[str, str | None], str | None]


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
    find_executable: FindExecutable | None = None,
) -> PreflightResult:
    unix_checker = connect_unix or _connect_unix_socket
    tcp_checker = connect_tcp or _connect_tcp_socket
    docker_host = env.get("DOCKER_HOST", "").strip()
    if docker_host:
        socket_result = _check_docker_host(docker_host, env, unix_checker, tcp_checker)
    else:
        socket_result = _check_default_socket(env, home, unix_checker)
    if not socket_result.is_ok:
        return socket_result
    return _check_credential_helper(env, home, socket_result, find_executable or _find_executable)


def configure_docker_desktop_credential_path(
    env: MutableMapping[str, str],
    *,
    credential_directory: Path = DOCKER_DESKTOP_CREDENTIAL_DIRECTORY,
    find_executable: FindExecutable | None = None,
) -> None:
    executable_finder = find_executable or _find_executable
    current_path = env.get("PATH")
    if executable_finder("docker-credential-desktop", current_path) is not None:
        return
    credential_helper = credential_directory / "docker-credential-desktop"
    if not credential_helper.is_file():
        return
    env["PATH"] = f"{credential_directory}{os.pathsep}{current_path or ''}"


def write_report(output: Path, result: PreflightResult) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_runtime_capacity(*, total_memory_bytes: int, available_storage_kib: int) -> PreflightResult:
    failures: list[str] = []
    if total_memory_bytes < MINIMUM_DOCKER_MEMORY_BYTES:
        failures.append(
            f"memory {total_memory_bytes / 1024**3:.1f} GiB < {MINIMUM_DOCKER_MEMORY_BYTES / 1024**3:.0f} GiB"
        )
    if available_storage_kib < MINIMUM_DOCKER_AVAILABLE_STORAGE_KIB:
        failures.append(
            "available storage "
            f"{available_storage_kib / 1024**2:.1f} GiB < {MINIMUM_DOCKER_AVAILABLE_STORAGE_KIB / 1024**2:.0f} GiB"
        )
    if not failures:
        return _ok("Docker runtime has enough memory and storage for the full Testcontainers suite")
    return _failure(
        "docker_runtime_capacity_insufficient",
        "\n".join(
            [
                "Testcontainers preflight failed before running pytest.",
                f"Docker runtime capacity is too small: {'; '.join(failures)}.",
                "Increase the Docker VM memory/storage allocation or use a larger Docker/Colima runtime.",
            ]
        ),
    )


def probe_runtime_capacity(env: Mapping[str, str]) -> PreflightResult:
    memory = _run_docker_probe(("info", "--format", "{{.MemTotal}}"), env=env, timeout_seconds=30)
    if memory.returncode != 0:
        return _probe_failure("docker info", memory.stderr)
    storage = _run_docker_probe(
        ("run", "--rm", "--entrypoint", "df", TESTCONTAINERS_REAPER_IMAGE, "-Pk", "/"),
        env=env,
        timeout_seconds=120,
    )
    if storage.returncode != 0:
        return _probe_failure(f"docker run {TESTCONTAINERS_REAPER_IMAGE}", storage.stderr)
    try:
        return check_runtime_capacity(
            total_memory_bytes=int(memory.stdout.strip()),
            available_storage_kib=_parse_available_storage_kib(storage.stdout),
        )
    except (ValueError, IndexError):
        return _probe_failure("Docker runtime capacity parse", "unexpected Docker probe output")


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


def _run_docker_probe(
    arguments: tuple[str, ...],
    *,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # nosec B603 -- executable is fixed and arguments are an argv tuple, never a shell command.
            ("docker", *arguments),
            capture_output=True,
            text=True,
            check=False,
            env=dict(env),
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(("docker", *arguments), 1, "", str(exc))


def _parse_available_storage_kib(output: str) -> int:
    rows = [line.split() for line in output.splitlines() if line.strip()]
    return int(rows[-1][3])


def _probe_failure(operation: str, detail: str) -> PreflightResult:
    bounded_detail = " ".join(detail.strip().split())[:240] or "no error detail"
    return _failure(
        "docker_runtime_probe_failed",
        "\n".join(
            [
                "Testcontainers preflight failed before running pytest.",
                f"{operation} failed: {bounded_detail}",
                "Verify Docker image-pull credentials and daemon health, then rerun the gate.",
            ]
        ),
    )


def _find_executable(name: str, path: str | None) -> str | None:
    return shutil.which(name, path=path)


def _check_credential_helper(
    env: Mapping[str, str],
    home: Path,
    socket_result: PreflightResult,
    find_executable: FindExecutable,
) -> PreflightResult:
    helper_name = _configured_credential_helper(home)
    if helper_name is None or find_executable(helper_name, env.get("PATH")) is not None:
        return socket_result
    return _failure(
        "docker_credential_helper_unavailable",
        "\n".join(
            [
                "Testcontainers preflight failed before running pytest.",
                f"Docker config requires '{helper_name}', but it is not available on PATH.",
                "Docker may appear reachable until Testcontainers needs to pull a missing image.",
                "Install the configured helper or add its directory to PATH, then rerun the gate.",
            ]
        ),
    )


def _configured_credential_helper(home: Path) -> str | None:
    config_path = home / DOCKER_CONFIG_PATH
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    store = payload.get("credsStore")
    if not isinstance(store, str) or not store.strip():
        return None
    return f"docker-credential-{store.strip()}"


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
                "Testcontainers preflight failed before running pytest.",
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
            "Testcontainers preflight failed before running pytest.",
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
            "Testcontainers preflight failed before running pytest.",
            "TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE is set, but DOCKER_HOST is missing.",
            "Run:",
            f"  export DOCKER_HOST=unix://{colima_socket}",
            "  pnpm --silent ci:gate",
        ]
    )


def _colima_override_message(socket_path: Path) -> str:
    return "\n".join(
        [
            "Testcontainers preflight failed before running pytest.",
            f"DOCKER_HOST points at Colima ({socket_path}), but Testcontainers also needs:",
            f"  export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE={TESTCONTAINERS_SOCKET_OVERRIDE}",
            "Then rerun:",
            "  pnpm --silent ci:gate",
        ]
    )


def _socket_unreachable_message(socket_path: Path, docker_host: str) -> str:
    return "\n".join(
        [
            "Testcontainers preflight failed before running pytest.",
            f"DOCKER_HOST is set to {docker_host}, but {socket_path} is not reachable.",
            "Start Docker or Colima, then rerun the gate.",
        ]
    )


def _ok(message: str) -> PreflightResult:
    return PreflightResult(is_ok=True, code="ok", message=message)


def _failure(code: str, message: str) -> PreflightResult:
    return PreflightResult(is_ok=False, code=code, message=message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Docker access before Testcontainers run.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/quality/testcontainers_preflight.json"))
    args = parser.parse_args()
    configure_docker_desktop_credential_path(os.environ)
    result = check_preflight(os.environ, home=Path.home())
    if result.is_ok:
        result = probe_runtime_capacity(os.environ)
    write_report(args.output, result)
    if result.is_ok:
        print(f"Testcontainers preflight OK: {result.message}")
        return 0
    print(result.message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
