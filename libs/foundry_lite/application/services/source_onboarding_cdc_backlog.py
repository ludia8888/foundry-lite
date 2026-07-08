"""CDC object-indexer backlog view helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports import DatasetVersionRow
from foundry_lite.domain.context import RequestContext


class CdcBacklogDatasetBoundary(Protocol):
    def list_dataset_versions(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[DatasetVersionRow]: ...


@dataclass(frozen=True)
class CdcObjectIndexBacklog:
    latest_source_dataset_version_id: str | None
    latest_source_dataset_version_number: int | None
    next_source_dataset_version_id: str | None
    next_source_dataset_version_number: int | None
    remaining_version_count: int

    @property
    def has_more_versions(self) -> bool:
        return self.remaining_version_count > 0


def cdc_version_backlog(
    dataset_registry_service: CdcBacklogDatasetBoundary,
    ctx: RequestContext,
    dataset_ref: str,
    cursor: Mapping[str, object],
) -> CdcObjectIndexBacklog:
    versions = tuple(dataset_registry_service.list_dataset_versions(dataset_ref, ctx=ctx))
    latest = versions[-1] if versions else None
    last_number = int_cursor(cursor, "lastIndexedVersionNumber")
    remaining = tuple(version for version in versions if _is_after_cursor(version, last_number))
    next_version = remaining[0] if remaining else None
    return CdcObjectIndexBacklog(
        latest_source_dataset_version_id=str(latest["id"]) if latest is not None else None,
        latest_source_dataset_version_number=int(latest["version_number"]) if latest is not None else None,
        next_source_dataset_version_id=str(next_version["id"]) if next_version is not None else None,
        next_source_dataset_version_number=int(next_version["version_number"]) if next_version is not None else None,
        remaining_version_count=len(remaining),
    )


def cdc_indexer_workflow_key(object_type_api_name: str, dataset_ref: str) -> str:
    return f"cdc-object-indexer:{object_type_api_name}:{dataset_ref}"


def cdc_cursor_from_workflow(row: Mapping[str, object] | None) -> dict[str, object]:
    if row is None:
        return {}
    output = row.get("output")
    if not isinstance(output, Mapping):
        return {}
    return cdc_cursor_from_output(output)


def cdc_cursor_from_output(output: Mapping[str, object]) -> dict[str, object]:
    indexer = output.get("cdcObjectIndexer")
    if not isinstance(indexer, Mapping):
        return {}
    cursor = indexer.get("cursor")
    return dict(cursor) if isinstance(cursor, Mapping) else {}


def cdc_backlog_payload(backlog: CdcObjectIndexBacklog) -> dict[str, object]:
    return {
        "latestSourceDatasetVersionId": backlog.latest_source_dataset_version_id,
        "latestSourceDatasetVersionNumber": backlog.latest_source_dataset_version_number,
        "nextSourceDatasetVersionId": backlog.next_source_dataset_version_id,
        "nextSourceDatasetVersionNumber": backlog.next_source_dataset_version_number,
        "remainingVersionCount": backlog.remaining_version_count,
        "hasMoreVersions": backlog.has_more_versions,
    }


def cdc_next_action(*, is_indexed: bool, backlog: CdcObjectIndexBacklog) -> str:
    if backlog.has_more_versions:
        return "run_object_index_again"
    if not is_indexed and backlog.latest_source_dataset_version_number is None:
        return "run_stream_archive"
    if not is_indexed:
        return "wait_for_next_cdc_version"
    return "monitor_operations"


def int_cursor(cursor: Mapping[str, object], key: str) -> int | None:
    value = cursor.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _is_after_cursor(version: DatasetVersionRow, last_number: int | None) -> bool:
    return last_number is None or int(version["version_number"]) > last_number


CDC_OBJECT_INDEXER_WORKFLOW_NAME = "ContinuousCdcObjectIndexerWorker"
