from __future__ import annotations

from foundry_lite.application.ports import (
    DatasetQualityRepository,
    DatasetRow,
    TransactionContext,
)
from foundry_lite.application.ports.dataset_quality_repository import DatasetCheckRow
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.application.services.dataset.quality_helpers import (
    _contract_check_audit_ref,
    _dataset_ref,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound


class DatasetQualityContractMixin:
    dataset_quality_repository: DatasetQualityRepository
    runtime_service: DatasetRuntimeBoundary

    def _required_contract_check(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_id: str,
        check_name: str,
    ) -> DatasetCheckRow:
        persisted = self.dataset_quality_repository.check_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset_id,
            name=check_name,
        )
        if persisted is None:
            raise RuntimeError("dataset quality contract check insert had no persisted winner")
        return persisted

    def _required_contract_check_by_id(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_id: str,
        check_id: str,
    ) -> DatasetCheckRow:
        persisted = self.dataset_quality_repository.check_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset_id,
            check_id=check_id,
        )
        if persisted is None:
            raise NotFound("dataset quality contract check not found", details={"check_id": check_id})
        return persisted

    def _ensure_no_contract_check_name_conflict(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_id: str,
        check_name: str,
        check_id: str,
    ) -> None:
        existing = self.dataset_quality_repository.check_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset_id,
            name=check_name,
        )
        if existing is not None and existing["id"] != check_id:
            raise ConflictDetected(
                "dataset quality contract check already exists",
                details={"check_id": existing["id"]},
            )

    def _audit_contract_check_created(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        check: DatasetCheckRow,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="dataset_quality_contract_check.created",
            resource_type="dataset_quality_contract_check",
            resource_id=check["id"],
            action="create",
            after_ref={
                "datasetId": dataset["id"],
                "datasetRef": _dataset_ref(dataset),
                "checkType": check["check_type"],
                "severity": check["severity"],
                "enabled": check["enabled"],
            },
            correlation_id=str(dataset["id"]),
        )

    def _audit_contract_check_updated(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        before: DatasetCheckRow,
        after: DatasetCheckRow,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="dataset_quality_contract_check.updated",
            resource_type="dataset_quality_contract_check",
            resource_id=after["id"],
            action="update",
            before_ref=_contract_check_audit_ref(dataset, before),
            after_ref=_contract_check_audit_ref(dataset, after),
            correlation_id=str(dataset["id"]),
        )
