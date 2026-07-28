"""Authoritative content-unit evidence resolution for citations and navigation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.citation_source import CitationSourceEvidence
from foundry_lite.application.ports.content_index import is_classification_cleared
from foundry_lite.application.ports.media_derivative_repository import (
    ContentUnitRecord,
    MediaDerivativeRecord,
    MediaDerivativeRepository,
)
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.clearance import allowed_media_classifications
from foundry_lite.application.services.media.document_context_evidence import (
    document_context_from_unit,
    document_context_hash,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied


@dataclass(frozen=True, slots=True)
class ResolvedContentUnitEvidence:
    """Safe verified evidence; raw content text is intentionally not retained."""

    source_resource_id: str
    source_version: str
    content_hash: str
    display_label: str
    navigation_path: str
    evidence: CitationSourceEvidence


class ContentUnitEvidenceService(CoreService):
    """Re-read one content unit and its immutable derivative/media ancestry."""

    required_dependencies = ("engine", "policy", "media_repository", "media_derivative_repository")
    required_collaborators = ()

    def resolve(
        self,
        ctx: RequestContext,
        *,
        media_item_version_id: str,
        content_unit_id: str,
    ) -> ResolvedContentUnitEvidence:
        self.policy.require(ctx, "object:read")
        version, derivative, unit = self._read_records(
            ctx,
            media_item_version_id=media_item_version_id,
            content_unit_id=content_unit_id,
        )
        _validate_evidence_chain(ctx, version, derivative, unit)
        context_hash = document_context_hash(document_context_from_unit(unit))
        evidence = _citation_evidence(version, derivative, unit)
        return ResolvedContentUnitEvidence(
            source_resource_id=f"content-unit://{media_item_version_id}/{content_unit_id}",
            source_version=media_item_version_id,
            content_hash=context_hash,
            display_label=f"content_unit:{content_unit_id}@{media_item_version_id}",
            navigation_path="/document-intelligence",
            evidence=evidence,
        )

    def _read_records(
        self,
        ctx: RequestContext,
        *,
        media_item_version_id: str,
        content_unit_id: str,
    ) -> tuple[MediaItemVersionRecord, MediaDerivativeRecord, ContentUnitRecord]:
        with self.engine.begin() as transaction:
            unit = self.media_derivative_repository.content_unit_by_id(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                content_unit_id=content_unit_id,
            )
            version = self.media_repository.media_item_version_by_id(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                media_item_version_id=media_item_version_id,
            )
            derivative = _derivative_for_unit(
                self.media_derivative_repository,
                transaction,
                ctx.tenant_id,
                unit,
            )
        if unit is None or version is None or derivative is None:
            raise NotFound(
                "content unit citation source not found",
                details={"mediaItemVersionId": media_item_version_id, "contentUnitId": content_unit_id},
            )
        return version, derivative, unit


def _derivative_for_unit(
    repository: MediaDerivativeRepository,
    transaction: TransactionContext,
    tenant_id: str,
    unit: ContentUnitRecord | None,
) -> MediaDerivativeRecord | None:
    if unit is None:
        return None
    return repository.derivative_by_id(
        transaction=transaction,
        tenant_id=tenant_id,
        media_derivative_id=unit.derivative_id,
    )


def _validate_evidence_chain(
    ctx: RequestContext,
    version: MediaItemVersionRecord,
    derivative: MediaDerivativeRecord,
    unit: ContentUnitRecord,
) -> None:
    _require_committed(version, derivative)
    _require_identity_chain(ctx, version, derivative, unit)
    _require_processor_pins(derivative)
    _require_security_inheritance(version, derivative, unit)
    _require_pdf_locator(version, unit)
    classification = str(version.security_envelope.get("classification", ""))
    if not is_classification_cleared(classification, allowed_media_classifications(ctx)):
        raise PermissionDenied("content unit citation classification is not cleared")


def _require_committed(version: MediaItemVersionRecord, derivative: MediaDerivativeRecord) -> None:
    if version.status != "COMMITTED" or derivative.status != "COMMITTED":
        raise ConflictDetected(
            "content unit citation requires committed media and derivative",
            details={"mediaStatus": version.status, "derivativeStatus": derivative.status},
        )


def _require_identity_chain(
    ctx: RequestContext,
    version: MediaItemVersionRecord,
    derivative: MediaDerivativeRecord,
    unit: ContentUnitRecord,
) -> None:
    expected = (ctx.tenant_id, version.media_item_version_id, derivative.media_derivative_id)
    actual = (unit.tenant_id, unit.source_media_item_version_id, unit.derivative_id)
    derivative_identity = (derivative.tenant_id, derivative.source_media_item_version_id)
    if actual != expected or derivative_identity != expected[:2] or version.tenant_id != ctx.tenant_id:
        raise ConflictDetected("content unit citation identity chain does not match")


def _require_processor_pins(derivative: MediaDerivativeRecord) -> None:
    required = (
        derivative.processor_spec_hash,
        derivative.processor_name,
        derivative.processor_version,
        derivative.params_hash,
    )
    if not all(value.strip() for value in required):
        raise ConflictDetected("content unit citation derivative pins are incomplete")
    if derivative.model_name and not derivative.model_version.strip():
        raise ConflictDetected("content unit citation model version pin is missing")


def _require_security_inheritance(
    version: MediaItemVersionRecord,
    derivative: MediaDerivativeRecord,
    unit: ContentUnitRecord,
) -> None:
    source = version.security_envelope
    if not _inherits(source, derivative.security_envelope) or not _inherits(source, unit.security_envelope):
        raise ConflictDetected("content unit citation security envelope was weakened")


def _require_pdf_locator(version: MediaItemVersionRecord, unit: ContentUnitRecord) -> None:
    if not _is_pdf_version(version):
        return
    if not _has_navigable_pdf_coordinates(unit):
        raise ConflictDetected("PDF citation requires a verified page and bounding box")
    if not _source_locator_matches_unit(unit):
        raise ConflictDetected("PDF citation source locator does not match the committed bounding box")


def _is_pdf_version(version: MediaItemVersionRecord) -> bool:
    return version.sniffed_mime_type == "application/pdf" or version.format.lower() == "pdf"


def _has_navigable_pdf_coordinates(unit: ContentUnitRecord) -> bool:
    return unit.page_number is not None and unit.page_number > 0 and _bbox_is_navigable(unit.bbox)


def _source_locator_matches_unit(unit: ContentUnitRecord) -> bool:
    locator = unit.source_locator
    if locator is None:
        return False
    coordinate_system = locator.get("coordinateSystem")
    return (
        locator.get("pageNumber") == unit.page_number
        and locator.get("bbox") == unit.bbox
        and isinstance(coordinate_system, str)
        and bool(coordinate_system)
    )


def _bbox_is_navigable(bbox: Mapping[str, object] | None) -> bool:
    if bbox is None:
        return False
    horizontal = bbox.get("x", bbox.get("left"))
    vertical = bbox.get("y", bbox.get("top"))
    required = (
        horizontal,
        vertical,
        bbox.get("width"),
        bbox.get("height"),
        bbox.get("pageWidth"),
        bbox.get("pageHeight"),
    )
    numeric: list[float] = []
    for value in required:
        if not isinstance(value, int | float) or isinstance(value, bool):
            return False
        numeric.append(float(value))
    return all(value > 0 for value in numeric[2:])


def _inherits(source: Mapping[str, object], derived: Mapping[str, object]) -> bool:
    return all(derived.get(key) == value for key, value in source.items())


def _citation_evidence(
    version: MediaItemVersionRecord,
    derivative: MediaDerivativeRecord,
    unit: ContentUnitRecord,
) -> CitationSourceEvidence:
    return CitationSourceEvidence(
        media_item_version_id=version.media_item_version_id,
        media_derivative_id=derivative.media_derivative_id,
        content_unit_id=unit.content_unit_id,
        page_number=unit.page_number,
        bbox=dict(unit.bbox) if unit.bbox is not None else None,
        timecode=_timecode(unit),
        source_locator=dict(unit.source_locator) if unit.source_locator is not None else None,
        derivative_kind=derivative.derivative_kind,
        processor_name=derivative.processor_name,
        processor_version=derivative.processor_version,
        processor_spec_hash=derivative.processor_spec_hash,
        model_name=derivative.model_name,
        model_version=derivative.model_version if derivative.model_name else None,
        params_hash=derivative.params_hash,
        security_envelope=_public_security_envelope(version),
    )


def _timecode(unit: ContentUnitRecord) -> dict[str, object] | None:
    if unit.start_ms is None and unit.end_ms is None:
        return None
    return {"startMs": unit.start_ms, "endMs": unit.end_ms}


def _public_security_envelope(version: MediaItemVersionRecord) -> dict[str, object]:
    envelope = version.security_envelope
    return {
        "tenantId": version.tenant_id,
        "classification": envelope.get("classification", ""),
        "policyVersion": envelope.get("policyVersion", ""),
        "hasLegalHold": version.has_legal_hold,
    }
