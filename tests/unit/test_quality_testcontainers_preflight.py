from __future__ import annotations

from pathlib import Path

from scripts.quality.check_testcontainers_preflight import (
    TESTCONTAINERS_SOCKET_OVERRIDE,
    check_preflight,
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


def _touch_colima_socket(tmp_path: Path) -> Path:
    socket_path = tmp_path / ".colima" / "default" / "docker.sock"
    socket_path.parent.mkdir(parents=True)
    socket_path.touch()
    return socket_path
