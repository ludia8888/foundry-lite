"""Application service helpers for quality api workflows."""

from __future__ import annotations

from foundry_lite.application.ports import DatasetQualityRepository, DatasetRow, TransactionContext, TransactionManager
from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckConfig,
    DatasetQualityContractCheck,
    DatasetQualityContractCheckCreateResult,
    DatasetQualityContractCheckList,
    DatasetQualityContractVersion,
    DatasetQualityContractVersionCreateResult,
    DatasetQualityContractVersionList,
    DatasetQualityContractVersionRecord,
    DatasetQualityResultHistory,
    DatasetQualityResultSummary,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.dataset.quality_contracts import DatasetQualityContractMixin
from foundry_lite.application.services.dataset.quality_helpers import (
    DEFAULT_QUALITY_CONTRACT_KEY,
    _check_name,
    _contract_check,
    _contract_check_config,
    _contract_check_record,
    _contract_version,
    _dataset_ref,
    _quality_result_history_item,
    _quality_result_summary,
    _updated_contract_check_record,
    _validate_contract_key,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.security.policy import PolicyService


class DatasetQualityApiMixin(DatasetQualityContractMixin):
    dataset_quality_repository: DatasetQualityRepository
    engine: TransactionManager
    policy: PolicyService

    def list_contract_checks(
        self,
        dataset: DatasetRow,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheckList:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        with self.engine.begin() as conn:
            rows = self.dataset_quality_repository.checks_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=str(dataset["id"]),
            )
        return {"datasetRef": _dataset_ref(dataset), "checks": [_contract_check(row) for row in rows]}

    def list_quality_results(
        self,
        dataset: DatasetRow,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityResultHistory:
        ctx = ctx or RequestContext()
        if limit < 1 or limit > 100:
            raise ValidationFailed("quality result history limit must be between 1 and 100")
        self.policy.require(ctx, "dataset:read")
        with self.engine.begin() as conn:
            rows = self.dataset_quality_repository.check_results_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=str(dataset["id"]),
                limit=limit,
            )
        return {
            "datasetRef": _dataset_ref(dataset),
            "results": [_quality_result_history_item(row) for row in rows],
        }

    def get_quality_result_summary(
        self,
        dataset: DatasetRow,
        *,
        latest_limit: int = 5,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityResultSummary:
        ctx = ctx or RequestContext()
        if latest_limit < 1 or latest_limit > 20:
            raise ValidationFailed("quality result summary latest limit must be between 1 and 20")
        self.policy.require(ctx, "dataset:read")
        dataset_id = str(dataset["id"])
        with self.engine.begin() as conn:
            status_rows = self.dataset_quality_repository.check_result_status_counts_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
            )
            type_rows = self.dataset_quality_repository.check_result_type_status_counts_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
            )
            latest_rows = self.dataset_quality_repository.check_results_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
                limit=latest_limit,
            )
        return _quality_result_summary(dataset, status_rows, type_rows, latest_rows)

    def list_data_contract_versions(
        self,
        dataset: DatasetRow,
        *,
        limit: int = 20,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractVersionList:
        ctx = ctx or RequestContext()
        if limit < 1 or limit > 100:
            raise ValidationFailed("data contract version limit must be between 1 and 100")
        self.policy.require(ctx, "dataset:read")
        with self.engine.begin() as conn:
            rows = self.dataset_quality_repository.contract_versions_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=str(dataset["id"]),
                limit=limit,
            )
        return {
            "datasetRef": _dataset_ref(dataset),
            "contractVersions": [_contract_version(row, dataset) for row in rows],
        }

    def get_data_contract_version(
        self,
        dataset: DatasetRow,
        contract_version_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractVersion:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        with self.engine.begin() as conn:
            row = self._required_contract_version_by_id(conn, ctx, str(dataset["id"]), contract_version_id)
        return _contract_version(row, dataset)

    def create_contract_check(
        self,
        dataset: DatasetRow,
        *,
        config: DatasetCheckConfig,
        severity: str | None = None,
        enabled: bool = True,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheckCreateResult:
        ctx = ctx or RequestContext()
        dataset_id = str(dataset["id"])
        if config is None and severity is None and enabled is None:
            raise ValidationFailed("quality contract check update requires a change")
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="create_quality_contract_check",
            resource_type="dataset",
            resource_id=dataset_id,
        )
        contract_config = _contract_check_config(config, severity)
        check_name = _check_name(contract_config)
        with self.engine.begin() as conn:
            existing = self.dataset_quality_repository.check_by_name(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
                name=check_name,
            )
            if existing is not None:
                return {"check": _contract_check(existing), "isIdempotentReplay": True}
            record = _contract_check_record(ctx, dataset_id, check_name, contract_config, enabled)
            self.dataset_quality_repository.insert_check(transaction=conn, record=record)
            persisted = self._required_contract_check(conn, ctx, dataset_id, check_name)
            self._audit_contract_check_created(conn, ctx, dataset, persisted)
            return {"check": _contract_check(persisted), "isIdempotentReplay": False}

    def create_data_contract_version(
        self,
        dataset: DatasetRow,
        *,
        contract_key: str = DEFAULT_QUALITY_CONTRACT_KEY,
        owner_user_id: str | None = None,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractVersionCreateResult:
        ctx = ctx or RequestContext()
        dataset_id = str(dataset["id"])
        key = _validate_contract_key(contract_key)
        if key != DEFAULT_QUALITY_CONTRACT_KEY:
            raise ValidationFailed("only the default data quality contract can be versioned in this slice")
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="create_data_contract_version",
            resource_type="dataset",
            resource_id=dataset_id,
        )
        with self.engine.begin() as conn:
            record = self._data_contract_version_record(conn, ctx, dataset_id, key, owner_user_id, description)
            self.dataset_quality_repository.insert_contract_version(transaction=conn, record=record)
            row = self._required_contract_version_by_id(conn, ctx, dataset_id, record.contract_version_id)
            self._audit_contract_version_created(conn, ctx, dataset, row)
        return {"contractVersion": _contract_version(row, dataset), "isIdempotentReplay": False}

    def activate_data_contract_version(
        self,
        dataset: DatasetRow,
        contract_version_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractVersion:
        ctx = ctx or RequestContext()
        dataset_id = str(dataset["id"])
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="activate_data_contract_version",
            resource_type="dataset",
            resource_id=dataset_id,
        )
        with self.engine.begin() as conn:
            before = self._required_contract_version_by_id(conn, ctx, dataset_id, contract_version_id)
            updated = self.dataset_quality_repository.activate_contract_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
                contract_key=before["contract_key"],
                contract_version_id=contract_version_id,
                activated_at=_now(),
            )
            if not updated:
                raise NotFound(
                    "dataset quality contract version not found",
                    details={"contract_version_id": contract_version_id},
                )
            after = self._required_contract_version_by_id(conn, ctx, dataset_id, contract_version_id)
            self._audit_contract_version_activated(conn, ctx, dataset, after)
        return _contract_version(after, dataset)

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
        ctx = ctx or RequestContext()
        dataset_id = str(dataset["id"])
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="update_quality_contract_check",
            resource_type="dataset",
            resource_id=dataset_id,
        )
        with self.engine.begin() as conn:
            before = self._required_contract_check_by_id(conn, ctx, dataset_id, check_id)
            record = _updated_contract_check_record(before, config=config, severity=severity, enabled=enabled)
            self._ensure_no_contract_check_name_conflict(conn, ctx, dataset_id, record.name, check_id)
            if not self.dataset_quality_repository.update_check(transaction=conn, record=record):
                raise NotFound("dataset quality contract check not found", details={"check_id": check_id})
            after = self._required_contract_check_by_id(conn, ctx, dataset_id, check_id)
            self._audit_contract_check_updated(conn, ctx, dataset, before, after)
            return _contract_check(after)

    def _data_contract_version_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_id: str,
        contract_key: str,
        owner_user_id: str | None,
        description: str | None,
    ) -> DatasetQualityContractVersionRecord:
        latest_version = self.dataset_quality_repository.latest_contract_version_number(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset_id,
            contract_key=contract_key,
        )
        latest_schema = self.dataset_quality_repository.latest_schema(transaction=conn, dataset_id=dataset_id)
        return DatasetQualityContractVersionRecord(
            contract_version_id=_new_id("dqcv"),
            tenant_id=ctx.tenant_id,
            dataset_id=dataset_id,
            contract_key=contract_key,
            version=(latest_version or 0) + 1,
            status="DRAFT",
            owner_user_id=owner_user_id,
            description=description,
            checks_snapshot=self._snapshot_contract_checks(conn, ctx, dataset_id),
            schema_version_id=latest_schema["id"] if latest_schema else None,
            schema_version=int(latest_schema["version"]) if latest_schema else None,
            created_at=_now(),
        )
