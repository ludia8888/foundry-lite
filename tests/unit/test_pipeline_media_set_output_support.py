from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest
from foundry_lite.application.ports.media_derivative_repository import MediaDerivativeRecord
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord, MediaSetRecord
from foundry_lite.application.ports.media_storage import MediaObjectStat, MediaStorageAdapter
from foundry_lite.application.services.pipeline_media_set_output_contracts import (
    MediaOutputContract,
    MediaOutputEntry,
    PipelineMediaDerivativeBytesUnavailable,
    PipelineMediaOutputSourceCorrupt,
    PipelineMediaSetOutputContractMismatch,
)
from foundry_lite.application.services.pipeline_media_set_output_support import (
    derivative_bytes,
    derivative_logical_path,
    format_for_mime,
    item_text,
    media_output_entry,
    media_set_ref_parts,
    output_contract,
    pipeline_output_lineage,
    require_derivative_coordinates,
    require_item_coordinate,
    require_target_contract,
    required_text,
    schema_for_mime,
    validate_output_versions,
    versions_by_entry,
)


def _version(**overrides: object) -> MediaItemVersionRecord:
    values: dict[str, object] = {
        "media_item_version_id": "mv-source",
        "tenant_id": "tenant-demo",
        "media_item_id": "mi-source",
        "media_transaction_id": "mtx-source",
        "version_number": 1,
        "blob_key": "source/report.pdf",
        "content_hash": "a" * 64,
        "byte_size": 12,
        "supplied_mime_type": "application/pdf",
        "sniffed_mime_type": "application/pdf",
        "schema_type": "document",
        "format": "pdf",
        "probe_metadata": {},
        "security_envelope": {"classification": "INTERNAL"},
        "source_ref": None,
        "status": "COMMITTED",
        "created_at": "2026-07-28T00:00:00Z",
    }
    values.update(overrides)
    return MediaItemVersionRecord(**values)  # type: ignore[arg-type]


def _media_set(**overrides: object) -> MediaSetRecord:
    values: dict[str, object] = {
        "media_set_id": "ms-target",
        "tenant_id": "tenant-demo",
        "namespace": "clean",
        "name": "reports",
        "schema_type": "document",
        "primary_format": "pdf",
        "allowed_input_formats": ("pdf",),
        "transaction_policy": "transactional",
        "storage_profile": "local",
        "processing_profile": "pipeline_graph_v2",
        "classification": "INTERNAL",
        "retention_policy_id": None,
        "created_at": "2026-07-28T00:00:00Z",
        "updated_at": "2026-07-28T00:00:00Z",
    }
    values.update(overrides)
    return MediaSetRecord(**values)  # type: ignore[arg-type]


def _entry(**overrides: object) -> MediaOutputEntry:
    values: dict[str, object] = {
        "entry_fingerprint": "entry-1",
        "source_media_item_version_id": "mv-source",
        "media_derivative_id": None,
        "logical_path": "report.pdf",
        "blob_key": "source/report.pdf",
        "content_hash": "a" * 64,
        "byte_size": 12,
        "mime_type": "application/pdf",
        "schema_type": "document",
        "format": "pdf",
        "primary_format": "pdf",
        "allowed_formats": ("pdf",),
        "security_envelope": {"classification": "INTERNAL"},
    }
    values.update(overrides)
    return MediaOutputEntry(**values)  # type: ignore[arg-type]


def _derivative(**overrides: object) -> MediaDerivativeRecord:
    values: dict[str, object] = {
        "media_derivative_id": "md-1",
        "tenant_id": "tenant-demo",
        "source_media_item_version_id": "mv-source",
        "derivative_kind": "page.text",
        "processor_spec_hash": "spec",
        "processor_name": "pdf",
        "processor_version": "1",
        "model_name": None,
        "model_version": "1",
        "params_hash": "params",
        "security_envelope": {"classification": "INTERNAL"},
        "status": "COMMITTED",
        "blob_key": "derivatives/page.json",
        "content_hash": "b" * 64,
        "byte_size": 7,
        "mime_type": "application/json",
    }
    values.update(overrides)
    return MediaDerivativeRecord(**values)  # type: ignore[arg-type]


class _Storage:
    def __init__(self, *, is_present: bool = True) -> None:
        self.is_present = is_present

    def stat(self, object_key: str) -> MediaObjectStat:
        return MediaObjectStat(
            object_key=object_key,
            byte_size=12,
            content_hash="a" * 64,
            is_present=self.is_present,
        )


def test_media_output_entry_and_contract_preserve_committed_coordinates() -> None:
    source = _version()
    derivative = _derivative()
    direct = media_output_entry(
        source_version=source,
        media_set=_media_set(),
        logical_path="report.pdf",
        blob_key=source.blob_key,
        content_hash=source.content_hash,
        byte_size=source.byte_size,
        mime_type=source.sniffed_mime_type,
        schema_type=source.schema_type,
        format=source.format,
    )
    derived = media_output_entry(
        source_version=source,
        media_set=None,
        logical_path="report.page-text.json",
        blob_key=cast(str, derivative.blob_key),
        content_hash=cast(str, derivative.content_hash),
        byte_size=cast(int, derivative.byte_size),
        mime_type=cast(str, derivative.mime_type),
        schema_type="document",
        format="json",
        derivative=derivative,
    )

    assert direct.media_derivative_id is None
    assert derived.media_derivative_id == "md-1"
    assert derived.primary_format == "json"
    assert output_contract([direct]) == MediaOutputContract("document", "pdf", ("pdf",), "INTERNAL")

    with pytest.raises(PipelineMediaSetOutputContractMismatch, match="safe target contract"):
        output_contract([direct, replace(derived, schema_type="image")])
    with pytest.raises(PipelineMediaSetOutputContractMismatch, match="primary format"):
        output_contract([direct, replace(direct, entry_fingerprint="entry-2", primary_format="json")])


