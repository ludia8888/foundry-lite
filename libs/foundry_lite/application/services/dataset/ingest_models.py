"""Application service helpers for ingest models workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports import DatasetRow


@dataclass(frozen=True)
class UploadSyncPlan:
    """Identifiers opened before converting one external ingest."""

    transaction_id: str
    run_id: str


@dataclass(frozen=True)
class ConnectorSnapshotSync:
    dataset: DatasetRow
    plan: UploadSyncPlan
    staged: Path
    resume_cursor: Mapping[str, object] | None


@dataclass(frozen=True)
class WebhookIngest:
    dataset: DatasetRow
    event_id: str
    payload_hash: str
