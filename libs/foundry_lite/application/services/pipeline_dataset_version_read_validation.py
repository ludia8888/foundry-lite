"""Integrity validation for exact committed Dataset-version reads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    DatasetFileRow,
    DatasetManifest,
    DatasetManifestFile,
    DatasetSchemaRow,
    DatasetTransactionRow,
    DatasetVersionRow,
    TabularRow,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_dataset_version_read_contracts import (
    ExactDatasetVersionReadRequest,
    ResolvedExactDatasetVersion,
    dataset_version_content_fingerprint,
    exact_dataset_version_request_failure,
)
from foundry_lite.domain.classification import (
    CLASSIFICATION_RANKS as _CLASSIFICATION_RANK,
)
from foundry_lite.domain.classification import (
    normalize_classification as _classification,
)
from foundry_lite.domain.context import RequestContext

_COMMITTED_VERSION_STATUSES = frozenset({"ACTIVE", "COMMITTED"})
_CONTENT_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def validate_exact_dataset_request(request: ExactDatasetVersionReadRequest) -> None:
    if request.descriptor_id != "source.dataset":
        raise exact_dataset_version_request_failure(request, "source_descriptor_not_dataset")
    if request.artifact_kind != "dataset_version":
        raise exact_dataset_version_request_failure(request, "source_artifact_kind_not_dataset_version")
    required = (request.dataset_ref, request.dataset_id, request.version_id, request.content_fingerprint)
    if not all(isinstance(value, str) and value.strip() for value in required):
        raise exact_dataset_version_request_failure(request, "source_coordinates_invalid")
    if request.version_number < 1:
        raise exact_dataset_version_request_failure(request, "source_version_number_invalid")


def validate_exact_dataset_metadata(
    ctx: RequestContext,
    request: ExactDatasetVersionReadRequest,
    resolved: ResolvedExactDatasetVersion,
) -> None:
    _validate_dataset_row(ctx, request, resolved)
    _validate_version_row(ctx, request, resolved.version)
    _validate_transaction_row(ctx, request, resolved.version, resolved.transaction)
    _validate_schema_row(request, resolved.version, resolved.schema)
    _validate_version_pin(request, resolved.version, resolved.schema)
    _validate_security_envelope(ctx, request, resolved.dataset)
    _validate_access_evidence(ctx, request)


def validate_exact_dataset_manifest(
    request: ExactDatasetVersionReadRequest,
    resolved: ResolvedExactDatasetVersion,
    manifest: DatasetManifest,
    storage_profile: str,
) -> None:
    version, schema = resolved.version, resolved.schema
    expected = {
        "version_id": request.version_id,
        "dataset": request.dataset_ref,
        "branch": version["branch"],
        "schema_hash": schema["schema_hash"],
        "storage_profile": storage_profile,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise exact_dataset_version_request_failure(request, "source_manifest_coordinates_mismatch")
    files = validated_manifest_files(request, manifest)
    _validate_manifest_totals(request, version, files)
    _validate_registered_files(request, files, resolved.files)


def validate_exact_dataset_rows(
    request: ExactDatasetVersionReadRequest,
    version: DatasetVersionRow,
    manifest: Mapping[str, object],
    rows: Sequence[TabularRow],
) -> None:
    manifest_rows = sum(file["row_count"] for file in manifest_files(manifest))
    if len(rows) != manifest_rows or len(rows) != version["row_count"]:
        raise exact_dataset_version_request_failure(
            request,
            "source_rows_count_mismatch",
            actualRowCount=len(rows),
            expectedRowCount=version["row_count"],
        )


def _validate_dataset_row(
    ctx: RequestContext,
    request: ExactDatasetVersionReadRequest,
    resolved: ResolvedExactDatasetVersion,
) -> None:
    dataset = resolved.dataset
    actual_ref = f"{dataset['namespace']}.{dataset['name']}"
    if dataset["tenant_id"] != ctx.tenant_id or dataset["id"] != request.dataset_id:
        raise exact_dataset_version_request_failure(request, "source_dataset_tenant_mismatch")
    if actual_ref != request.dataset_ref:
        raise exact_dataset_version_request_failure(
            request,
            "source_dataset_ref_mismatch",
            actualDatasetRef=actual_ref,
        )
    if dataset["status"].upper() != "ACTIVE":
        raise exact_dataset_version_request_failure(request, "source_dataset_not_active")


def _validate_version_row(
    ctx: RequestContext,
    request: ExactDatasetVersionReadRequest,
    version: DatasetVersionRow,
) -> None:
    if version["tenant_id"] != ctx.tenant_id or version["dataset_id"] != request.dataset_id:
        raise exact_dataset_version_request_failure(request, "source_version_tenant_mismatch")
    if version["id"] != request.version_id:
        raise exact_dataset_version_request_failure(request, "source_version_id_mismatch")
    if version["status"].upper() not in _COMMITTED_VERSION_STATUSES:
        raise exact_dataset_version_request_failure(request, "source_version_not_committed")


def _validate_transaction_row(
    ctx: RequestContext,
    request: ExactDatasetVersionReadRequest,
    version: DatasetVersionRow,
    transaction: DatasetTransactionRow,
) -> None:
    expected = {
        "tenant_id": ctx.tenant_id,
        "dataset_id": request.dataset_id,
        "branch": version["branch"],
        "status": "COMMITTED",
        "committed_version_id": request.version_id,
        "schema_version": version["schema_version"],
    }
    if any(transaction.get(key) != value for key, value in expected.items()):
        raise exact_dataset_version_request_failure(request, "source_commit_transaction_mismatch")
    if transaction["id"] != version["transaction_id"]:
        raise exact_dataset_version_request_failure(request, "source_commit_transaction_id_mismatch")


def _validate_schema_row(
    request: ExactDatasetVersionReadRequest,
    version: DatasetVersionRow,
    schema: DatasetSchemaRow,
) -> None:
    if schema["dataset_id"] != request.dataset_id or schema["version"] != version["schema_version"]:
        raise exact_dataset_version_request_failure(request, "source_schema_version_mismatch")
    if not isinstance(schema["schema_json"], Mapping) or not schema["schema_hash"]:
        raise exact_dataset_version_request_failure(request, "source_schema_invalid")
    if request.schema_hash != schema["schema_hash"] or request.schema_version != schema["version"]:
        raise exact_dataset_version_request_failure(request, "source_schema_pin_mismatch")
    pinned_hash = _json_hash({"schema": request.schema_contract})
    actual_hash = _json_hash({"schema": schema["schema_json"]})
    if pinned_hash != actual_hash:
        raise exact_dataset_version_request_failure(request, "source_schema_contract_mismatch")


def _validate_version_pin(
    request: ExactDatasetVersionReadRequest,
    version: DatasetVersionRow,
    schema: DatasetSchemaRow,
) -> None:
    fingerprint = dataset_version_content_fingerprint(version, schema["schema_hash"])
    if request.version_number != version["version_number"]:
        raise exact_dataset_version_request_failure(request, "source_version_number_mismatch")
    if request.content_fingerprint != fingerprint:
        raise exact_dataset_version_request_failure(request, "source_version_fingerprint_mismatch")
    expected = _version_metadata(version)
    if any(request.version_metadata.get(key) != value for key, value in expected.items()):
        raise exact_dataset_version_request_failure(request, "source_version_metadata_mismatch")


def _validate_security_envelope(
    ctx: RequestContext,
    request: ExactDatasetVersionReadRequest,
    dataset: Mapping[str, object],
) -> None:
    envelope = request.security_envelope
    source_classification = _classification(dataset.get("classification"))
    pinned_classification = _classification(envelope.get("classification"))
    if envelope.get("tenantId") != ctx.tenant_id or envelope.get("inheritance") != "source":
        raise exact_dataset_version_request_failure(request, "source_security_envelope_invalid")
    if envelope.get("ownerTeam") != dataset.get("owner_team"):
        raise exact_dataset_version_request_failure(request, "source_security_owner_mismatch")
    if _classification_is_weaker(pinned_classification, source_classification):
        raise exact_dataset_version_request_failure(request, "source_security_classification_weakened")


def _validate_access_evidence(
    ctx: RequestContext,
    request: ExactDatasetVersionReadRequest,
) -> None:
    evidence = request.access_evidence
    if evidence.get("tenantId") != ctx.tenant_id or evidence.get("permission") != "dataset:read":
        raise exact_dataset_version_request_failure(request, "source_access_evidence_invalid")
    if evidence.get("scopeEnforcement") != "tenant_scoped_repository":
        raise exact_dataset_version_request_failure(request, "source_access_scope_invalid")


def validated_manifest_files(
    request: ExactDatasetVersionReadRequest,
    manifest: Mapping[str, object],
) -> list[DatasetManifestFile]:
    files = manifest_files(manifest)
    if not files:
        raise exact_dataset_version_request_failure(request, "source_manifest_has_no_files")
    uris = [file.get("uri") for file in files]
    if len(set(uris)) != len(uris):
        raise exact_dataset_version_request_failure(request, "source_manifest_has_duplicate_files")
    for file in files:
        _validate_manifest_file(request, file)
    return files


def _validate_manifest_file(
    request: ExactDatasetVersionReadRequest,
    manifest_file: DatasetManifestFile,
) -> None:
    if manifest_file.get("format") != "parquet":
        raise exact_dataset_version_request_failure(request, "source_manifest_file_format_unsupported")
    _validate_manifest_file_counts(request, manifest_file)
    _validate_manifest_file_coordinates(request, manifest_file)


def _validate_manifest_file_counts(
    request: ExactDatasetVersionReadRequest,
    manifest_file: DatasetManifestFile,
) -> None:
    numeric = (manifest_file.get("row_count"), manifest_file.get("byte_size"))
    if any(isinstance(value, bool) or not isinstance(value, int) for value in numeric):
        raise exact_dataset_version_request_failure(request, "source_manifest_file_counts_invalid")
    if manifest_file["row_count"] < 0 or manifest_file["byte_size"] < 1:
        raise exact_dataset_version_request_failure(request, "source_manifest_file_counts_invalid")


def _validate_manifest_file_coordinates(
    request: ExactDatasetVersionReadRequest,
    manifest_file: DatasetManifestFile,
) -> None:
    uri, content_hash = manifest_file.get("uri"), manifest_file.get("content_hash")
    if not isinstance(uri, str) or not uri.strip():
        raise exact_dataset_version_request_failure(request, "source_manifest_file_uri_invalid")
    if not isinstance(content_hash, str) or not _CONTENT_HASH_PATTERN.fullmatch(content_hash):
        raise exact_dataset_version_request_failure(request, "source_manifest_file_hash_invalid")


def _validate_manifest_totals(
    request: ExactDatasetVersionReadRequest,
    version: DatasetVersionRow,
    files: Sequence[DatasetManifestFile],
) -> None:
    row_count = sum(file["row_count"] for file in files)
    byte_size = sum(file["byte_size"] for file in files)
    if row_count != version["row_count"] or byte_size != version["byte_size"]:
        raise exact_dataset_version_request_failure(
            request,
            "source_manifest_totals_mismatch",
            manifestRowCount=row_count,
            manifestByteSize=byte_size,
        )


def _validate_registered_files(
    request: ExactDatasetVersionReadRequest,
    files: Sequence[DatasetManifestFile],
    registered: Sequence[DatasetFileRow],
) -> None:
    expected = {_manifest_file_identity(file) for file in files}
    actual = {_registered_file_identity(file) for file in registered}
    if len(registered) != len(files) or actual != expected:
        raise exact_dataset_version_request_failure(
            request,
            "source_manifest_file_registry_mismatch",
            manifestFileCount=len(files),
            registeredFileCount=len(registered),
        )


def manifest_files(manifest: Mapping[str, object]) -> list[DatasetManifestFile]:
    value = manifest.get("files")
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        return []
    return [cast(DatasetManifestFile, dict(item)) for item in value]


def _manifest_file_identity(file: DatasetManifestFile) -> tuple[object, ...]:
    return (
        file["uri"],
        file["format"],
        file["row_count"],
        file["byte_size"],
        file["content_hash"],
        _json_hash({"partitionValues": file.get("partition_values") or {}}),
    )


def _registered_file_identity(file: DatasetFileRow) -> tuple[object, ...]:
    return (
        file["uri"],
        file["format"],
        file["row_count"],
        file["byte_size"],
        file["content_hash"],
        _json_hash({"partitionValues": dict(file["partition_values"])}),
    )


def _version_metadata(version: DatasetVersionRow) -> dict[str, object]:
    return {
        "versionNumber": version["version_number"],
        "branch": version["branch"],
        "manifestUri": version["manifest_uri"],
        "rowCount": version["row_count"],
        "byteSize": version["byte_size"],
        "status": version["status"],
        "schemaVersion": version["schema_version"],
    }


def _classification_is_weaker(candidate: str, source: str) -> bool:
    if candidate in _CLASSIFICATION_RANK and source in _CLASSIFICATION_RANK:
        return _CLASSIFICATION_RANK[candidate] < _CLASSIFICATION_RANK[source]
    return candidate != source
