"""Curated, versioned documentation summaries available to Platform Q&A mode."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from foundry_lite.domain.errors import NotFound

_MAX_DOCUMENT_CHARACTERS = 24_000

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
    {
        "id": "python-engineering",
        "title": "Python transforms and engineering rules",
        "path": "foundry_lite_python_engineering_guidelines_ko.md",
        "summary": "Python transform boundaries, ports, adapters, transactions, tests, and quality requirements.",
    },
    {
        "id": "sdk-cookbook",
        "title": "OSDK and React SDK cookbook",
        "path": "docs/sdk-frontend-cookbook.md",
        "summary": (
            "Typed TypeScript OSDK, React providers, screen recipes, source flows, objects, Actions, and Operations."
        ),
    },
    {
        "id": "aip-spec",
        "title": "AIP and function runtime specification",
        "path": "docs/aip-lite-canonical-spec.md",
        "summary": "Versioned tools, functions, evidence, model runtime, approvals, and agent execution contracts.",
    },
)

_OFFICIAL_DOCUMENT_TOOL_IDS = {
    "get_python_transforms_documentation": "python-engineering",
    "get_typescript_v1_functions_documentation": "aip-spec",
    "get_typescript_v2_functions_documentation": "aip-spec",
    "get_custom_widget_documentation": "sdk-cookbook",
    "get_ml_documentation": "implementation-status",
    "get_spark_profile_documentation": "implementation-status",
    "get_osdk_react_components_documentation": "sdk-cookbook",
}


def search_platform_docs(query: str, max_results: int) -> dict[str, object]:
    """Search the curated local platform-document allowlist."""
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
        "catalogVersion": "2026-08-05",
        "isCuratedAllowlist": True,
    }


def platform_documentation_summaries() -> dict[str, object]:
    """Return stable metadata for every curated platform document."""
    return {
        "items": [dict(document) for document in _DOCS],
        "count": len(_DOCS),
        "catalogVersion": "2026-08-05",
        "isCuratedAllowlist": True,
    }


def load_platform_document(document_id: str) -> dict[str, object]:
    """Load one bounded document from the maintained repository allowlist."""
    document = next((item for item in _DOCS if item["id"] == document_id), None)
    if document is None:
        raise NotFound("curated platform document not found", details={"documentId": document_id})
    text = (_repository_root() / document["path"]).read_text(encoding="utf-8")
    content = text[:_MAX_DOCUMENT_CHARACTERS]
    return {
        **document,
        "content": content,
        "isTruncated": len(content) != len(text),
        "contentCharacters": len(content),
        "catalogVersion": "2026-08-05",
    }


def load_official_tool_document(tool_id: str, topic: str | None) -> dict[str, object]:
    """Resolve an official-name documentation tool to its local source."""
    document_id = _OFFICIAL_DOCUMENT_TOOL_IDS.get(tool_id)
    if document_id is None:
        raise NotFound("curated documentation tool not found", details={"toolId": tool_id})
    payload = load_platform_document(document_id)
    return {**payload, "toolId": tool_id, "topic": topic, "isFoundryLiteDocumentation": True}


def ontology_sdk_context(topic: str | None) -> dict[str, object]:
    """Return bounded Ontology SDK concepts for builder agents."""
    return {**load_platform_document("sdk-cookbook"), "topic": topic, "contextType": "ontology_sdk"}


def ontology_sdk_examples(topic: str | None, language: str | None) -> dict[str, object]:
    """Return maintained OSDK examples for one optional language."""
    return {
        **load_platform_document("sdk-cookbook"),
        "topic": topic,
        "language": language,
        "contextType": "ontology_sdk_examples",
    }


def list_platform_sdk_apis(product: str | None, max_results: int) -> dict[str, object]:
    """List API/SDK surfaces from the generated product registry."""
    registry = _sdk_registry()
    surfaces = registry.get("surfaces")
    items = [dict(item) for item in surfaces if isinstance(item, Mapping)] if isinstance(surfaces, list) else []
    if product:
        lowered = product.lower()
        items = [item for item in items if lowered in str(item.get("productArea", "")).lower()]
    selected = items[:max_results]
    return {"product": product, "items": selected, "count": len(selected), "isGeneratedRegistry": True}


def platform_sdk_api_reference(api_id: str) -> dict[str, object]:
    """Return one exact generated API/SDK surface contract."""
    registry = _sdk_registry()
    surfaces = registry.get("surfaces")
    if isinstance(surfaces, list):
        match = next((dict(item) for item in surfaces if isinstance(item, Mapping) and item.get("id") == api_id), None)
        if match is not None:
            return {"api": match, "schemaVersion": registry.get("schemaVersion"), "isGeneratedRegistry": True}
    raise NotFound("Platform SDK API reference not found", details={"apiId": api_id})


def _repository_root() -> Path:
    """Resolve the repository root containing the curated sources."""
    return Path(__file__).resolve().parents[5]


def _sdk_registry() -> dict[str, object]:
    """Load the generated API/SDK surface registry from disk."""
    value = json.loads((_repository_root() / "docs/frontend-api-sdk-surface-matrix.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise NotFound("Platform SDK registry is unavailable")
    return {str(key): item for key, item in value.items()}


def _score(document: Mapping[str, str], terms: set[str]) -> int:
    text = " ".join(document.values()).lower()
    return sum(3 if term in document["title"].lower() else 1 for term in terms if term in text)
