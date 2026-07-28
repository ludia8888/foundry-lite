"""Shared projections for processor content-unit location metadata."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.media_processor import ProcessedContentUnit


def source_locator_payload(unit: ProcessedContentUnit) -> dict[str, object] | None:
    """Prefer an exact processor locator and otherwise derive page/time coordinates."""

    if isinstance(unit.source_locator, Mapping):
        explicit = dict(unit.source_locator)
        if explicit:
            return explicit
    values: dict[str, object] = {
        "pageNumber": unit.page_number,
        "startMs": unit.start_ms,
        "endMs": unit.end_ms,
    }
    locator = {key: value for key, value in values.items() if value is not None}
    return locator or None
