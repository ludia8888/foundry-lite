"""Single definition of which database a local deployment reads and writes.

The API runtime and the migration job are separate processes that must agree on this, or
migrations silently land in a different file than the one being served. Both resolve the
target through here instead of hardcoding a path.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

STORAGE_ROOT_ENV = "FOUNDRY_LITE_HOME"
DATABASE_URL_ENV = "FOUNDRY_LITE_DB_URL"
DEFAULT_LOCAL_STORAGE_ROOT = ".foundry-lite"
_LOCAL_DATABASE_FILENAME = "foundry-lite.db"


def local_storage_root(storage_root: str | Path | None) -> Path:
    """Resolve the durable-state directory a local deployment keeps its files under."""

    return Path(storage_root or DEFAULT_LOCAL_STORAGE_ROOT).resolve()


def local_database_url(storage_root: str | Path | None) -> str:
    """Build the SQLite URL for a storage root, as an absolute path.

    The path is absolute so a caller's working directory cannot move the database.
    """

    return f"sqlite:///{local_storage_root(storage_root) / _LOCAL_DATABASE_FILENAME}"


def database_url_from_environment(environ: Mapping[str, str]) -> str:
    """Select the database URL exactly the way the API runtime composes it at startup."""

    explicit = environ.get(DATABASE_URL_ENV)
    if explicit is not None and explicit.strip():
        return explicit.strip()
    return local_database_url(environ.get(STORAGE_ROOT_ENV))


__all__ = [
    "DATABASE_URL_ENV",
    "DEFAULT_LOCAL_STORAGE_ROOT",
    "STORAGE_ROOT_ENV",
    "database_url_from_environment",
    "local_database_url",
    "local_storage_root",
]
