"""Ontology rollback use-case service (archived version -> new active version)."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    FunctionTypeRow,
    OntologyRollbackResult,
    OntologyVersionRow,
    TransactionContext,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_activation_service import OntologyActivationService
from foundry_lite.application.services.ontology_definition_snapshot import (
    JsonObject,
    ontology_definition_snapshot,
)
from foundry_lite.application.services.ontology_protocols import (
    OntologyRuntimeBoundary,
    require_ontology_write_open,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class OntologyRollbackService(CoreService):
    """Restore an archived ontology version as a brand-new active version.

    History stays append-only: rollback never flips an archived row back to
    active. It replays the archived definition through the one activation path
    so migration blocking, reindex contracts, audit, and outbox behave exactly
    like a regular apply.
    """

    required_dependencies = ("engine", "ontology_repository")
    required_collaborators = ("ontology_activation_service", "runtime_service")
    ontology_activation_service: OntologyActivationService
    runtime_service: OntologyRuntimeBoundary

    def rollback_to_version(
        self,
        version_number: int,
        *,
        expected_active_version_number: int | None = None,
        idempotency_key: str | None = None,
        ctx: RequestContext | None = None,
    ) -> OntologyRollbackResult:
        ctx = ctx or RequestContext()
        # Rollback activates a new version, so it demands the same permission as apply.
        self.runtime_service._require_or_audit(ctx, "ontology:activate", "ontology", "rollback")
        require_ontology_write_open(self.runtime_service, ctx, "rollback_ontology", "ontology", "rollback")
        receipt_key = _rollback_receipt_key(idempotency_key)
        with self.engine.begin() as conn:
            self.ontology_repository.lock_ontology_for_activation(transaction=conn, tenant_id=ctx.tenant_id)
            replay = self._rollback_replay(conn, ctx, version_number, expected_active_version_number, receipt_key)
            if replay is not None:
                return replay
            target = self._archived_target_version(conn, ctx, version_number)
            source = self.ontology_repository.active_ontology_version(transaction=conn, tenant_id=ctx.tenant_id)
            _require_expected_active_version(source, expected_active_version_number)
            definition = self._definition_for_version(conn, ctx, target["id"])
            applied = self.ontology_activation_service._apply_loaded_ontology(conn, ctx, definition)
            result: OntologyRollbackResult = {
                "ontology_version_id": applied["ontology_version_id"],
                "version_number": applied["version_number"],
                "rolled_back_from_version_number": source["version_number"] if source is not None else None,
                "rolled_back_to_version_number": target["version_number"],
                "migration_plan": applied["migration_plan"],
            }
            self._record_rollback(conn, ctx, source, target, result, receipt_key)
            return result

    def replay_rollback(
        self,
        version_number: int,
        *,
        expected_active_version_number: int,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OntologyRollbackResult | None:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:activate", "ontology", "rollback")
        with self.engine.begin() as conn:
            return self._rollback_replay(
                conn,
                ctx,
                version_number,
                expected_active_version_number,
                _rollback_receipt_key(idempotency_key),
            )

    def _archived_target_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        version_number: int,
    ) -> OntologyVersionRow:
        target = self.ontology_repository.ontology_version_by_number(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            version_number=version_number,
        )
        if target is None:
            raise NotFound("ontology version not found", details={"versionNumber": version_number})
        if target["status"] != "archived":
            raise ValidationFailed(
                "only archived ontology versions can be rolled back to",
                details={"versionNumber": version_number, "status": target["status"]},
            )
        return target

    def _definition_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> JsonObject:
        object_rows = self.ontology_repository.object_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )
        action_rows = [
            action
            for row in object_rows
            for action in self.ontology_repository.actions_for_target(transaction=conn, object_type_id=row["id"])
        ]
        return ontology_definition_snapshot(
            object_rows=object_rows,
            properties_by_object_id={
                row["id"]: self.ontology_repository.properties_for_object_type(
                    transaction=conn, object_type_id=row["id"]
                )
                for row in object_rows
            },
            link_rows=self.ontology_repository.link_types_for_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ontology_version_id=ontology_version_id,
            ),
            action_rows=action_rows,
            interface_rows=self.ontology_repository.interface_types_for_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ontology_version_id=ontology_version_id,
            ),
            function_rows=self._function_rows_for_version(conn, ctx, ontology_version_id),
        )

    def _function_rows_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> list[FunctionTypeRow]:
        return self.ontology_repository.function_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _record_rollback(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        source: OntologyVersionRow | None,
        target: OntologyVersionRow,
        result: OntologyRollbackResult,
        receipt_key: str | None,
    ) -> None:
        self._record_rollback_audit(conn, ctx, source, target, result, receipt_key)
        if receipt_key is not None:
            self._record_rollback_receipt(conn, ctx, source, target, result, receipt_key)

    def _record_rollback_audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        source: OntologyVersionRow | None,
        target: OntologyVersionRow,
        result: OntologyRollbackResult,
        receipt_key: str | None,
    ) -> None:
        before_ref: dict[str, object] = {
            "sourceOntologyVersionId": source["id"] if source is not None else None,
            "sourceVersionNumber": source["version_number"] if source is not None else None,
        }
        after_ref: dict[str, object] = {
            "targetOntologyVersionId": target["id"],
            "targetVersionNumber": target["version_number"],
            "newOntologyVersionId": result["ontology_version_id"],
            "newVersionNumber": result["version_number"],
        }
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="ontology.rollback",
            resource_type="ontology_version",
            resource_id=result["ontology_version_id"],
            action="rollback",
            before_ref=before_ref,
            after_ref=after_ref,
            correlation_id=receipt_key,
        )

    def _record_rollback_receipt(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        source: OntologyVersionRow | None,
        target: OntologyVersionRow,
        result: OntologyRollbackResult,
        receipt_key: str,
    ) -> None:
        self.runtime_service._outbox(
            conn,
            ctx,
            "ontology.rollback.completed",
            "ontology_version",
            result["ontology_version_id"],
            {
                "targetVersionNumber": target["version_number"],
                "expectedActiveVersionNumber": source["version_number"] if source is not None else None,
                "result": dict(result),
            },
            idempotency_key=receipt_key,
            correlation_id=receipt_key,
        )

    def _rollback_replay(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        target_version_number: int,
        expected_active_version_number: int | None,
        receipt_key: str | None,
    ) -> OntologyRollbackResult | None:
        if receipt_key is None:
            return None
        row = self.runtime_service._outbox_event_by_idempotency_key(conn, ctx, receipt_key)
        payload = row.get("payload") if isinstance(row, Mapping) else None
        if not isinstance(payload, Mapping):
            return None
        _require_replay_request(payload, target_version_number, expected_active_version_number)
        return _rollback_result(payload.get("result"))


def _require_expected_active_version(source: OntologyVersionRow | None, expected: int | None) -> None:
    if expected is None:
        return
    actual = source["version_number"] if source is not None else None
    if actual != expected:
        raise ValidationFailed(
            "active ontology changed after the rollback target was reviewed",
            details={"expectedActiveVersionNumber": expected, "activeVersionNumber": actual},
        )


def _rollback_receipt_key(idempotency_key: str | None) -> str | None:
    if idempotency_key is None:
        return None
    if not idempotency_key.strip():
        raise ValidationFailed("idempotencyKey is required for ontology rollback")
    return f"ontology.rollback:{idempotency_key.strip()}"


def _require_replay_request(payload: Mapping[str, object], target: int, expected: int | None) -> None:
    if payload.get("targetVersionNumber") != target or payload.get("expectedActiveVersionNumber") != expected:
        raise ValidationFailed("ontology rollback idempotency key was reused with different arguments")


def _rollback_result(value: object) -> OntologyRollbackResult:
    if not isinstance(value, Mapping):
        raise ValidationFailed("ontology rollback receipt is missing its result")
    migration_plan = value.get("migration_plan")
    if not isinstance(migration_plan, Mapping):
        raise ValidationFailed("ontology rollback receipt has an invalid migration plan")
    return {
        "ontology_version_id": str(value["ontology_version_id"]),
        "version_number": int(value["version_number"]),
        "rolled_back_from_version_number": _optional_int(value.get("rolled_back_from_version_number")),
        "rolled_back_to_version_number": int(value["rolled_back_to_version_number"]),
        "migration_plan": dict(migration_plan),
    }


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
