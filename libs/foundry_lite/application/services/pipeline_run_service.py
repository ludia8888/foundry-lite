"""Pipeline Builder deploy, run, schedule, and timeline workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.pipeline_repository import (
    PipelineRepository,
    PipelineRunRow,
    PipelineVersionRow,
)
from foundry_lite.application.primitives import CommitResult, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_compiler_service import PipelineCompilerService
from foundry_lite.application.services.pipeline_graph_model import output_dataset_ref
from foundry_lite.application.services.pipeline_payloads import (
    bounded_pipeline_limit,
    run_payload,
    run_record,
    schedule_payload,
    schedule_record,
    version_payload,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.application.services.transform_service import TransformService
from foundry_lite.application.state_transitions import (
    PIPELINE_RUN_CANCELLED,
    PIPELINE_RUN_FAILED,
    PIPELINE_RUN_SUCCEEDED,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


class PipelineRunService(CoreService):
    """Deploy compiled pipeline versions and run them through existing transforms."""

    required_dependencies = ("engine", "policy", "pipeline_repository")
    required_collaborators = ("pipeline_compiler_service", "runtime_service", "transform_service")
    pipeline_compiler_service: PipelineCompilerService
    pipeline_repository: PipelineRepository
    runtime_service: RuntimeEvidenceBoundary
    transform_service: TransformService

    def deploy(
        self,
        pipeline_id: str,
        version_id: str,
        *,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:deploy", "pipeline_version", version_id)
        self._require_write_open(ctx, "deploy_pipeline_version", version_id)
        with self.engine.begin() as conn:
            version = self._require_version(conn, ctx, version_id)
            _require_pipeline_match(version, pipeline_id)
        compiled = self.pipeline_compiler_service.compile_graph(
            pipeline_id=pipeline_id,
            version_id=version_id,
            graph=version["graph"],
            ctx=ctx,
        )
        with self.engine.begin() as conn:
            row = self.pipeline_repository.mark_version_deployed(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                version_id=version_id,
                deployed_at=_now(),
            )
            if row is None:
                raise NotFound("pipeline version not found", details={"version_id": version_id})
            self._audit(conn, ctx, "deployed", "pipeline_version", version_id, compiled)
        return {"version": version_payload(row), "compiled": compiled, "options": dict(options or {})}

    def start_run(
        self,
        pipeline_id: str,
        *,
        version_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:run", "pipeline", pipeline_id)
        self._require_write_open(ctx, "start_pipeline_run", pipeline_id)
        version = self._deployed_version(ctx, pipeline_id, version_id)
        with self.engine.begin() as conn:
            row = self.pipeline_repository.insert_run(
                transaction=conn,
                record=run_record(ctx, pipeline_id=pipeline_id, version_id=str(version["id"]), now=_now()),
            )
        try:
            return self._execute_run(ctx, row, version)
        except Exception as exc:
            return self._fail_run(ctx, row, exc)

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            return run_payload(self._require_run(conn, ctx, run_id))

    def run_timeline(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        run = self.get_run(run_id, ctx=ctx)
        return {"runId": run_id, "timeline": run["timeline"]}

    def cancel_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:run", "pipeline_run", run_id)
        with self.engine.begin() as conn:
            row = self._require_run(conn, ctx, run_id)
            if row["status"] != "running":
                raise ConflictDetected("pipeline run is already terminal", details={"run_id": run_id})
            after = self.pipeline_repository.update_run_terminal(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                transition=PIPELINE_RUN_CANCELLED,
                output_dataset_ref=row["output_dataset_ref"],
                output_version_id=row["output_version_id"],
                timeline=[*row["timeline"], {"event": "pipeline.run.cancelled", "at": _now()}],
                error=None,
                completed_at=_now(),
            )
        return run_payload(after) if after is not None else self.get_run(run_id, ctx=ctx)

    def upsert_schedule(
        self,
        pipeline_id: str,
        *,
        version_id: str,
        schedule: Mapping[str, object],
        enabled: bool = True,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:deploy", "pipeline_schedule", pipeline_id)
        self._require_write_open(ctx, "upsert_pipeline_schedule", pipeline_id)
        with self.engine.begin() as conn:
            version = self._require_version(conn, ctx, version_id)
            _require_pipeline_match(version, pipeline_id)
            row = self.pipeline_repository.upsert_schedule(
                transaction=conn,
                record=schedule_record(
                    ctx,
                    pipeline_id=pipeline_id,
                    version_id=version_id,
                    schedule=schedule,
                    enabled=enabled,
                    now=_now(),
                ),
            )
        return schedule_payload(row) or {}

    def get_schedule(self, pipeline_id: str, *, ctx: RequestContext | None = None) -> dict[str, object] | None:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            row = self.pipeline_repository.schedule_by_pipeline(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
            )
        return schedule_payload(row)

    def delete_schedule(self, pipeline_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:deploy", "pipeline_schedule", pipeline_id)
        self._require_write_open(ctx, "delete_pipeline_schedule", pipeline_id)
        with self.engine.begin() as conn:
            deleted = self.pipeline_repository.delete_schedule(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
            )
        return {"deleted": deleted}

    def preview_due_schedules(
        self,
        *,
        max_runs: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            rows = self.pipeline_repository.list_due_schedules(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                limit=bounded_pipeline_limit(max_runs),
            )
        return {"items": [schedule_payload(row) for row in rows], "maxRuns": bounded_pipeline_limit(max_runs)}

    def tick_schedules(
        self,
        *,
        max_runs: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:run", "pipeline_scheduler", "tick")
        due = cast(list[dict[str, object]], self.preview_due_schedules(max_runs=max_runs, ctx=ctx)["items"])
        started = [self.start_run(str(item["pipelineId"]), version_id=str(item["versionId"]), ctx=ctx) for item in due]
        return {"started": started, "maxRuns": bounded_pipeline_limit(max_runs)}

    def _execute_run(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
    ) -> dict[str, object]:
        compiled = self.pipeline_compiler_service.compile_graph(
            pipeline_id=str(version["pipeline_id"]),
            version_id=str(version["id"]),
            graph=version["graph"],
            ctx=ctx,
        )
        timeline = [*row["timeline"], {"event": "pipeline.compile.completed", "at": _now(), **compiled}]
        result = _run_compiled_transforms(self.transform_service, ctx, compiled, timeline)
        return self._succeed_run(ctx, row, version, timeline, result)

    def _succeed_run(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        timeline: list[dict[str, object]],
        result: CommitResult | None,
    ) -> dict[str, object]:
        with self.engine.begin() as conn:
            after = self.pipeline_repository.update_run_terminal(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                transition=PIPELINE_RUN_SUCCEEDED,
                output_dataset_ref=output_dataset_ref(version["graph"]),
                output_version_id=result.version_id if result is not None else None,
                timeline=[*timeline, {"event": "pipeline.run.succeeded", "at": _now()}],
                error=None,
                completed_at=_now(),
            )
            self._audit(conn, ctx, "succeeded", "pipeline_run", str(row["id"]), {"version_id": version["id"]})
        return run_payload(after) if after is not None else self.get_run(str(row["id"]), ctx=ctx)

    def _fail_run(self, ctx: RequestContext, row: PipelineRunRow, exc: Exception) -> dict[str, object]:
        error = self.runtime_service._error_payload(exc, ctx, run_id=str(row["id"]))
        with self.engine.begin() as conn:
            after = self.pipeline_repository.update_run_terminal(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                transition=PIPELINE_RUN_FAILED,
                output_dataset_ref=row["output_dataset_ref"],
                output_version_id=row["output_version_id"],
                timeline=[*row["timeline"], {"event": "pipeline.run.failed", "at": _now(), "error": dict(error)}],
                error=dict(error),
                completed_at=_now(),
            )
        return run_payload(after) if after is not None else self.get_run(str(row["id"]), ctx=ctx)

    def _deployed_version(
        self,
        ctx: RequestContext,
        pipeline_id: str,
        version_id: str | None,
    ) -> PipelineVersionRow:
        with self.engine.begin() as conn:
            if version_id is not None:
                version = self._require_version(conn, ctx, version_id)
                _require_pipeline_match(version, pipeline_id)
                _require_deployed(version)
                return version
            rows = self.pipeline_repository.list_versions(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
                limit=100,
            )
        for row in rows:
            if row["deployed_at"] is not None:
                return row
        raise NotFound("deployed pipeline version not found", details={"pipeline_id": pipeline_id})

    def _require_version(self, conn: TransactionContext, ctx: RequestContext, version_id: str) -> PipelineVersionRow:
        row = self.pipeline_repository.version_by_id(transaction=conn, tenant_id=ctx.tenant_id, version_id=version_id)
        if row is None:
            raise NotFound("pipeline version not found", details={"version_id": version_id})
        return row

    def _require_run(self, conn: TransactionContext, ctx: RequestContext, run_id: str) -> PipelineRunRow:
        row = self.pipeline_repository.run_by_id(transaction=conn, tenant_id=ctx.tenant_id, run_id=run_id)
        if row is None:
            raise NotFound("pipeline run not found", details={"run_id": run_id})
        return row

    def _require_write_open(self, ctx: RequestContext, operation: str, resource_id: str) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation=operation,
            resource_type="pipeline",
            resource_id=resource_id,
        )

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event: str,
        resource_type: str,
        resource_id: str,
        after_ref: Mapping[str, object],
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"pipeline.{event}",
            resource_type=resource_type,
            resource_id=resource_id,
            action=event,
            after_ref=after_ref,
        )


def _run_compiled_transforms(
    transform_service: TransformService,
    ctx: RequestContext,
    compiled: Mapping[str, object],
    timeline: list[dict[str, object]],
) -> CommitResult | None:
    result: CommitResult | None = None
    for item in _compiled_items(compiled):
        api_name = str(item["apiName"])
        result = transform_service.run_transform(api_name, ctx=ctx)
        timeline.append(
            {
                "event": "pipeline.node.succeeded",
                "at": _now(),
                "nodeId": item["nodeId"],
                "transformApiName": api_name,
                "operationRunType": "transform",
                "outputVersionId": result.version_id,
            }
        )
    return result


def _compiled_items(compiled: Mapping[str, object]) -> list[dict[str, object]]:
    items = compiled.get("transforms")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict) and "apiName" in item]


def _require_pipeline_match(version: PipelineVersionRow, pipeline_id: str) -> None:
    if version["pipeline_id"] != pipeline_id:
        raise ValidationFailed(
            "pipeline version belongs to a different pipeline",
            details={"pipeline_id": pipeline_id, "versionPipelineId": version["pipeline_id"]},
        )


def _require_deployed(version: PipelineVersionRow) -> None:
    if version["deployed_at"] is None:
        raise ConflictDetected("pipeline version is not deployed", details={"version_id": version["id"]})
