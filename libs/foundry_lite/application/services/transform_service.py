from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Connection

from foundry_lite.application.primitives import (
    INPUT_PATTERN,
    CommitResult,
    _new_id,
    _now,
    _sql_identifier,
    _sql_literal,
)
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
    ValidationFailed,
)
from foundry_lite.infrastructure import schema as db


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
        with self.engine.begin() as conn:
            existing = (
                conn.execute(
                    select(db.transforms).where(
                        and_(
                            db.transforms.c.tenant_id == ctx.tenant_id,
                            db.transforms.c.api_name == api_name,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                conn.execute(
                    update(db.transforms)
                    .where(db.transforms.c.id == existing["id"])
                    .values(
                        language=language,
                        entrypoint=str(entrypoint),
                        mode=mode,
                        inputs=inputs,
                        output_dataset_ref=output_dataset_ref,
                        checks=checks or [],
                    )
                )
                return dict(existing)
            conn.execute(
                insert(db.transforms).values(
                    id=transform_id,
                    tenant_id=ctx.tenant_id,
                    api_name=api_name,
                    language=language,
                    entrypoint=str(entrypoint),
                    mode=mode,
                    inputs=inputs,
                    output_dataset_ref=output_dataset_ref,
                    checks=checks or [],
                )
            )
            return self._select_by_id(conn, db.transforms, transform_id) or {}

    def run_transform(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "transform:run", "transform", api_name)
        with self.engine.begin() as conn:
            transform = self._get_transform(conn, ctx, api_name)
            output_dataset = self.get_dataset(transform["output_dataset_ref"], ctx=ctx)
            input_versions = self._resolve_transform_inputs(conn, ctx, transform)
            tx_id = self._open_dataset_transaction(conn, ctx, output_dataset, "SNAPSHOT")
            run_id = _new_id("transform_run")
            conn.execute(
                insert(db.transform_runs).values(
                    id=run_id,
                    tenant_id=ctx.tenant_id,
                    transform_id=transform["id"],
                    status="RUNNING",
                    input_versions=input_versions,
                    output_version_id=None,
                    transaction_id=tx_id,
                    error=None,
                    created_at=_now(),
                    completed_at=None,
                )
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
                conn.execute(
                    update(db.transform_runs)
                    .where(db.transform_runs.c.id == run_id)
                    .values(
                        status="SUCCESS",
                        output_version_id=result.version_id,
                        completed_at=_now(),
                    )
                )
                return result
        except Exception as exc:
            self._abort_transaction_after_error(ctx, tx_id, run_id, exc, "transform")
            if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
                raise
            raise ValidationFailed("transform failed", details={"error": str(exc)}) from exc

    def _get_transform(self, conn: Connection, ctx: RequestContext, api_name: str) -> dict[str, Any]:
        row = (
            conn.execute(
                select(db.transforms).where(
                    and_(
                        db.transforms.c.tenant_id == ctx.tenant_id,
                        db.transforms.c.api_name == api_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("transform not found", details={"api_name": api_name})
        return dict(row)

    def _resolve_transform_inputs(
        self,
        conn: Connection,
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
        con = duckdb.connect()
        try:
            for index, (dataset_ref, version_id) in enumerate(input_versions.items()):
                version = self._get_version_by_id(version_id)
                view = f"input_{index}"
                _sql_identifier(view)
                con.read_parquet(str(self._version_file_path(version))).create_view(view, replace=True)
                sql = sql.replace(f"{{{{ input('{dataset_ref}') }}}}", view)
            unresolved = INPUT_PATTERN.findall(sql)
            if unresolved:
                raise ValidationFailed("transform has unresolved inputs", details={"inputs": unresolved})
            staged.parent.mkdir(parents=True, exist_ok=True)
            con.execute(f"copy ({sql}) to {_sql_literal(staged)} (format parquet)")
        finally:
            con.close()
