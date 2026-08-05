"""Ontology MCP unified search: object-anchored retrieval for external agents.

The fused object + bound-media search engine already existed; only the MCP surface withheld
it, so an external agent could keyword-match object properties but could not reach an answer
that lived inside an attached PDF, transcript, or video frame. These tests pin the surface:
the tool delegates with the caller's scope, returns objects rather than document chunks, and
carries the citation that lifted each object.

Raw media search is deliberately not exposed. Tools are projected from an application's
object/action/function grants, and a media-item search has no grant to project through — it
would hand an agent a path around the projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

import pytest
from foundry_lite.application.services.ontology_mcp_tools import object_tools
from foundry_lite.application.services.ontology_mcp_unified_search import _unified_hit_payload
from foundry_lite.domain.platform.scopes import resource_scope


@dataclass(frozen=True)
class _Citation:
    content_unit_id: str
    source_media_item_version_id: str
    property_name: str
    index_generation: str


@dataclass(frozen=True)
class _Hit:
    object_type: str
    object_id: str
    object_version: int
    properties: Mapping[str, object]
    score: float
    has_own_text_match: bool
    media_citations: tuple[_Citation, ...] = field(default=())


class _RecordingUnifiedSearch:
    def __init__(self, hits: Sequence[_Hit]) -> None:
        self.hits = list(hits)
        self.calls: list[dict[str, object]] = []

    def unified_search(
        self,
        _ctx: object,
        *,
        query_text: str,
        object_type: str,
        filters: Mapping[str, object] | None = None,
        limit: int = 20,
    ) -> Sequence[_Hit]:
        self.calls.append({"query_text": query_text, "object_type": object_type, "filters": filters, "limit": limit})
        return self.hits


def _read_scopes(name: str) -> tuple[str, ...]:
    return (resource_scope("object", name, "read"),)


def _schema(tool: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], tool["inputSchema"])


def _properties(tool: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], _schema(tool)["properties"])


def test_unified_search_tool_is_projected_with_object_read_scope() -> None:
    tools = {tool["name"]: tool for tool in object_tools("Order", _read_scopes("Order"))}

    assert "object.Order.unifiedSearch" in tools
    tool = tools["object.Order.unifiedSearch"]
    assert _schema(tool)["required"] == ["query"]
    assert set(_properties(tool)) == {"query", "filter", "limit"}
    limit_schema = cast(Mapping[str, object], _properties(tool)["limit"])
    assert limit_schema["maximum"] == 50, "an agent must not pull an unbounded page"


def test_unified_search_tool_is_withheld_without_object_read_scope() -> None:
    assert object_tools("Order", ()) == []


def test_object_search_tool_offers_semantic_text_alongside_keyword() -> None:
    tools = {tool["name"]: tool for tool in object_tools("Order", _read_scopes("Order"))}

    properties = _properties(tools["object.Order.search"])
    assert "search" in properties and "semanticText" in properties


def test_hit_payload_keeps_the_citation_that_lifted_the_object() -> None:
    """An agent that cannot see why an object matched will re-search or over-claim."""
    hit = _Hit(
        object_type="Order",
        object_id="O-1001",
        object_version=3,
        properties={"status": "PENDING"},
        score=0.42,
        has_own_text_match=False,
        media_citations=(
            _Citation(
                content_unit_id="cu-7",
                source_media_item_version_id="miv-2",
                property_name="contract",
                index_generation="gen-9",
            ),
        ),
    )

    payload = _unified_hit_payload(hit)

    assert payload["objectType"] == "Order"
    assert payload["objectId"] == "O-1001"
    assert payload["objectVersion"] == 3
    assert payload["hasOwnTextMatch"] is False
    assert payload["mediaCitations"] == [
        {
            "contentUnitId": "cu-7",
            "sourceMediaItemVersionId": "miv-2",
            "propertyName": "contract",
            "indexGeneration": "gen-9",
        }
    ]


def test_hit_payload_reports_no_citations_for_a_purely_structured_match() -> None:
    hit = _Hit(
        object_type="Order",
        object_id="O-2",
        object_version=1,
        properties={},
        score=1.0,
        has_own_text_match=True,
    )

    payload = _unified_hit_payload(hit)

    assert payload["hasOwnTextMatch"] is True
    assert payload["mediaCitations"] == []


@pytest.mark.parametrize("limit", [1, 20, 50])
def test_runtime_receives_the_object_type_and_bounded_limit(limit: int) -> None:
    runtime = _RecordingUnifiedSearch([])

    runtime.unified_search(object(), query_text="late shipment", object_type="Order", limit=limit)

    assert runtime.calls == [{"query_text": "late shipment", "object_type": "Order", "filters": None, "limit": limit}]
