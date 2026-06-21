from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.compute_adapter import SqlTransformPlan
from foundry_lite.application.ports.transform_repository import (
    TransformCheck,
    TransformRecord,
    TransformRetryResult,
    TransformRow,
)
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.transform_protocols import (
    TransformDatasetRegistry,
    TransformDatasetTransactions,
    TransformDatasetVersions,
    TransformRuntimeBoundary,
)
from foundry_lite.application.services.transform_protocols import (
    require_transform_write_open as require_write_open,
)
from foundry_lite.application.services.transform_runs import (
    TransformRunPlan,
    start_failed_transform_retry,
    start_transform_run,
)
from foundry_lite.application.services.transform_sql_guards import validate_sql_transform_uses_declared_inputs
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    DatasetCommitBlocked,
    InvariantViolation,
    NotFound,
    ValidationFailed,
)


class TransformService(CoreService):
    required_dependencies = ("engine", "compute_adapter", "transform_repository")
    required_collaborators = (
        "dataset_registry_service",
        "dataset_transaction_service",
        "dataset_version_service",
        "runtime_service",
    )
    dataset_registry_service: TransformDatasetRegistry
    dataset_transaction_service: TransformDatasetTransactions
    dataset_version_service: TransformDatasetVersions
    runtime_service: TransformRuntimeBoundary

    def register_transform(
        self,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
        language: str = "sql",
    ) -> TransformRow:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "transform:run", "transform", api_name)
        require_write_open(self.runtime_service, ctx, "register_transform", "transform", api_name)
        return self._register_transform_definition(
            ctx,
            api_name,
            entrypoint=entrypoint,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            mode=mode,
            language=language,
        )

    def _register_transform_definition(
        self,
        ctx: RequestContext,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None,
        mode: str,
        language: str,
    ) -> TransformRow:
        language = _supported_transform_language(language)
        normalized_checks = self._normalized_checks(checks)
        with self.engine.begin() as conn:
            existing = self._transform_by_api_name(conn, ctx, api_name)
            if existing:
                return self._replace_transform_definition(
                    conn,
                    ctx,
                    existing,
                    api_name,
                    entrypoint,
                    inputs,
                    output_dataset_ref,
                    normalized_checks,
                    mode,
                    language,
                )
            return self._create_transform_definition(
                conn,
                ctx,
                api_name,
                entrypoint=entrypoint,
                inputs=inputs,
                output_dataset_ref=output_dataset_ref,
                checks=normalized_checks,
                mode=mode,
                language=language,
            )

    def _transform_by_api_name(
        self, conn: TransactionContext, ctx: RequestContext, api_name: str
    ) -> TransformRow | None:
        return self.transform_repository.transform_by_api_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            api_name=api_name,
        )

    def run_transform(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "transform:run", "transform", api_name)
        require_write_open(self.runtime_service, ctx, "run_transform", "transform", api_name)
        with self.engine.begin() as conn:
            plan = start_transform_run(
                conn=conn,
                ctx=ctx,
                api_name=api_name,
                dataset_registry_service=self.dataset_registry_service,
                dataset_transaction_service=self.dataset_transaction_service,
                dataset_version_service=self.dataset_version_service,
                transform_repository=self.transform_repository,
            )
        staged = self.dataset_transaction_service._staging_file(
            plan.output_dataset, plan.transaction_id, "part-00000.parquet"
        )
        try:
            self._execute_sql_transform(plan.sql_template, plan.input_versions, staged)
            blocked: DatasetCommitBlocked | None = None
            with self.engine.begin() as conn:
                try:
                    return self._finalize_transform_run(conn, ctx, plan, staged)
                except DatasetCommitBlocked as exc:
                    blocked = exc
            if blocked is not None:
                raise blocked
            raise InvariantViolation("transform finalization did not return a commit result")
        except Exception as exc:
            self._abort_transform_run(ctx, plan, exc)
            if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
                raise
            raise ValidationFailed("transform failed", details={"error": str(exc)}) from exc

    def retry_transform_run(
        self,
        transform_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> TransformRetryResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "transform_run", transform_run_id)
        require_write_open(self.runtime_service, ctx, "retry_transform_run", "transform_run", transform_run_id)
        with self.engine.begin() as conn:
            plan = start_failed_transform_retry(
                conn=conn,
                ctx=ctx,
                transform_run_id=transform_run_id,
                dataset_registry_service=self.dataset_registry_service,
                dataset_transaction_service=self.dataset_transaction_service,
                transform_repository=self.transform_repository,
            )
        staged = self.dataset_transaction_service._staging_file(
            plan.output_dataset, plan.transaction_id, "part-00000.parquet"
        )
        try:
            self._execute_sql_transform(plan.sql_template, plan.input_versions, staged)
            blocked: DatasetCommitBlocked | None = None
            with self.engine.begin() as conn:
                try:
                    result = self._finalize_transform_run(conn, ctx, plan, staged, should_audit_retry=True)
                except DatasetCommitBlocked as exc:
                    blocked = exc
                    result = None
            if blocked is not None:
                raise blocked
            if result is None:
                raise InvariantViolation("transform retry finalization did not return a commit result")
            return _transform_retry_response(plan, result)
        except Exception as exc:
            self._abort_transform_run(ctx, plan, exc)
            if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
                raise
            raise ValidationFailed("transform failed", details={"error": str(exc)}) from exc

    def _finalize_transform_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: TransformRunPlan,
        staged: Path,
        *,
        should_audit_retry: bool = False,
    ) -> CommitResult:
        def after_persist(conn: TransactionContext, result: CommitResult) -> None:
            self._record_transform_lineage(conn, ctx, plan, result)
            self._mark_transform_run_succeeded(conn, ctx, plan.run_id, result)
            if should_audit_retry:
                self._audit_transform_retry(conn, ctx, plan, result)

        return self.dataset_transaction_service._finalize_open_transaction(
            conn,
            ctx,
            dataset=plan.output_dataset,
            transaction_id=plan.transaction_id,
            staged_parquet=staged,
            run_id=plan.run_id,
            audit_action="transform_output_commit",
            outbox_event_type="dataset.version.committed",
            extra_checks=plan.checks,
            after_persist=after_persist,
        )

    def _record_transform_lineage(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: TransformRunPlan,
        result: CommitResult,
    ) -> None:
        for version_id in plan.input_versions.values():
            self.runtime_service._lineage(
                conn,
                ctx,
                "dataset_version",
                version_id,
                "dataset_version",
                result.version_id,
                "input_to",
                plan.run_id,
            )

    def _abort_transform_run(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        exc: Exception,
    ) -> None:
        """Abort the output transaction and mark the transform run failed."""
        self.dataset_transaction_service._abort_transaction_after_error(
            ctx,
            plan.transaction_id,
            plan.run_id,
            exc,
            "transform",
            adapter="compute_adapter.execute_transform",
        )

    def _create_transform_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: list[dict[str, object]],
        mode: str,
        language: str,
    ) -> TransformRow:
        """Insert a new transform definition and emit its audit record."""
        transform_id = _new_id("tf")
        self._insert_transform_definition(
            conn,
            ctx,
            transform_id,
            api_name,
            entrypoint=entrypoint,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            mode=mode,
            language=language,
        )
        self._audit_transform_definition_created(conn, ctx, transform_id, api_name, output_dataset_ref)
        row = self.transform_repository.transform_by_id(transaction=conn, transform_id=transform_id)
        if row is None:
            raise InvariantViolation("transform row missing after insert", details={"transform_id": transform_id})
        return row

    def _insert_transform_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        transform_id: str,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: list[dict[str, object]],
        mode: str,
        language: str,
    ) -> None:
        self.transform_repository.insert_transform(
            transaction=conn,
            record=TransformRecord(
                transform_id=transform_id,
                tenant_id=ctx.tenant_id,
                api_name=api_name,
                language=language,
                entrypoint=str(entrypoint),
                mode=mode,
                inputs=dict(inputs),
                output_dataset_ref=output_dataset_ref,
                checks=checks,
            ),
        )

    def _audit_transform_definition_created(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        transform_id: str,
        api_name: str,
        output_dataset_ref: str,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="transform.definition.created",
            resource_type="transform",
            resource_id=transform_id,
            action="register",
            after_ref={"api_name": api_name, "output_dataset_ref": output_dataset_ref},
        )

    def _update_transform_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        existing: TransformRow,
        *,
        language: str,
        entrypoint: str | Path,
        mode: str,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck],
    ) -> None:
        """Persist a replacement transform definition under the tenant boundary."""
        self.transform_repository.update_transform_definition(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            transform_id=existing["id"],
            language=language,
            entrypoint=str(entrypoint),
            mode=mode,
            inputs=dict(inputs),
            output_dataset_ref=output_dataset_ref,
            checks=checks,
        )

    def _replace_transform_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        existing: TransformRow,
        api_name: str,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck],
        mode: str,
        language: str,
    ) -> TransformRow:
        """Update an existing transform definition and emit its audit record."""
        self._update_transform_definition(
            conn,
            ctx,
            existing,
            language=language,
            entrypoint=entrypoint,
            mode=mode,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="transform.definition.updated",
            resource_type="transform",
            resource_id=existing["id"],
            action="register",
            after_ref={"api_name": api_name, "output_dataset_ref": output_dataset_ref},
        )
        row = self.transform_repository.transform_by_id(transaction=conn, transform_id=existing["id"])
        if row is None:
            raise InvariantViolation("transform row missing after update", details={"transform_id": existing["id"]})
        return row

    def _normalized_checks(self, checks: Sequence[TransformCheck] | None) -> list[dict[str, object]]:
        return [dict(check) for check in checks or ()]

    def _mark_transform_run_succeeded(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        result: CommitResult,
    ) -> None:
        """Close a transform run with the output dataset version."""
        self.transform_repository.update_transform_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            transform_run_id=run_id,
            status="SUCCESS",
            output_version_id=result.version_id,
            error=None,
            completed_at=_now(),
        )

    def _audit_transform_retry(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: TransformRunPlan,
        result: CommitResult,
    ) -> None:
        if plan.retry_of_run_id is None:
            return
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="transform.run.retry_requested",
            resource_type="transform_run",
            resource_id=plan.run_id,
            action="operations:retry",
            before_ref={"transform_run_id": plan.retry_of_run_id},
            after_ref={"transform_run_id": plan.run_id, "output_version_id": result.version_id},
            correlation_id=plan.run_id,
        )

    def _execute_sql_transform(
        self,
        sql_template: str,
        input_versions: Mapping[str, str],
        staged: Path,
    ) -> None:
        validate_sql_transform_uses_declared_inputs(sql_template)
        input_paths_by_ref = {
            dataset_ref: self.dataset_transaction_service._version_file_path(
                self.dataset_version_service._get_version_by_id(version_id)
            )
            for dataset_ref, version_id in input_versions.items()
        }
        self.compute_adapter.execute_transform(
            SqlTransformPlan(
                sql_template=sql_template,
                input_paths_by_ref=input_paths_by_ref,
                target_path=staged,
            )
        )


def _transform_retry_response(plan: TransformRunPlan, result: CommitResult) -> TransformRetryResult:
    return {
        "original_run_id": plan.retry_of_run_id or "",
        "transform_run_id": plan.run_id,
        "dataset_id": result.dataset_id,
        "dataset_ref": result.dataset_ref,
        "transaction_id": result.transaction_id,
        "version_id": result.version_id,
        "version_number": result.version_number,
        "row_count": result.row_count,
        "manifest_uri": result.manifest_uri,
        "schema_hash": result.schema_hash,
    }


def _supported_transform_language(language: str) -> str:
    normalized = language.strip().lower()
    if normalized == "sql":
        return normalized
    raise ValidationFailed(
        "unsupported transform language",
        details={"language": language, "supported_languages": ["sql"]},
    )
