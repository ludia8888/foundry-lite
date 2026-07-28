"""Typed contracts for exact committed Dataset-version reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from foundry_lite.application.ports import (
    DatasetFileRow,
    DatasetManifest,
    DatasetRow,
    DatasetSchemaRow,
    DatasetTransactionRow,
    DatasetVersionRow,
    TabularRow,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2SourceContract,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation


class ExactDatasetVersionReadFailed(InvariantViolation):
    """Typed failure for unavailable, drifted, or corrupted pinned Dataset input."""

    code = "EXACT_DATASET_VERSION_READ_FAILED"


@dataclass(frozen=True, slots=True)
class ExactDatasetVersionReadRequest:
    """Runtime coordinates copied from one deployed ``source.dataset`` contract."""

    node_id: str
    descriptor_id: str
    artifact_kind: str
    dataset_ref: str
    dataset_id: str
    version_id: str
    version_number: int
    content_fingerprint: str
    version_metadata: Mapping[str, object]
    schema_contract: Mapping[str, object]
    schema_hash: str
    schema_version: int | None
    security_envelope: Mapping[str, object]
    access_evidence: Mapping[str, object]

    @classmethod
    def from_source_contract(
        cls,
        contract: PipelineV2SourceContract,
    ) -> ExactDatasetVersionReadRequest:
        if len(contract.version_pins) != 1:
            raise exact_dataset_version_read_failure(
                "source_contract_requires_one_exact_version",
                node_id=contract.node_id,
                dataset_ref=contract.resource_ref,
            )
        pin = contract.version_pins[0]
        dataset_ref, dataset_id = _backing_dataset_coordinates(contract, pin.metadata)
        return cls(
            node_id=contract.node_id,
            descriptor_id="source.dataset",
            artifact_kind="dataset_version",
            dataset_ref=dataset_ref,
            dataset_id=dataset_id,
            version_id=pin.version_id,
            version_number=pin.ordinal,
            content_fingerprint=pin.content_fingerprint,
            version_metadata=pin.metadata,
            schema_contract=contract.schema_contract,
            schema_hash=contract.schema_hash,
            schema_version=contract.schema_version,
            security_envelope=contract.security_envelope,
            access_evidence=contract.access_evidence,
        )


@dataclass(frozen=True, slots=True)
class ExactDatasetVersionReadResult:
    """Authoritative rows and evidence for one exact immutable Dataset version."""

    dataset_ref: str
    dataset_id: str
    version_id: str
    version_number: int
    transaction_id: str
    branch: str
    manifest_uri: str
    rows: tuple[TabularRow, ...]
    schema_contract: Mapping[str, object]
    schema_hash: str
    schema_version: int
    manifest: Mapping[str, object]
    security_envelope: Mapping[str, object]
    access_evidence: Mapping[str, object]
    content_fingerprint: str

    def artifact_ref(self) -> dict[str, object]:
        return {
            "datasetRef": self.dataset_ref,
            "datasetId": self.dataset_id,
            "versionId": self.version_id,
            "versionNumber": self.version_number,
            "transactionId": self.transaction_id,
        }

    def runtime_manifest(self) -> dict[str, object]:
        files = self.manifest.get("files")
        file_count = len(files) if isinstance(files, tuple | list) else 0
        return {
            "manifestUri": self.manifest_uri,
            "fileCount": file_count,
            "rowCount": len(self.rows),
            "schemaHash": self.schema_hash,
            "schemaVersion": self.schema_version,
            "branch": self.branch,
        }


@dataclass(frozen=True, slots=True)
class ResolvedExactDatasetVersion:
    """Repository truth read in one transaction before storage access."""

    dataset: DatasetRow
    version: DatasetVersionRow
    schema: DatasetSchemaRow
    transaction: DatasetTransactionRow
    files: tuple[DatasetFileRow, ...]


def dataset_version_content_fingerprint(version: DatasetVersionRow, schema_hash: str) -> str:
    """Return the canonical fingerprint stored in Pipeline source contracts."""

    return _json_hash(
        {
            "versionId": version["id"],
            "manifestUri": version["manifest_uri"],
            "rowCount": version["row_count"],
            "byteSize": version["byte_size"],
            "schemaHash": schema_hash,
        }
    )


def _backing_dataset_coordinates(
    contract: PipelineV2SourceContract,
    metadata: Mapping[str, object],
) -> tuple[str, str]:
    if contract.descriptor_id == "source.dataset":
        return contract.resource_ref, contract.source_id
    dataset_ref = metadata.get("backingDatasetRef")
    dataset_id = metadata.get("backingDatasetId")
    if not isinstance(dataset_ref, str) or not dataset_ref.strip():
        raise exact_dataset_version_read_failure(
            "backing_dataset_ref_missing",
            node_id=contract.node_id,
            dataset_ref=contract.resource_ref,
        )
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise exact_dataset_version_read_failure(
            "backing_dataset_id_missing",
            node_id=contract.node_id,
            dataset_ref=contract.resource_ref,
        )
    return dataset_ref.strip(), dataset_id.strip()


def exact_dataset_version_request_failure(
    request: ExactDatasetVersionReadRequest,
    reason: str,
    **details: object,
) -> ExactDatasetVersionReadFailed:
    return exact_dataset_version_read_failure(
        reason,
        node_id=request.node_id,
        dataset_ref=request.dataset_ref,
        version_id=request.version_id,
        **details,
    )


def exact_dataset_version_read_failure(
    reason: str,
    *,
    node_id: str,
    dataset_ref: str,
    version_id: str | None = None,
    **details: object,
) -> ExactDatasetVersionReadFailed:
    payload: dict[str, object] = {
        "reason": reason,
        "nodeId": node_id,
        "datasetRef": dataset_ref,
        **details,
    }
    if version_id is not None:
        payload["versionId"] = version_id
    return ExactDatasetVersionReadFailed(
        "exact committed dataset version cannot be read",
        details=payload,
    )


def exact_dataset_access_evidence(ctx: RequestContext) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "tenantId": ctx.tenant_id,
            "principalId": ctx.actor_user_id,
            "requestId": ctx.request_id,
            "permission": "dataset:read",
            "scopeEnforcement": "tenant_scoped_exact_version",
        }
    )


def freeze_exact_dataset_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return freeze_exact_dataset_mapping({str(key): item for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def exact_dataset_result(
    *,
    ctx: RequestContext,
    request: ExactDatasetVersionReadRequest,
    resolved: ResolvedExactDatasetVersion,
    manifest: DatasetManifest,
    rows: tuple[TabularRow, ...],
) -> ExactDatasetVersionReadResult:
    version, schema = resolved.version, resolved.schema
    return ExactDatasetVersionReadResult(
        dataset_ref=request.dataset_ref,
        dataset_id=request.dataset_id,
        version_id=request.version_id,
        version_number=version["version_number"],
        transaction_id=version["transaction_id"],
        branch=version["branch"],
        manifest_uri=version["manifest_uri"],
        rows=rows,
        schema_contract=freeze_exact_dataset_mapping(schema["schema_json"]),
        schema_hash=schema["schema_hash"],
        schema_version=schema["version"],
        manifest=freeze_exact_dataset_mapping(manifest),
        security_envelope=freeze_exact_dataset_mapping(request.security_envelope),
        access_evidence=exact_dataset_access_evidence(ctx),
        content_fingerprint=request.content_fingerprint,
    )
