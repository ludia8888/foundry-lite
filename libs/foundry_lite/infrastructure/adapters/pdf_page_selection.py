"""Typed PDF page-window parameters shared by local PDF processors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class PdfPageSelection:
    start: int = 1
    limit: int | None = None


class PdfPageSelectionError(ValueError):
    """Raised when a page window cannot be interpreted safely."""


def pdf_page_selection(parameters: Mapping[str, object]) -> PdfPageSelection:
    raw = parameters.get("pageSelection")
    if raw is None:
        return PdfPageSelection()
    if not isinstance(raw, Mapping):
        raise PdfPageSelectionError("page_selection_must_be_object")
    start = raw.get("start", 1)
    limit = raw.get("limit")
    if not _positive_integer(start):
        raise PdfPageSelectionError("page_selection_start_invalid")
    if limit is not None and not _positive_integer(limit):
        raise PdfPageSelectionError("page_selection_limit_invalid")
    return PdfPageSelection(start=int(start), limit=int(limit) if limit is not None else None)


def selected_page_indexes(page_count: int, selection: PdfPageSelection) -> range:
    start_index = min(selection.start - 1, page_count)
    end_index = page_count if selection.limit is None else min(start_index + selection.limit, page_count)
    return range(start_index, end_index)


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
