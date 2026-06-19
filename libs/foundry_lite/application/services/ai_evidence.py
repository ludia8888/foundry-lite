"""Fine-grained evidence models for AI and insight explanations."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from foundry_lite.domain.errors import ValidationFailed

EvidenceKind = Literal["object_property", "insight_claim", "llm_extraction"]
HumanReviewStatus = Literal["pending", "approved", "rejected"]

_EVIDENCE_NAMESPACE: Final = uuid.UUID("d1587fc6-6208-43e3-8277-1e05a250f60f")
_MASKED_VALUE: Final = "***MASKED***"


@dataclass(frozen=True)
class EvidenceSourceSpan:
    """Pinned text/video/image span used as evidence for an AI extraction."""

    quote: str | None = None
    timecode_start: str | None = None
    timecode_end: str | None = None
    bounding_box: Mapping[str, object] | None = None

    def redacted_payload(self, *, is_masked: bool = False) -> dict[str, object]:
        """Return source span evidence without leaking masked values."""

        if is_masked:
            return {"isMasked": True, "quote": _MASKED_VALUE}
        payload: dict[str, object] = {"isMasked": False}
        if self.quote is not None:
            payload["quote"] = self.quote
        if self.timecode_start is not None:
            payload["timecodeStart"] = self.timecode_start
        if self.timecode_end is not None:
            payload["timecodeEnd"] = self.timecode_end
        if self.bounding_box is not None:
            payload["boundingBox"] = dict(self.bounding_box)
        return payload


@dataclass(frozen=True)
class EvidenceSourceRef:
    """Pinned dataset/object source for one evidence item."""

    dataset_version_id: str
    object_type: str
    object_id: str
    object_version: int

    def __post_init__(self) -> None:
        """Require the source to identify one immutable object revision."""

        if self.object_version < 1:
            raise ValidationFailed("evidence source object version must be positive")
        _require_non_empty(self.dataset_version_id, self.object_type, self.object_id)

    def identity_payload(self) -> list[object]:
        """Return the source coordinates used in evidence ids."""

        return [self.dataset_version_id, self.object_type, self.object_id, self.object_version]


@dataclass(frozen=True)
class EvidenceModelRef:
    """Pinned extractor/model/prompt coordinates for one evidence item."""

    extractor_version: str
    model_name: str
    model_version: str
    prompt_version: str
    model_parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        """Require model and prompt versions before evidence can be built."""

        _require_non_empty(self.extractor_version, self.model_name, self.model_version, self.prompt_version)

    @property
    def parameters_hash(self) -> str:
        """Return a stable hash of model parameters without storing raw config."""

        return _stable_hash(dict(self.model_parameters))

    def identity_payload(self) -> list[object]:
        """Return the model coordinates used in evidence ids."""

        return [self.model_name, self.model_version, self.prompt_version, self.parameters_hash]


@dataclass(frozen=True)
class EvidenceReference:
    """Immutable evidence reference for one source object and extraction revision."""

    evidence_id: str
    evidence_kind: EvidenceKind
    source_dataset_version_id: str
    source_object_type: str
    source_object_id: str
    source_object_version: int
    extractor_version: str
    model_name: str
    model_version: str
    prompt_version: str
    parameters_hash: str
    human_review_status: HumanReviewStatus
    confidence: float | None = None
    source_span: EvidenceSourceSpan | None = None
    previous_evidence_id: str | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        """Reject evidence that is not pinned to source and model versions."""

        if self.source_object_version < 1 or self.revision < 1:
            raise ValidationFailed("evidence versions must be positive")
        _require_non_empty(
            self.source_dataset_version_id,
            self.extractor_version,
            self.model_name,
            self.model_version,
            self.prompt_version,
            self.parameters_hash,
        )

    def payload(self, *, is_masked: bool = False) -> dict[str, object]:
        """Return a serializable evidence payload for API/operator surfaces."""

        payload: dict[str, object] = {
            "evidenceId": self.evidence_id,
            "evidenceKind": self.evidence_kind,
            "sourceDatasetVersionId": self.source_dataset_version_id,
            "sourceObjectType": self.source_object_type,
            "sourceObjectId": self.source_object_id,
            "sourceObjectVersion": self.source_object_version,
            "extractorVersion": self.extractor_version,
            "modelName": self.model_name,
            "modelVersion": self.model_version,
            "promptVersion": self.prompt_version,
            "parametersHash": self.parameters_hash,
            "humanReviewStatus": self.human_review_status,
            "revision": self.revision,
        }
        _add_optional_reference_fields(payload, self)
        if self.source_span is not None:
            payload["sourceSpan"] = self.source_span.redacted_payload(is_masked=is_masked)
        return payload


def build_llm_extraction_evidence(
    evidence_kind: EvidenceKind,
    *,
    source: EvidenceSourceRef,
    model: EvidenceModelRef,
    human_review_status: HumanReviewStatus,
    source_span: EvidenceSourceSpan | None = None,
    confidence: float | None = None,
) -> EvidenceReference:
    """Build an immutable evidence reference pinned to model and prompt versions."""

    return EvidenceReference(
        evidence_id=_llm_evidence_id(evidence_kind, source=source, model=model, source_span=source_span),
        evidence_kind=evidence_kind,
        source_dataset_version_id=source.dataset_version_id,
        source_object_type=source.object_type,
        source_object_id=source.object_id,
        source_object_version=source.object_version,
        extractor_version=model.extractor_version,
        model_name=model.model_name,
        model_version=model.model_version,
        prompt_version=model.prompt_version,
        parameters_hash=model.parameters_hash,
        human_review_status=human_review_status,
        confidence=confidence,
        source_span=source_span,
    )


def revise_evidence_reference(
    previous: EvidenceReference,
    *,
    extractor_version: str,
    model_version: str,
    prompt_version: str,
    model_parameters: Mapping[str, object],
    source_span: EvidenceSourceSpan | None = None,
    confidence: float | None = None,
    human_review_status: HumanReviewStatus = "pending",
) -> EvidenceReference:
    """Create a new evidence revision while preserving the old immutable reference."""

    revised = build_llm_extraction_evidence(
        previous.evidence_kind,
        source=EvidenceSourceRef(
            dataset_version_id=previous.source_dataset_version_id,
            object_type=previous.source_object_type,
            object_id=previous.source_object_id,
            object_version=previous.source_object_version,
        ),
        model=EvidenceModelRef(
            extractor_version=extractor_version,
            model_name=previous.model_name,
            model_version=model_version,
            prompt_version=prompt_version,
            model_parameters=model_parameters,
        ),
        human_review_status=human_review_status,
        source_span=source_span,
        confidence=confidence,
    )
    return _with_revision(revised, previous)


def build_insight_claim_payload(
    claim_id: str,
    claim_text: str,
    *,
    evidence_object_ids: Sequence[str],
    evidence_refs: Sequence[EvidenceReference],
    human_review_status: HumanReviewStatus,
) -> dict[str, object]:
    """Build an insight claim only when it has pinned evidence objects."""

    if not evidence_object_ids or not evidence_refs:
        raise ValidationFailed("insight claim requires evidence objects")
    return {
        "claimId": claim_id,
        "claimText": claim_text,
        "evidenceObjectIds": list(evidence_object_ids),
        "evidenceRefs": [ref.payload() for ref in evidence_refs],
        "humanReviewStatus": human_review_status,
    }


def _with_revision(revised: EvidenceReference, previous: EvidenceReference) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=_evidence_id({"previous": previous.evidence_id, "revised": revised.payload()}),
        evidence_kind=revised.evidence_kind,
        source_dataset_version_id=revised.source_dataset_version_id,
        source_object_type=revised.source_object_type,
        source_object_id=revised.source_object_id,
        source_object_version=revised.source_object_version,
        extractor_version=revised.extractor_version,
        model_name=revised.model_name,
        model_version=revised.model_version,
        prompt_version=revised.prompt_version,
        parameters_hash=revised.parameters_hash,
        human_review_status=revised.human_review_status,
        confidence=revised.confidence,
        source_span=revised.source_span,
        previous_evidence_id=previous.evidence_id,
        revision=previous.revision + 1,
    )


def _llm_evidence_id(
    evidence_kind: EvidenceKind,
    *,
    source: EvidenceSourceRef,
    model: EvidenceModelRef,
    source_span: EvidenceSourceSpan | None,
) -> str:
    payload = {
        "kind": evidence_kind,
        "source": source.identity_payload(),
        "extractor": model.extractor_version,
        "model": model.identity_payload(),
        "span": source_span.redacted_payload() if source_span else None,
    }
    return _evidence_id(payload)


def _add_optional_reference_fields(payload: dict[str, object], ref: EvidenceReference) -> None:
    if ref.confidence is not None:
        payload["confidence"] = ref.confidence
    if ref.previous_evidence_id is not None:
        payload["previousEvidenceId"] = ref.previous_evidence_id


def _require_non_empty(*values: str) -> None:
    if any(not value for value in values):
        raise ValidationFailed("evidence reference is missing a pinned version")


def _evidence_id(payload: Mapping[str, object]) -> str:
    return "evidence_" + str(uuid.uuid5(_EVIDENCE_NAMESPACE, _json_dumps(payload)))


def _stable_hash(payload: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()[:16]


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
