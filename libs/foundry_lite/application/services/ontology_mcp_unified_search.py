"""Object-anchored unified search surface for the consumer Ontology MCP.

Kept beside the gateway rather than inside it: the gateway is already at the application
module size ceiling, and this is a self-contained boundary — one runtime protocol and the
payload shape an external agent receives.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.domain.context import RequestContext


class OntologyMcpUnifiedSearchRuntime(Protocol):
    """Object-anchored search across an object type and the media bound to it."""

    def unified_search(
        self,
        ctx: RequestContext,
        *,
        query_text: str,
        object_type: str,
        filters: Mapping[str, object] | None = None,
        limit: int = 20,
    ) -> Sequence[object]: ...


def _unified_hit_payload(hit: object) -> dict[str, object]:
    """Serialize one object-anchored hit, keeping the citation that lifted it.

    An agent that cannot see why an object matched will either re-search or assert the object
    is relevant without grounds, so the media citation travels with the object rather than
    being dropped in favour of a bare score.
    """
    citations = getattr(hit, "media_citations", ())
    return {
        "objectType": getattr(hit, "object_type", ""),
        "objectId": getattr(hit, "object_id", ""),
        "objectVersion": getattr(hit, "object_version", 0),
        "properties": getattr(hit, "properties", {}),
        "score": getattr(hit, "score", 0.0),
        "hasOwnTextMatch": getattr(hit, "has_own_text_match", False),
        "mediaCitations": [
            {
                "contentUnitId": citation.content_unit_id,
                "sourceMediaItemVersionId": citation.source_media_item_version_id,
                "propertyName": citation.property_name,
                "indexGeneration": citation.index_generation,
            }
            for citation in citations
        ],
    }
