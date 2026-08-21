"""Shadow object-index validation and promotion service."""

from __future__ import annotations

from typing import cast

from foundry_lite.application.ports import (
    ObjectIndexRepository,
    ObjectIndexValidationResult,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.indexing_protocols import IndexRuntimeBoundary
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexRebuildCounts,
    ObjectIndexRebuildPlan,
    ObjectIndexStats,
    object_index_stats,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.domain.object_store.indexing import ShadowIndexValidation, shadow_validation_failed


class ObjectIndexShadowService(CoreService):
    """Validates and promotes shadow object indexes."""

    required_dependencies = ("object_index_repository",)
    required_collaborators = ("runtime_service",)
    object_index_repository: ObjectIndexRepository
    runtime_service: IndexRuntimeBoundary

    def validate_shadow_index(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        expected_count: int | None,
        expected_hash: str | None,
    ) -> ObjectIndexValidationResult:
        baseline = self._expected_shadow_stats(conn, ctx, plan, expected_count, expected_hash)
        actual = object_index_stats(
            self.object_index_repository.object_records_for_index_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_id=plan.object_type["id"],
                index_version=plan.index_version,
            )
        )
        validation = ShadowIndexValidation(
            baseline.object_count,
            actual.object_count,
            baseline.object_hash,
            actual.object_hash,
        )
        if shadow_validation_failed(validation):
            raise ValidationFailed("shadow index validation failed", details=validation.to_result())
        return cast(ObjectIndexValidationResult, validation.to_result())

    def switch_shadow_index(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        counts: ObjectIndexRebuildCounts,
        validation: ObjectIndexValidationResult,
    ) -> None:
        switched = self.object_index_repository.switch_active_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=plan.object_type["id"],
            object_type_api_name=plan.object_type["api_name"],
            index_version=plan.index_version,
            updated_at=_now(),
            expected_previous_index_version=plan.previous_index_version,
        )
        if not switched:
            raise ValidationFailed(
                "shadow index active version changed during switch",
                details=_shadow_switch_error(plan),
            )
        self._mark_shadow_run_succeeded(conn, ctx, plan, counts)
        self._audit_shadow_index_switch(conn, ctx, plan, validation)

    def _mark_shadow_run_succeeded(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        counts: ObjectIndexRebuildCounts,
    ) -> None:
        updated = self.object_index_repository.mark_index_run_succeeded(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_id=plan.run_id,
            rows_read=counts.rows_read,
            objects_upserted=counts.objects_upserted,
            objects_deleted=counts.objects_deleted,
            links_upserted=counts.links_upserted,
            cursor={"last_row": counts.rows_read},
            completed_at=_now(),
        )
        if not updated:
            raise ConflictDetected("index run terminal state changed concurrently", details={"run_id": plan.run_id})

    def _expected_shadow_stats(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        expected_count: int | None,
        expected_hash: str | None,
    ) -> ObjectIndexStats:
        records = self.object_index_repository.object_records_for_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=plan.object_type["id"],
            index_version=plan.previous_index_version,
        )
        baseline = object_index_stats(records)
        return ObjectIndexStats(
            object_count=expected_count if expected_count is not None else baseline.object_count,
            object_hash=expected_hash if expected_hash is not None else baseline.object_hash,
        )

    def _audit_shadow_index_switch(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        validation: ObjectIndexValidationResult,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="object.index.shadow_switched",
            resource_type="object_type",
            resource_id=plan.object_type["id"],
            action="index_shadow_rebuild",
            before_ref={"indexVersion": plan.previous_index_version},
            after_ref={"indexVersion": plan.index_version, "validation": validation},
        )


def _shadow_switch_error(plan: ObjectIndexRebuildPlan) -> dict[str, object]:
    return {
        "objectType": plan.object_type["api_name"],
        "expectedPreviousIndexVersion": plan.previous_index_version,
        "attemptedIndexVersion": plan.index_version,
    }
