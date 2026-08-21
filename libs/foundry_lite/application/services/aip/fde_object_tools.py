"""Focused object-tool projections for the AI FDE application surface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.services.aip.fde_tool_result import (
    FdePlatformToolError,
    FdePlatformToolRequest,
    required_text,
)
from foundry_lite.domain.context import RequestContext


class FdeSearchAroundResolver(Protocol):
    def resolve_search_around(
        self,
        from_object_type_api_name: str,
        link_types: Sequence[str],
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        include_items: bool = True,
    ) -> Mapping[str, object]: ...


def search_around_ontology_objects(
    object_sets: FdeSearchAroundResolver,
    ctx: RequestContext,
    request: FdePlatformToolRequest,
) -> dict[str, object]:
    return dict(
        object_sets.resolve_search_around(
            required_text(request.arguments, "fromObjectType"),
            _link_types(request.arguments.get("linkTypes")),
            ctx=ctx,
            filter_ast=_optional_filter(request.arguments.get("filter")),
            include_items=True,
        )
    )


def _link_types(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", "linkTypes must be a list of strings")
    if not all(isinstance(item, str) and item for item in value):
        raise FdePlatformToolError("schema_invalid", "linkTypes must be a list of non-empty strings")
    return [str(item) for item in value]


def _optional_filter(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise FdePlatformToolError("schema_invalid", "filter must be an object")
    return {str(name): item for name, item in value.items()}