def test_target_media_contract_reports_every_unsafe_mismatch() -> None:
    required = MediaOutputContract("document", "pdf", ("pdf", "txt"), "CONFIDENTIAL")
    require_target_contract(
        _media_set(
            allowed_input_formats=("pdf", "txt"),
            classification="CONFIDENTIAL",
        ),
        required,
        "clean.reports",
    )

    unsafe = _media_set(
        schema_type="image",
        primary_format="png",
        allowed_input_formats=("png",),
        transaction_policy="append",
        classification="PUBLIC",
        is_virtual=True,
    )
    with pytest.raises(PipelineMediaSetOutputContractMismatch) as raised:
        require_target_contract(unsafe, required, "clean.reports")
    assert set(cast(dict[str, object], raised.value.details["mismatches"])) == {
        "transactionPolicy",
        "schemaType",
        "primaryFormat",
        "allowedInputFormats",
        "classification",
    }


def test_output_version_validation_checks_lineage_status_and_durable_bytes() -> None:
    entry = _entry()
    target = _media_set()
    lineage = {
        "pipelineOutput": {
            "entryFingerprint": entry.entry_fingerprint,
            "requestFingerprint": "request-1",
            "transactionGeneration": 2,
        }
    }
    version = _version(
        media_item_version_id="mv-output",
        source_ref=lineage,
        status="STAGED",
    )
    storage = cast(MediaStorageAdapter, _Storage())

    validate_output_versions([version], [entry], target, "request-1", 2, storage)
    with pytest.raises(PipelineMediaSetOutputContractMismatch, match="failed validation"):
        validate_output_versions([version], [entry], target, "request-1", 2, storage, is_committed=True)
    with pytest.raises(PipelineMediaSetOutputContractMismatch, match="failed validation"):
        validate_output_versions(
            [replace(version, status="COMMITTED")],
            [entry],
            target,
            "request-1",
            2,
            cast(MediaStorageAdapter, _Storage(is_present=False)),
            is_committed=True,
        )

    assert pipeline_output_lineage(version)["entryFingerprint"] == "entry-1"
    assert pipeline_output_lineage(replace(version, source_ref=None)) == {}
    assert versions_by_entry([version]) == {"entry-1": version}
    with pytest.raises(PipelineMediaSetOutputContractMismatch, match="ambiguous lineage"):
        versions_by_entry([replace(version, source_ref={})])
    with pytest.raises(PipelineMediaSetOutputContractMismatch, match="ambiguous lineage"):
        versions_by_entry([version, replace(version, media_item_version_id="mv-duplicate")])


def test_derivative_and_coordinate_helpers_reject_corrupt_artifacts() -> None:
    derivative = _derivative()
    assert derivative_bytes(derivative) == ("derivatives/page.json", "b" * 64, 7, "application/json")
    assert derivative_logical_path("folder/report.pdf", derivative, "json") == "folder/report.page-text.json"
    assert derivative_logical_path("folder/.pdf", replace(derivative, derivative_kind="!!!"), "txt") == (
        "folder/.pdf.derivative.txt"
    )
    require_item_coordinate({"mediaItemVersionId": "mv-source"}, "mediaItemVersionId", "mv-source", "md-1")
    require_derivative_coordinates(
        {
            "mediaItemVersionId": "mv-source",
            "contentHash": "b" * 64,
            "securityEnvelope": {"classification": "INTERNAL"},
        },
        derivative,
    )

    with pytest.raises(PipelineMediaDerivativeBytesUnavailable):
        derivative_bytes(replace(derivative, blob_key=None))
    with pytest.raises(PipelineMediaOutputSourceCorrupt, match="coordinate"):
        require_item_coordinate({}, "mediaItemVersionId", "mv-source", "md-1")
    with pytest.raises(PipelineMediaOutputSourceCorrupt, match="committed truth"):
        require_derivative_coordinates(
            {
                "mediaItemVersionId": "mv-source",
                "contentHash": "wrong",
                "securityEnvelope": {"classification": "INTERNAL"},
            },
            derivative,
        )


def test_media_output_text_mime_and_reference_helpers_cover_safe_boundaries() -> None:
    assert format_for_mime("application/pdf") == "pdf"
    assert format_for_mime("application/vnd.custom") == "vnd.custom"
    assert [schema_for_mime(value) for value in ("image/png", "audio/wav", "video/mp4", "text/plain")] == [
        "image",
        "audio",
        "video",
        "document",
    ]
    assert schema_for_mime("application/pdf") == "document"
    assert schema_for_mime("application/octet-stream") == "binary"
    assert media_set_ref_parts(" clean.reports ") == ("clean", "reports")
    assert item_text({"path": " report.pdf "}, "path") == "report.pdf"
    assert required_text({"target": " clean.reports "}, "target", "out") == "clean.reports"

    failures = (
        lambda: format_for_mime("application/???"),
        lambda: media_set_ref_parts("reports"),
        lambda: required_text({}, "target", "out"),
    )
    for failure in failures:
        with pytest.raises(PipelineMediaSetOutputContractMismatch):
            failure()
    with pytest.raises(PipelineMediaOutputSourceCorrupt):
        item_text({}, "path")
