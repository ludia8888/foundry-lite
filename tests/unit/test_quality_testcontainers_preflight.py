from __future__ import annotations

from pathlib import Path

from scripts.quality.check_testcontainers_preflight import (
    MINIMUM_DOCKER_AVAILABLE_STORAGE_KIB,
    MINIMUM_DOCKER_MEMORY_BYTES,
    TESTCONTAINERS_SOCKET_OVERRIDE,
    _parse_available_storage_kib,
    check_preflight,
    check_runtime_capacity,
    configure_docker_desktop_credential_path,
)


def test_preflight_requires_colima_env_when_socket_is_reachable(tmp_path: Path) -> None:
    colima_socket = _touch_colima_socket(tmp_path)

    result = check_preflight(
        {},
        home=tmp_path,
        connect_unix=lambda path: path == colima_socket,
    )

    assert result.is_ok is False
    assert result.code == "missing_colima_env"
    assert f"export DOCKER_HOST=unix://{colima_socket}" in result.message
    assert f"export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE={TESTCONTAINERS_SOCKET_OVERRIDE}" in result.message


def test_preflight_requires_docker_host_when_only_testcontainers_override_is_set(tmp_path: Path) -> None:
    colima_socket = _touch_colima_socket(tmp_path)

    result = check_preflight(
        {"TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE": TESTCONTAINERS_SOCKET_OVERRIDE},
        home=tmp_path,
        connect_unix=lambda path: path == colima_socket,
    )

    assert result.is_ok is False
    assert result.code == "missing_docker_host"
    assert f"export DOCKER_HOST=unix://{colima_socket}" in result.message


def test_preflight_requires_testcontainers_override_for_colima_docker_host(tmp_path: Path) -> None:
    colima_socket = _touch_colima_socket(tmp_path)

    result = check_preflight(
        {"DOCKER_HOST": f"unix://{colima_socket}"},
        home=tmp_path,
        connect_unix=lambda path: path == colima_socket,
    )

    assert result.is_ok is False
    assert result.code == "missing_testcontainers_socket_override"
    assert f"export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE={TESTCONTAINERS_SOCKET_OVERRIDE}" in result.message


def test_preflight_accepts_reachable_colima_with_testcontainers_override(tmp_path: Path) -> None:
    colima_socket = _touch_colima_socket(tmp_path)

    result = check_preflight(
        {
            "DOCKER_HOST": f"unix://{colima_socket}",
            "TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE": TESTCONTAINERS_SOCKET_OVERRIDE,
        },
        home=tmp_path,
        connect_unix=lambda path: path == colima_socket,
    )

    assert result.is_ok is True
    assert result.code == "ok"


def test_preflight_fails_early_when_docker_is_unavailable(tmp_path: Path) -> None:
    result = check_preflight({}, home=tmp_path, connect_unix=lambda path: False)

    assert result.is_ok is False
    assert result.code == "docker_unavailable"
    assert "Testcontainers preflight failed before running pytest." in result.message


def test_preflight_fails_before_image_pull_when_configured_credential_helper_is_missing(tmp_path: Path) -> None:
    docker_config = tmp_path / ".docker" / "config.json"
    docker_config.parent.mkdir()
    docker_config.write_text('{"credsStore":"desktop"}\n', encoding="utf-8")

    result = check_preflight(
        {"DOCKER_HOST": "tcp://docker:2375", "PATH": "/usr/bin"},
        home=tmp_path,
        connect_tcp=lambda host, port: (host, port) == ("docker", 2375),
        find_executable=lambda name, path: None,
    )

    assert result.is_ok is False
    assert result.code == "docker_credential_helper_unavailable"
    assert "docker-credential-desktop" in result.message
    assert "pull a missing image" in result.message


def test_preflight_accepts_available_configured_credential_helper(tmp_path: Path) -> None:
    docker_config = tmp_path / ".docker" / "config.json"
    docker_config.parent.mkdir()
    docker_config.write_text('{"credsStore":"desktop"}\n', encoding="utf-8")

    result = check_preflight(
        {"DOCKER_HOST": "tcp://docker:2375", "PATH": "/safe/bin"},
        home=tmp_path,
        connect_tcp=lambda host, port: (host, port) == ("docker", 2375),
        find_executable=lambda name, path: f"/safe/bin/{name}",
    )

    assert result.is_ok is True


def test_configure_docker_desktop_credential_path_prepends_existing_helper(tmp_path: Path) -> None:
    credential_directory = tmp_path / "Docker.app" / "Contents" / "Resources" / "bin"
    credential_directory.mkdir(parents=True)
    (credential_directory / "docker-credential-desktop").touch()
    env = {"PATH": "/usr/bin"}

    configure_docker_desktop_credential_path(
        env,
        credential_directory=credential_directory,
        find_executable=lambda name, path: None,
    )

    assert env["PATH"] == f"{credential_directory}:/usr/bin"


def test_runtime_capacity_rejects_small_docker_vm_before_testcontainers_start() -> None:
    result = check_runtime_capacity(
        total_memory_bytes=1024**3,
        available_storage_kib=8 * 1024**2,
    )

    assert result.is_ok is False
    assert result.code == "docker_runtime_capacity_insufficient"
    assert "memory 1.0 GiB < 4 GiB" in result.message
    assert "available storage 8.0 GiB < 12 GiB" in result.message


def test_runtime_capacity_accepts_release_sized_docker_runtime() -> None:
    result = check_runtime_capacity(
        total_memory_bytes=MINIMUM_DOCKER_MEMORY_BYTES,
        available_storage_kib=MINIMUM_DOCKER_AVAILABLE_STORAGE_KIB,
    )

    assert result.is_ok is True


def test_runtime_capacity_parser_reads_portable_df_available_kib_column() -> None:
    output = "Filesystem 1024-blocks Used Available Capacity Mounted on\noverlay 61603852 10485760 48234496 18% /\n"

    assert _parse_available_storage_kib(output) == 48_234_496


def _touch_colima_socket(tmp_path: Path) -> Path:
    socket_path = tmp_path / ".colima" / "default" / "docker.sock"
    socket_path.parent.mkdir(parents=True)
    socket_path.touch()
    return socket_path
