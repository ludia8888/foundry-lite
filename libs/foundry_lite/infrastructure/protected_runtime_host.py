"""Host-level invariants for protected staging and production composition.

Protected runtimes keep a small amount of encrypted/local operator state today
(OAuth signing material, prompt artifacts, and backup receipts).  Accepting the
default relative ``.foundry-lite`` path on an ephemeral container would silently
lose that state on every redeploy.  This module makes the PostgreSQL and mounted
durable-state requirements explicit before any repository or adapter is built.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url

from foundry_lite.application.runtime_profile import RuntimeProfile

DURABLE_STATE_MOUNT_ENV = "FOUNDRY_LITE_DURABLE_STATE_MOUNT"

MountChecker = Callable[[Path], bool]


@dataclass(frozen=True)
class ProtectedRuntimeHost:
    """Safe, non-secret summary of one protected runtime host binding."""

    database_backend: str
    durable_state_mount: Path
    runtime_home: Path


def require_protected_runtime_host(
    *,
    profile: RuntimeProfile,
    database_url: str | None,
    runtime_home: str | Path | None,
    environ: Mapping[str, str],
    mount_checker: MountChecker | None = None,
) -> ProtectedRuntimeHost:
    """Reject SQLite and ephemeral local state for a protected runtime."""

    if not profile.is_protected:
        raise ValueError("protected runtime host validation requires a staging or production profile")
    backend = _postgres_backend(database_url)
    mount = _durable_mount(environ)
    home = _runtime_home(runtime_home, mount)
    is_mount = (mount_checker or _is_mount)(mount)
    if not is_mount:
        raise ValueError(
            f"{DURABLE_STATE_MOUNT_ENV} must be an active filesystem mount; "
            "protected local state cannot use an ephemeral container filesystem"
        )
    return ProtectedRuntimeHost(
        database_backend=backend,
        durable_state_mount=mount,
        runtime_home=home,
    )


def _postgres_backend(database_url: str | None) -> str:
    if database_url is None or not database_url.strip():
        raise ValueError("FOUNDRY_LITE_DB_URL is required for protected runtime startup")
    try:
        url = make_url(database_url)
    except Exception:
        raise ValueError("FOUNDRY_LITE_DB_URL must be a valid PostgreSQL URL") from None
    backend = url.get_backend_name()
    if backend != "postgresql" or not url.database:
        raise ValueError("protected runtime metadata requires a PostgreSQL database URL")
    return backend


def _durable_mount(environ: Mapping[str, str]) -> Path:
    raw_mount = environ.get(DURABLE_STATE_MOUNT_ENV, "").strip()
    if not raw_mount:
        raise ValueError(f"{DURABLE_STATE_MOUNT_ENV} is required for protected runtime startup")
    mount = Path(raw_mount).expanduser()
    if not mount.is_absolute():
        raise ValueError(f"{DURABLE_STATE_MOUNT_ENV} must be an absolute path")
    resolved = mount.resolve()
    if resolved == Path("/"):
        raise ValueError(f"{DURABLE_STATE_MOUNT_ENV} cannot be the filesystem root")
    return resolved


def _runtime_home(runtime_home: str | Path | None, mount: Path) -> Path:
    if runtime_home is None or not str(runtime_home).strip():
        raise ValueError("FOUNDRY_LITE_HOME is required for protected runtime startup")
    home = Path(runtime_home).expanduser()
    if not home.is_absolute():
        raise ValueError("FOUNDRY_LITE_HOME must be absolute in protected runtimes")
    resolved = home.resolve()
    if not resolved.is_relative_to(mount):
        raise ValueError("FOUNDRY_LITE_HOME must be located under FOUNDRY_LITE_DURABLE_STATE_MOUNT")
    return resolved


def _is_mount(path: Path) -> bool:
    try:
        return path.is_dir() and path.is_mount()
    except OSError:
        return False


__all__ = [
    "DURABLE_STATE_MOUNT_ENV",
    "ProtectedRuntimeHost",
    "require_protected_runtime_host",
]
