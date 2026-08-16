"""MCP tool-result projections shared by the Builder and Ontology planes."""

from __future__ import annotations

import json
from collections.abc import Mapping

from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping, scrub_error_text
from foundry_lite.domain.errors import FoundryLiteError


def serialized_text_content(structured: Mapping[str, object]) -> list[dict[str, str]]:
    """Mirror structured content as stable JSON for compatibility clients."""

    return [
        {
            "type": "text",
            "text": json.dumps(structured, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        }
    ]


def tool_error_result(exc: FoundryLiteError, *, request_id: str) -> dict[str, object]:
    """Return a sanitized execution failure without making it a protocol failure."""

    structured = tool_error_structured(exc, request_id=request_id)
    return {
        "structuredContent": structured,
        "content": serialized_text_content(structured),
        "isError": True,
    }


def tool_error_structured(exc: FoundryLiteError, *, request_id: str) -> dict[str, object]:
    """Build the durable, sanitized structured portion of a tool execution error."""

    details = scrub_error_mapping(exc.details)
    return {
        "error": {
            "type": exc.code,
            "message": scrub_error_text(exc.message),
            "details": details,
            "requestId": request_id,
        }
    }
