"""Application port contract for destructive development admin."""

from __future__ import annotations

from typing import Protocol


class DestructiveDevelopmentAdmin(Protocol):
    """Explicit dev-only boundary for destructive schema reset operations."""

    def reset_schema(self) -> None:
        """Drop and recreate metadata tables for explicit development reset."""
        ...
