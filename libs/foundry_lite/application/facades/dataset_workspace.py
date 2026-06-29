from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetInspectionPayload,
    DatasetQualityContractCheck,
    DatasetQualityContractCheckCreateResult,
    DatasetQualityContractCheckList,
    DatasetQualityResultHistory,
    DatasetQualityResultSummary,
    DatasetRow,
    DatasetVersionRow,
    RestSourceConfig,
    StreamArchiveConfig,
    TabularRow,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.dataset_service import DatasetServices
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class DatasetWorkspace:
    """Dataset bounded context: catalog, versions, ingestion, and recovery."""

    def __init__(self, datasets: DatasetServices) -> None:
        self._datasets = datasets

    def create(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
        description: str | None = None,
        owner_team: str | None = None,
        classification: str | None = None,
        partition_spec: list[str] | None = None,
        sort_order: list[str] | None = None,
        target_file_size_bytes: int | None = None,
    ) -> DatasetRow:
        return self._datasets.registry.create_dataset(
            dataset_ref,
            ctx=ctx,
            primary_key=primary_key,
            storage_kind=storage_kind,
            description=description,
            owner_team=owner_team,
            classification=classification,
            partition_spec=partition_spec,
            sort_order=sort_order,
            target_file_size_bytes=target_file_size_bytes,
        )

    def ensure(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
        partition_spec: list[str] | None = None,
        sort_order: list[str] | None = None,
        target_file_size_bytes: int | None = None,
    ) -> DatasetRow:
        return self._datasets.registry.ensure_dataset(
            dataset_ref,
            ctx=ctx,
            primary_key=primary_key,
            storage_kind=storage_kind,
            partition_spec=partition_spec,
            sort_order=sort_order,
            target_file_size_bytes=target_file_size_bytes,
        )

    def find(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow | None:
        return self._datasets.registry.find_dataset(dataset_ref, ctx=ctx)

    def get(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow:
        return self._datasets.registry.get_dataset(dataset_ref, ctx=ctx)

    def list_datasets(self, *, ctx: RequestContext | None = None) -> list[DatasetRow]:
        return self._datasets.registry.list_datasets(ctx=ctx)

    def list_versions(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> list[DatasetVersionRow]:
        return self._datasets.registry.list_dataset_versions(dataset_ref, ctx=ctx)

    def abort_stale_open_transactions(self, created_before: str, *, ctx: RequestContext | None = None) -> list[str]:
        return self._datasets.recovery.abort_stale_open_transactions(created_before, ctx=ctx)

    def preview(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 100,
        version: str = "latest",
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[TabularRow]:
        return self._datasets.registry.preview_dataset(
            dataset_ref,
            ctx=ctx,
            limit=limit,
            version=version,
            partition_filter=partition_filter,
        )

    def inspect(
        self, dataset_ref: str, *, ctx: RequestContext | None = None, version: str = "latest"
    ) -> DatasetInspectionPayload:
        return self._datasets.registry.inspect_dataset(dataset_ref, ctx=ctx, version=version)

    def list_quality_contract_checks(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheckList:
        dataset = self._datasets.registry.get_dataset(dataset_ref, ctx=ctx)
        return self._datasets.quality.list_contract_checks(dataset, ctx=ctx)

    def list_quality_results(
        self,
        dataset_ref: str,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityResultHistory:
        dataset = self._datasets.registry.get_dataset(dataset_ref, ctx=ctx)
        return self._datasets.quality.list_quality_results(dataset, limit=limit, ctx=ctx)

    def get_quality_result_summary(
        self,
        dataset_ref: str,
        *,
        latest_limit: int = 5,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityResultSummary:
        dataset = self._datasets.registry.get_dataset(dataset_ref, ctx=ctx)
        return self._datasets.quality.get_quality_result_summary(dataset, latest_limit=latest_limit, ctx=ctx)

    def create_quality_contract_check(
        self,
        dataset_ref: str,
        *,
        config: Mapping[str, object],
        severity: str | None = None,
        enabled: bool = True,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheckCreateResult:
        dataset = self._datasets.registry.get_dataset(dataset_ref, ctx=ctx)
        return self._datasets.quality.create_contract_check(
            dataset,
            config=config,
            severity=severity,
            enabled=enabled,
            ctx=ctx,
        )

    def update_quality_contract_check(
        self,
        dataset_ref: str,
        check_id: str,
        *,
        config: Mapping[str, object] | None = None,
        severity: str | None = None,
        enabled: bool | None = None,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheck:
        dataset = self._datasets.registry.get_dataset(dataset_ref, ctx=ctx)
        return self._datasets.quality.update_contract_check(
            dataset,
            check_id,
            config=config,
            severity=severity,
            enabled=enabled,
            ctx=ctx,
        )

    def upload_csv(
        self,
        dataset_ref: str,
        csv_path: str | Path,
        *,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
    ) -> CommitResult:
        return self._datasets.ingest.upload_csv(dataset_ref, csv_path, ctx=ctx, sync_name=sync_name)

    def sync_connector_snapshot(
        self,
        dataset_ref: str,
        *,
        connector_name: str,
        resource_name: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        cursor: Mapping[str, object] | None = None,
        rest: RestSourceConfig | None = None,
    ) -> CommitResult:
        return self._datasets.ingest.sync_connector_snapshot(
            dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            ctx=ctx,
            sync_name=sync_name,
            cursor=cursor,
            rest=rest,
        )

    def ingest_webhook_event(
        self,
        dataset_ref: str,
        *,
        connector_name: str,
        resource_name: str,
        payload: Mapping[str, object],
        raw_body: bytes,
        signature: str,
        signature_timestamp: str,
        secret: str,
        event_id: str | None = None,
        require_service_principal_signature: bool = False,
        ctx: RequestContext | None = None,
    ) -> CommitResult:
        return self._datasets.ingest.ingest_webhook_event(
            dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            payload=payload,
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            secret=secret,
            event_id=event_id,
            require_service_principal_signature=require_service_principal_signature,
            ctx=ctx,
        )

    def archive_stream_events(
        self,
        dataset_ref: str,
        *,
        stream: StreamArchiveConfig,
        ctx: RequestContext | None = None,
        after_offset: int | None = None,
        sync_name: str | None = None,
    ) -> CommitResult | None:
        return self._datasets.ingest.archive_stream_events(
            dataset_ref,
            stream=stream,
            ctx=ctx,
            after_offset=after_offset,
            sync_name=sync_name,
        )
