"""Safe, bounded evidence for interactive Pipeline Builder semantic trials."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict

from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_semantic_schema import SemanticOutputError
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.errors import FoundryLiteError
from foundry_lite.domain.platform.evidence import redact_evidence

JsonObject = dict[str, object]
TrialStatus = Literal["succeeded", "failed"]
_MAX_SNAPSHOT_CHARACTERS = 16_384
_MAX_STRING_CHARACTERS = 2_048
_MAX_MAPPING_ITEMS = 64
_MAX_SEQUENCE_ITEMS = 32
_MAX_DEPTH = 6
_TRUNCATED = "***TRUNCATED***"


class SemanticTrialEvidence(TypedDict):
    """JSON-safe contract consumed by the interactive trial UI."""

    schemaVersion: int
    evidenceKind: str
    input: JsonObject
    request: JsonObject
    parseAttempts: list[JsonObject]
    correction: JsonObject
    final: JsonObject
    pins: JsonObject
    noCommit: JsonObject


@dataclass(frozen=True, slots=True)
class SemanticTrialContext:
    """Immutable inputs needed to construct one row-level trial record."""

    selected_input: Mapping[str, object]
    input_fields: tuple[str, ...]
    output_schema: Mapping[str, object]
    model_alias: str
    prompt_version_id: str
    prompt_mode: str
    data_classification: str
    sensitive_fields: frozenset[str] = frozenset()
    is_commit_forbidden: bool = True


@dataclass(frozen=True, slots=True)
class BoundedSnapshot:
    """A redacted JSON-compatible value with an explicit truncation marker."""

    value: object
    is_truncated: bool


@dataclass(slots=True)
class _SnapshotBudget:
    remaining_characters: int = _MAX_SNAPSHOT_CHARACTERS
    is_truncated: bool = False

    def consume(self, count: int) -> int:
        allowed = max(0, min(count, self.remaining_characters))
        self.remaining_characters -= allowed
        if allowed < count:
            self.is_truncated = True
        return allowed


def semantic_trial_success(
    context: SemanticTrialContext,
    request: ModelRequest,
    response: ModelResponse,
    output: object,
) -> SemanticTrialEvidence:
    """Build safe evidence for one successfully parsed model response."""

    return _trial_evidence(
        context,
        request,
        response,
        status="succeeded",
        output=output,
        error=None,
    )


def semantic_trial_failure(
    context: SemanticTrialContext,
    request: ModelRequest,
    response: ModelResponse,
    error: FoundryLiteError,
) -> SemanticTrialEvidence:
    """Build safe evidence for one failed parse or schema-validation attempt."""

    return _trial_evidence(
        context,
        request,
        response,
        status="failed",
        output=None,
        error=error,
    )


def bounded_redacted_snapshot(
    value: object,
    *,
    sensitive_fields: Collection[str] = (),
) -> BoundedSnapshot:
    """Redact secret/sensitive keys and cap the retained preview structure."""

    redacted = redact_evidence(value, set(sensitive_fields))
    budget = _SnapshotBudget()
    bounded = _bounded_value(redacted, budget, depth=0)
    return BoundedSnapshot(value=bounded, is_truncated=budget.is_truncated)


def semantic_trial_error(
    error: SemanticOutputError,
    trial: SemanticTrialEvidence | None,
) -> SemanticOutputError:
    """Attach already-redacted trial evidence to a typed parse failure."""

    if trial is None:
        return error
    return SemanticOutputError(
        str(error),
        details={**dict(error.details), "trialEvidence": dict(trial)},
    )


def _trial_evidence(
    context: SemanticTrialContext,
    request: ModelRequest,
    response: ModelResponse,
    *,
    status: TrialStatus,
    output: object,
    error: FoundryLiteError | None,
) -> SemanticTrialEvidence:
    input_snapshot = bounded_redacted_snapshot(
        context.selected_input,
        sensitive_fields=context.sensitive_fields,
    )
    final_snapshot = bounded_redacted_snapshot(output, sensitive_fields=context.sensitive_fields)
    return SemanticTrialEvidence(
        schemaVersion=1,
        evidenceKind="pipeline_semantic_trial",
        input=_input_evidence(context, input_snapshot),
        request=_request_evidence(request),
        parseAttempts=[_parse_attempt(response, status, error, context.sensitive_fields)],
        correction={"attempted": False, "attemptCount": 0, "strategy": "none"},
        final=_final_evidence(status, output, final_snapshot, error, context.sensitive_fields),
        pins=_pin_evidence(context, response),
        noCommit={
            "commitForbidden": context.is_commit_forbidden,
            "servingVersionCreated": False,
        },
    )


def _input_evidence(
    context: SemanticTrialContext,
    snapshot: BoundedSnapshot,
) -> JsonObject:
    return {
        "selectedFields": list(context.input_fields),
        "rowSnapshot": snapshot.value,
        "rowFingerprint": _json_hash(dict(context.selected_input)),
        "isTruncated": snapshot.is_truncated,
    }


def _request_evidence(request: ModelRequest) -> JsonObject:
    return {
        "requestFingerprint": request.request_hash,
        "modelAlias": request.model_alias,
        "messageSummaries": [_message_summary(message) for message in request.messages],
        "responseSchemaFingerprint": _json_hash({"schema": request.response_schema or ""}),
        "temperature": request.temperature,
        "maxOutputTokens": request.max_output_tokens,
        "thinkingMode": request.thinking_mode,
        "dataClassification": request.data_classification,
        "regionRequirement": request.region_requirement,
    }


def _message_summary(message: object) -> JsonObject:
    role = str(getattr(message, "role", ""))
    content = str(getattr(message, "content", ""))
    media_references = tuple(getattr(message, "media_references", ()))
    return {
        "role": role,
        "characterCount": len(content),
        "contentFingerprint": _json_hash({"role": role, "content": content}),
        "mediaReferences": [_media_reference_summary(reference) for reference in media_references],
    }


def _media_reference_summary(reference: object) -> JsonObject:
    return {
        "mediaItemVersionId": str(getattr(reference, "media_item_version_id", "")),
        "mimeType": str(getattr(reference, "mime_type", "")),
        "contentHash": str(getattr(reference, "content_hash", "")),
        "sourceLocator": dict(getattr(reference, "source_locator", {})),
    }


def _parse_attempt(
    response: ModelResponse,
    status: TrialStatus,
    error: FoundryLiteError | None,
    sensitive_fields: frozenset[str],
) -> JsonObject:
    snapshot = _response_snapshot(response.content, sensitive_fields)
    return {
        "attemptNumber": 1,
        "stage": "initial_response",
        "status": "parsed" if status == "succeeded" else "parse_failed",
        "responseFingerprint": _json_hash({"content": response.content}),
        "responseCharacterCount": len(response.content),
        "responseSnapshot": snapshot.value,
        "isTruncated": snapshot.is_truncated,
        "error": _error_evidence(error, sensitive_fields),
    }


def _final_evidence(
    status: TrialStatus,
    output: object,
    snapshot: BoundedSnapshot,
    error: FoundryLiteError | None,
    sensitive_fields: frozenset[str],
) -> JsonObject:
    return {
        "status": status,
        "typedOutput": snapshot.value if status == "succeeded" else None,
        "outputFingerprint": _json_hash({"output": output}) if status == "succeeded" else None,
        "isTruncated": snapshot.is_truncated,
        "error": _error_evidence(error, sensitive_fields),
    }


def _pin_evidence(
    context: SemanticTrialContext,
    response: ModelResponse,
) -> JsonObject:
    return {
        "provider": response.provider,
        "modelAlias": context.model_alias,
        "resolvedModelId": response.resolved_model_id,
        "resolvedModelRevision": response.resolved_model_revision,
        "modelHash": response.model_hash,
        "promptVersionId": context.prompt_version_id,
        "promptMode": context.prompt_mode,
        "promptHash": response.prompt_hash,
        "outputSchemaFingerprint": _json_hash(dict(context.output_schema)),
        "finishReason": response.finish_reason,
        "providerRequestId": response.provider_request_id,
        "inputTokens": response.input_tokens,
        "outputTokens": response.output_tokens,
        "latencyMs": response.latency_ms,
    }


def _error_evidence(
    error: FoundryLiteError | None,
    sensitive_fields: Collection[str],
) -> JsonObject | None:
    if error is None:
        return None
    details = bounded_redacted_snapshot(error.details, sensitive_fields=sensitive_fields)
    return {
        "code": error.code,
        "message": scrub_error_text(str(error)),
        "details": details.value,
        "isTruncated": details.is_truncated,
    }


def _response_snapshot(
    content: str,
    sensitive_fields: Collection[str],
) -> BoundedSnapshot:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return BoundedSnapshot(
            value={
                "contentRedacted": True,
                "contentFingerprint": _json_hash({"content": content}),
                "characterCount": len(content),
            },
            is_truncated=False,
        )
    return bounded_redacted_snapshot(parsed, sensitive_fields=sensitive_fields)


def _bounded_value(value: object, budget: _SnapshotBudget, *, depth: int) -> object:
    if budget.remaining_characters <= 0 or depth > _MAX_DEPTH:
        budget.is_truncated = True
        return _TRUNCATED
    if isinstance(value, Mapping):
        return _bounded_mapping(value, budget, depth)
    if isinstance(value, list | tuple):
        return _bounded_sequence(value, budget, depth)
    if isinstance(value, str):
        return _bounded_text(scrub_error_text(value), budget)
    if isinstance(value, bytes):
        budget.consume(24)
        return {"byteSize": len(value), "redacted": True}
    if value is None or isinstance(value, bool | int | float):
        budget.consume(len(str(value)))
        return value
    budget.consume(24)
    return {"type": type(value).__name__, "redacted": True}


def _bounded_mapping(
    value: Mapping[object, object],
    budget: _SnapshotBudget,
    depth: int,
) -> JsonObject:
    result: JsonObject = {}
    items = sorted(value.items(), key=lambda item: str(item[0]))
    for key, item in items[:_MAX_MAPPING_ITEMS]:
        text_key = str(key)
        if budget.consume(len(text_key)) < len(text_key):
            break
        result[text_key] = _bounded_value(item, budget, depth=depth + 1)
    if len(items) > _MAX_MAPPING_ITEMS:
        budget.is_truncated = True
        result["_truncatedFields"] = len(items) - _MAX_MAPPING_ITEMS
    return result


def _bounded_sequence(
    value: Sequence[object],
    budget: _SnapshotBudget,
    depth: int,
) -> list[object]:
    result = [_bounded_value(item, budget, depth=depth + 1) for item in value[:_MAX_SEQUENCE_ITEMS]]
    if len(value) > _MAX_SEQUENCE_ITEMS:
        budget.is_truncated = True
        result.append({"_truncatedItems": len(value) - _MAX_SEQUENCE_ITEMS})
    return result


def _bounded_text(value: str, budget: _SnapshotBudget) -> str:
    requested = min(len(value), _MAX_STRING_CHARACTERS)
    allowed = budget.consume(requested)
    if len(value) > allowed:
        budget.is_truncated = True
        return f"{value[:allowed]}…"
    return value
