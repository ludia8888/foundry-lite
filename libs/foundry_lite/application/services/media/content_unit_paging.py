"""Validation helpers for read-only content-unit pagination."""

from __future__ import annotations

from foundry_lite.domain.errors import ValidationFailed


def _content_unit_limit(limit: int) -> int:
    if isinstance(limit, bool) or limit < 1:
        raise ValidationFailed("content unit limit must be positive", details={"limit": limit})
    return min(limit, 500)


def _require_non_negative(value: int | None, field: str) -> None:
    if value is not None and (isinstance(value, bool) or value < 0):
        raise ValidationFailed(f"{field} must be non-negative", details={field: value})


def _require_positive_optional(value: int | None, field: str) -> None:
    if value is not None and (isinstance(value, bool) or value < 1):
        raise ValidationFailed(f"{field} must be positive", details={field: value})
