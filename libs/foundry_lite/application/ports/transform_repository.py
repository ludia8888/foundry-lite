from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


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
    checks: list[dict[str, Any]]


@dataclass(frozen=True)
class TransformRunRecord:
    transform_run_id: str
    tenant_id: str
    transform_id: str
    status: str
    input_versions: dict[str, str]
    output_version_id: str | None
    transaction_id: str
    error: dict[str, Any] | None
    created_at: str
    completed_at: str | None


class TransformRepository(Protocol):
    """DB boundary for transform registry and transform run persistence."""

    def transform_by_api_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        api_name: str,
    ) -> dict[str, Any] | None:
        """Return a transform row by tenant + api_name, or None."""
        ...

    def insert_transform(self, *, transaction: Any, record: TransformRecord) -> None:
        """Persist a newly registered transform."""
        ...

    def update_transform_definition(
        self,
        *,
        transaction: Any,
        transform_id: str,
        language: str,
        entrypoint: str,
        mode: str,
        inputs: dict[str, str],
        output_dataset_ref: str,
        checks: list[dict[str, Any]],
    ) -> None:
        """Update an existing transform's definition fields."""
        ...

    def transform_by_id(self, *, transaction: Any, transform_id: str) -> dict[str, Any] | None:
        """Return a transform row by id, or None."""
        ...

    def insert_transform_run(self, *, transaction: Any, record: TransformRunRecord) -> None:
        """Persist a newly received transform run."""
        ...

    def update_transform_run_terminal(
        self,
        *,
        transaction: Any,
        transform_run_id: str,
        status: str,
        output_version_id: str | None,
        error: dict[str, Any] | None,
        completed_at: str,
    ) -> None:
        """Mark a transform run as terminal (SUCCESS/FAILED/ABORTED)."""
        ...
