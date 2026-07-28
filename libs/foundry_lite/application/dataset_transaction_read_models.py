"""Typed read models shared by Dataset transaction ports and recovery services."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

DatasetTransactionMetadata = Mapping[str, object]


class DatasetTransactionRow(TypedDict):
    id: str
    tenant_id: str
    dataset_id: str
    branch: str
    tx_type: str
    status: str
    base_version_id: str | None
    committed_version_id: str | None
    schema_version: int | None
    created_by: str
    created_at: str
    committed_at: str | None
    metadata: DatasetTransactionMetadata


class PipelineDatasetCommitRow(TypedDict):
    transaction_id: str
    dataset_id: str
    dataset_ref: str
    version_id: str
    version_number: int
    manifest_uri: str
    row_count: int
    schema_hash: str
    metadata: DatasetTransactionMetadata
    committed_at: str
