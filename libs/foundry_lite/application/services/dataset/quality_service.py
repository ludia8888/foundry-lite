"""Compatibility entrypoint for the Dataset Quality bounded context."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from foundry_lite.application.ports import DatasetRow, TransactionContext
from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckConfig,
    DatasetCheckResult,
    DatasetQualityContractCheck,
    DatasetQualityContractCheckCreateResult,
    DatasetQualityContractCheckList,
    DatasetQualityContractVersion,
    DatasetQualityContractVersionCreateResult,
    DatasetQualityContractVersionList,
    DatasetQualityResultHistory,
    DatasetQualityResultSummary,
    DatasetSchemaJson,
    DatasetSchemaReference,
)
from foundry_lite.application.primitives import StagedFileStats
from foundry_lite.application.services.dataset.quality import DatasetQualityRuntimeService
from foundry_lite.application.services.dataset.quality_api import DatasetQualityApiService
from foundry_lite.application.services.dataset.quality_helpers import DEFAULT_QUALITY_CONTRACT_KEY
from foundry_lite.application.services.dataset.schema_evolution import DatasetSchemaEvolutionResult
from foundry_lite.domain.context import RequestContext


class DatasetQualityService:
    """Stable service surface backed by explicit quality use cases."""

    required_dependencies = ()
    required_collaborators = ()
    _dataset_quality_api_service: DatasetQualityApiService
    _dataset_quality_runtime_service: DatasetQualityRuntimeService

    def __init__(
        self,
        *,
        dataset_quality_api_service: DatasetQualityApiService,
        dataset_quality_runtime_service: DatasetQualityRuntimeService,
    ) -> None:
        self._dataset_quality_api_service = dataset_quality_api_service
        self._dataset_quality_runtime_service = dataset_quality_runtime_service

    def list_contract_checks(
        self,
        dataset: DatasetRow,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheckList:
        return self._dataset_quality_api_service.list_contract_checks(dataset, ctx=ctx)

    def list_quality_results(
        self,
        dataset: DatasetRow,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityResultHistory:
        return self._dataset_quality_api_service.list_quality_results(dataset, limit=limit, ctx=ctx)

    def get_quality_result_summary(
        self,
        dataset: DatasetRow,
        *,
        latest_limit: int = 5,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityResultSummary:
        return self._dataset_quality_api_service.get_quality_result_summary(
            dataset,
            latest_limit=latest_limit,
            ctx=ctx,
        )

    def list_data_contract_versions(
        self,
        dataset: DatasetRow,
        *,
        limit: int = 20,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractVersionList:
        return self._dataset_quality_api_service.list_data_contract_versions(dataset, limit=limit, ctx=ctx)

    def get_data_contract_version(
        self,
        dataset: DatasetRow,
        contract_version_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractVersion:
        return self._dataset_quality_api_service.get_data_contract_version(dataset, contract_version_id, ctx=ctx)

    def create_contract_check(
        self,
        dataset: DatasetRow,
        *,
        config: DatasetCheckConfig,
        severity: str | None = None,
        enabled: bool = True,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheckCreateResult:
        return self._dataset_quality_api_service.create_contract_check(
            dataset,
            config=config,
            severity=severity,
            enabled=enabled,
            ctx=ctx,
        )

    def create_data_contract_version(
        self,
        dataset: DatasetRow,
        *,
        contract_key: str = DEFAULT_QUALITY_CONTRACT_KEY,
        owner_user_id: str | None = None,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractVersionCreateResult:
        return self._dataset_quality_api_service.create_data_contract_version(
            dataset,
            contract_key=contract_key,
            owner_user_id=owner_user_id,
            description=description,
            ctx=ctx,
        )

    def activate_data_contract_version(
        self,
        dataset: DatasetRow,
        contract_version_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractVersion:
        return self._dataset_quality_api_service.activate_data_contract_version(
            dataset,
            contract_version_id,
            ctx=ctx,
        )

    def update_contract_check(
        self,
        dataset: DatasetRow,
        check_id: str,
        *,
        config: DatasetCheckConfig | None = None,
        severity: str | None = None,
        enabled: bool | None = None,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheck:
        return self._dataset_quality_api_service.update_contract_check(
            dataset,
            check_id,
            config=config,
            severity=severity,
            enabled=enabled,
            ctx=ctx,
        )

    def _inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        return self._dataset_quality_runtime_service._inspect_parquet(parquet_path, primary_key)

    def _ensure_schema(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        schema_json: DatasetSchemaJson,
        schema_hash: str,
    ) -> int:
        return self._dataset_quality_runtime_service._ensure_schema(conn, dataset, schema_json, schema_hash)

    def _ensure_schema_reference(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        schema_json: DatasetSchemaJson,
        schema_hash: str,
    ) -> DatasetSchemaReference:
        return self._dataset_quality_runtime_service._ensure_schema_reference(conn, dataset, schema_json, schema_hash)

    def _schema_evolution_result(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        next_schema: DatasetSchemaJson,
        target_schema: DatasetSchemaReference,
    ) -> DatasetSchemaEvolutionResult:
        return self._dataset_quality_runtime_service._schema_evolution_result(
            conn,
            dataset,
            next_schema,
            target_schema,
        )

    def _schema_compatibility_error(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        next_schema: DatasetSchemaJson,
    ) -> DatasetCheckResult | None:
        return self._dataset_quality_runtime_service._schema_compatibility_error(conn, dataset, next_schema)

    def _execute_check(
        self,
        parquet_path: Path,
        row_count: int,
        check: DatasetCheckConfig,
    ) -> DatasetCheckResult:
        return self._dataset_quality_runtime_service._execute_check(parquet_path, row_count, check)

    def _checks_for_dataset(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        extra_checks: Sequence[DatasetCheckConfig],
        contract_checks: Sequence[DatasetCheckConfig] | None = None,
    ) -> list[DatasetCheckConfig]:
        return self._dataset_quality_runtime_service._checks_for_dataset(
            conn,
            ctx,
            dataset,
            extra_checks,
            contract_checks,
        )

    def _run_dataset_checks(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        parquet_path: Path,
        row_count: int,
        run_id: str,
        transaction_id: str,
        *,
        extra_checks: Sequence[DatasetCheckConfig],
        checked_manifest_hash: str,
        validated_against_schema_version_id: str,
        validated_against_schema_version: int,
    ) -> list[DatasetCheckResult]:
        return self._dataset_quality_runtime_service._run_dataset_checks(
            conn,
            ctx,
            dataset,
            parquet_path,
            row_count,
            run_id,
            transaction_id,
            extra_checks=extra_checks,
            checked_manifest_hash=checked_manifest_hash,
            validated_against_schema_version_id=validated_against_schema_version_id,
            validated_against_schema_version=validated_against_schema_version,
        )
