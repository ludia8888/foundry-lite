from __future__ import annotations

from pathlib import Path
from typing import Any

from foundry_lite.application.ports.transform_repository import (
    TransformRecord,
    TransformRunRecord,
)
from foundry_lite.application.primitives import (
    INPUT_PATTERN,
    CommitResult,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
    ValidationFailed,
)


class TransformServiceMixin(CoreServiceMixin):
    def register_transform(
        self,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: dict[str, str],
        output_dataset_ref: str,
        checks: list[dict[str, Any]] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
        language: str = "sql",
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "transform:run")
        transform_id = _new_id("tf")
        normalized_checks = checks or []
        with self.engine.begin() as conn:
            existing = self.transform_repository.transform_by_api_name(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                api_name=api_name,
            )
            if existing:
                self.transform_repository.update_transform_definition(
                    transaction=conn,
                    transform_id=existing["id"],
                    language=language,
                    entrypoint=str(entrypoint),
                    mode=mode,
                    inputs=inputs,
                    output_dataset_ref=output_dataset_ref,
                    checks=normalized_checks,
                )
                return dict(existing)
            self.transform_repository.insert_transform(
                transaction=conn,
                record=TransformRecord(
                    transform_id=transform_id,
                    tenant_id=ctx.tenant_id,
                    api_name=api_name,
                    language=language,
                    entrypoint=str(entrypoint),
                    mode=mode,
                    inputs=inputs,
                    output_dataset_ref=output_dataset_ref,
                    checks=normalized_checks,
                ),
            )
            return self.transform_repository.transform_by_id(transaction=conn, transform_id=transform_id) or {}

    def run_transform(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "transform:run", "transform", api_name)
        with self.engine.begin() as conn:
            transform = self._get_transform(conn, ctx, api_name)
            output_dataset = self.get_dataset(transform["output_dataset_ref"], ctx=ctx)
            input_versions = self._resolve_transform_inputs(conn, ctx, transform)
            tx_id = self._open_dataset_transaction(conn, ctx, output_dataset, "SNAPSHOT")
            run_id = _new_id("transform_run")
            self.transform_repository.insert_transform_run(
                transaction=conn,
                record=TransformRunRecord(
                    transform_run_id=run_id,
                    tenant_id=ctx.tenant_id,
                    transform_id=transform["id"],
                    status="RUNNING",
                    input_versions=input_versions,
                    output_version_id=None,
                    transaction_id=tx_id,
                    error=None,
                    created_at=_now(),
                    completed_at=None,
                ),
            )

        staged = self._staging_file(output_dataset, tx_id, "part-00000.parquet")
        try:
            self._execute_sql_transform(transform, input_versions, staged)
            with self.engine.begin() as conn:
                result = self._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=output_dataset,
                    transaction_id=tx_id,
                    staged_parquet=staged,
                    run_id=run_id,
                    audit_action="transform_output_commit",
                    outbox_event_type="dataset.version.committed",
                    extra_checks=transform["checks"],
                )
                for version_id in input_versions.values():
                    self._lineage(
                        conn,
                        ctx,
                        "dataset_version",
                        version_id,
                        "dataset_version",
                        result.version_id,
                        "input_to",
                        run_id,
                    )
                self.transform_repository.update_transform_run_terminal(
                    transaction=conn,
                    transform_run_id=run_id,
                    status="SUCCESS",
                    output_version_id=result.version_id,
                    error=None,
                    completed_at=_now(),
                )
                return result
        except Exception as exc:
            self._abort_transaction_after_error(ctx, tx_id, run_id, exc, "transform")
            if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
                raise
            raise ValidationFailed("transform failed", details={"error": str(exc)}) from exc

    def _get_transform(self, conn: Any, ctx: RequestContext, api_name: str) -> dict[str, Any]:
        row = self.transform_repository.transform_by_api_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            api_name=api_name,
        )
        if row is None:
            raise NotFound("transform not found", details={"api_name": api_name})
        return row

    def _resolve_transform_inputs(
        self,
        conn: Any,
        ctx: RequestContext,
        transform: dict[str, Any],
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        template = Path(transform["entrypoint"]).read_text(encoding="utf-8")
        referenced = set(INPUT_PATTERN.findall(template))
        configured = set(transform["inputs"].values())
        for dataset_ref in sorted(referenced | configured):
            dataset = self.get_dataset(dataset_ref, ctx=ctx)
            version = self._latest_version_by_dataset_id(conn, dataset["id"])
            resolved[dataset_ref] = version["id"]
        return resolved

    def _execute_sql_transform(
        self,
        transform: dict[str, Any],
        input_versions: dict[str, str],
        staged: Path,
    ) -> None:
        sql = Path(transform["entrypoint"]).read_text(encoding="utf-8")
        input_paths_by_ref = {
            dataset_ref: self._version_file_path(self._get_version_by_id(version_id))
            for dataset_ref, version_id in input_versions.items()
        }
        self.compute_adapter.execute_sql_transform(
            sql_template=sql,
            input_paths_by_ref=input_paths_by_ref,
            target_path=staged,
        )
