"""Curated, versioned documentation summaries available to Platform Q&A mode."""

from __future__ import annotations

from collections.abc import Mapping

_DOCS: tuple[dict[str, str], ...] = (
    {
        "id": "implementation-status",
        "title": "Implementation status",
        "path": "docs/implementation-status.md",
        "summary": "Current versus planned product boundaries and links to runtime evidence.",
    },
    {
        "id": "action-types",
        "title": "Action Types parity",
        "path": "docs/action-types-parity-matrix.json",
        "summary": (
            "Canonical Action v3 contract, planning, permissions, durable runs, effects, log, revert, branches, "
            "and interfaces."
        ),
    },
    {
        "id": "pipeline-builder",
        "title": "Pipeline Builder parity",
        "path": "docs/pipeline-builder-parity-matrix.json",
        "summary": (
            "Branch-first pipeline design, validation, proposals, deployments, async DAG runs, cancellation, and "
            "takeover evidence."
        ),
    },
    {
        "id": "quality-roadmap",
        "title": "Quality gate roadmap",
        "path": "docs/quality-gate-roadmap.md",
        "summary": "Static, contract, integration, browser, migration, and live-infrastructure release gates.",
    },
    {
        "id": "ai-fde",
        "title": "AI FDE research and parity",
        "path": "docs/ai-fde-research.md",
        "summary": (
            "Governed modes, server-owned tools, explicit context, confirmations, branch proposals, MCP, and Pilot "
            "boundaries."
        ),
    },
    {
        "id": "documentation-map",
        "title": "Documentation map",
        "path": "docs/documentation-map.md",
        "summary": "Source-of-truth roles and required update order for product claims and proof ledgers.",
    },
)


def search_platform_docs(query: str, max_results: int) -> dict[str, object]:
    terms = {term for term in query.lower().replace("-", " ").split() if term}
    ranked = sorted(
        ((_score(document, terms), document) for document in _DOCS),
        key=lambda item: (-item[0], item[1]["id"]),
    )
    matches = [dict(document) for score, document in ranked if score > 0][:max_results]
    return {
        "query": query,
        "items": matches,
        "count": len(matches),
        "catalogVersion": "2026-08-04",
        "isCuratedAllowlist": True,
    }


def _score(document: Mapping[str, str], terms: set[str]) -> int:
    text = " ".join(document.values()).lower()
    return sum(3 if term in document["title"].lower() else 1 for term in terms if term in text)
