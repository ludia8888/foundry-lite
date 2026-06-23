from __future__ import annotations

from typing import Protocol

from foundry_lite.application.ports.content_index import ContentSearchHit, HybridContentQuery


class ContentRetrievalService(Protocol):
    """Document/page/chunk/segment retrieval, distinct from object query (doc §10.1).

    Content-unit artifacts are the truth; the search index is a rebuildable projection
    (ADR-0001 invariant 5). Implementations re-read authoritative metadata/security before
    returning hits. Contract only in this sprint.
    """

    def search_content(self, query: HybridContentQuery) -> list[ContentSearchHit]:
        """Return content hits with exact citations, after authoritative security re-read."""
        ...
