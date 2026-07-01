"""Application port contract for transform repository."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext

TransformCheck = Mapping[str, object]


class TransformRow(TypedDict):
    id: str
    tenant_id: str
    api_name: str
    language: str
    entrypoint: str
    mode: str
    inputs: dict[str, str]
    output_dataset_ref: str
    checks: list[dict[str, object]]


@dataclass(frozen=True)
class TransformRecord:
    transform_id: str
    tenant_id: str
    api_name: str
    language: str
    entrypoint: str
    mode: str
    inputs: dict[str, str]
    output_dataset_ref: str
    checks: list[dict[str, object]]


@dataclass(frozen=True)
class TransformRunRecord:
    transform_run_id: str
    tenant_id: str
    transform_id: str
    status: str
    input_versions: dict[str, str]
    definition_snapshot: dict[str, object]
    output_version_id: str | None
    transaction_id: str
    error: Mapping[str, object] | None
    created_at: str
    completed_at: str | None


class TransformRunRow(TypedDict):
    """Persisted transform run row used by Operations retry workflows."""

    id: str
    tenant_id: str
    transform_id: str
    status: str
    input_versions: dict[str, str]
    definition_snapshot: dict[str, object] | None
    output_version_id: str | None
    transaction_id: str
    error: Mapping[str, object] | None
    created_at: str
    completed_at: str | None


class TransformRetryResult(TypedDict):
    """Operator response returned after retrying a failed transform run."""

    original_run_id: str
    transform_run_id: str
    dataset_id: str
    dataset_ref: str
    transaction_id: str
    version_id: str
    version_number: int
    row_count: int
    manifest_uri: str
    schema_hash: str


class TransformRecordDlqRetryResult(TypedDict):
    """Operator response returned after replaying a transform row quarantine."""

    deadLetterRecordId: str
    status: str
    replayStatus: str
    replayRunId: str
    transformRunId: str | None
    isIdempotentReplay: bool
    replayDatasetVersionId: str | None
    rowCount: int | None
    error: Mapping[str, object] | None
    downstreamBackfillPlan: Mapping[str, object]


class TransformRepository(Protocol):
    """DB boundary for transform registry and transform run persistence."""

    def transform_by_api_name(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        api_name: str,
    ) -> TransformRow | None:
        """Return a transform row by tenant + api_name, or None."""
        ...

    def insert_transform(self, *, transaction: TransactionContext, record: TransformRecord) -> None:
        """Persist a newly registered transform."""
        ...

    def update_transform_definition(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transform_id: str,
        language: str,
        entrypoint: str,
        mode: str,
        inputs: dict[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck],
    ) -> None:
        """Update an existing transform's definition fields."""
        ...

    def transform_by_id(self, *, transaction: TransactionContext, transform_id: str) -> TransformRow | None:
        """Return a transform row by id, or None."""
        ...

    def list_transforms(self, *, transaction: TransactionContext, tenant_id: str) -> list[TransformRow]:
        """Return all transform definitions visible to a tenant."""
        ...

    def transform_run_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transform_run_id: str,
    ) -> TransformRunRow | None:
        """Return a tenant-scoped transform run row by id, or None."""
        ...

    def latest_successful_transform_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transform_id: str,
    ) -> TransformRunRow | None:
        """Return the latest successful run for one tenant-scoped transform."""
        ...

    def running_transform_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transform_id: str,
    ) -> TransformRunRow | None:
        """Return a running run for one tenant-scoped transform, if any."""
        ...

    def insert_transform_run(self, *, transaction: TransactionContext, record: TransformRunRecord) -> None:
        """Persist a newly received transform run."""
        ...

    def update_transform_run_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transform_run_id: str,
        transition: StatusTransition,
        output_version_id: str | None,
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> bool:
        """CAS a transform run from an allowed state into a terminal state."""
        ...
