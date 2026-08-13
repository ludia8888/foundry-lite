"""Idempotent Pipeline Builder deployment and immutable plan promotion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineDeploymentRecord,
    PipelineDeploymentRow,
    PipelineExecutionRepository,
)
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineVersionRow
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_deployment_admission import (
    LEGACY_DEPLOYMENT_CAPABILITIES,
    deployment_receipt,
    project_committed_deployment,
    require_stored_version,
)
from foundry_lite.application.services.pipeline_deployment_admission import (
    has_dataset_source as _has_dataset_source,
)
from foundry_lite.application.services.pipeline_execution_contracts import (
    ModelRef,
    PipelineExecutionPlan,
    pipeline_execution_plan_payload,
)
from foundry_lite.application.services.pipeline_payloads import (
    bounded_pipeline_limit,
    required_text,
    version_payload,
)
from foundry_lite.application.services.pipeline_payloads import (
    locked_deployment_replay as _locked_deployment_replay,
)
from foundry_lite.application.services.pipeline_payloads import (
    optional_text as _optional_text,
)
from foundry_lite.application.services.pipeline_payloads import (
    require_expected_current_deployment as _require_expected_current_deployment,
)
from foundry_lite.application.services.pipeline_payloads import (
    require_matching_deployment as _require_matching_deployment,
)
from foundry_lite.application.services.pipeline_plan_compiler import PipelinePlanCompiler
from foundry_lite.application.services.pipeline_source_contract_resolver import (
    PipelineSourceContractResolver,
)
from foundry_lite.application.services.pipeline_source_contracts import PipelineSourceResolution
from foundry_lite.application.services.pipeline_trained_model_contracts import (
    require_trained_model_import,
    trained_model_branch_config,
    trained_model_definition_snapshot,
    validate_trained_model_config,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class PipelineDeploymentCompiler(Protocol):
    """Compile the legacy deployment projection used by the promotion service."""

    def compile_graph(
        self,
        *,
        pipeline_id: str,
        version_id: str,
        graph: Mapping[str, object],
        ctx: RequestContext,
    ) -> dict[str, object]:
        """Compile one pinned pipeline graph without exposing its concrete service."""
        ...


class PipelineDeploymentService(CoreService):
    """Promote a pinned plan while preserving replay and operator evidence."""

    required_dependencies = (
        "engine",
        "policy",
        "pipeline_repository",
        "pipeline_execution_repository",
        "dataset_repository",
        "dataset_version_repository",
        "dataset_quality_repository",
        "media_repository",
        "source_management_repository",
        "trained_model_inference_port",
    )
    required_collaborators = ("pipeline_compiler_service", "runtime_service")
    pipeline_compiler_service: PipelineDeploymentCompiler
    pipeline_execution_repository: PipelineExecutionRepository
    pipeline_repository: PipelineRepository
    runtime_service: RuntimeEvidenceBoundary

    def deploy(
        self,
        pipeline_id: str,
        version_id: str,
        *,
        idempotency_key: str,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        key = required_text(idempotency_key, "Idempotency-Key")
        self.runtime_service._require_or_audit(ctx, "pipeline:deploy", "pipeline_version", version_id)
        self._require_write_open(ctx, version_id)
        request_fingerprint = _deployment_request_fingerprint(pipeline_id, version_id, options)
        replay = self._deployment_replay(ctx, key, request_fingerprint, options)
        if replay is not None:
            return replay
        version = self._version(ctx, pipeline_id, version_id)
        if _has_dataset_source(version["graph"]):
            self.policy.require(ctx, "dataset:read")
        source_resolution = self._resolve_sources(version["graph"], ctx)
        model_refs = self._trained_model_refs(version["graph"])
        plan = PipelinePlanCompiler().compile(
            version["graph"],
            model_refs=model_refs,
            source_resolution=source_resolution,
            require_source_contracts=True,
        )
        plan_payload = pipeline_execution_plan_payload(plan)
        compiled = self._compile_deployment(pipeline_id, version_id, version["graph"], plan, plan_payload, ctx)
        return self._commit_deployment(ctx, version, key, request_fingerprint, plan_payload, compiled, options)

    def _trained_model_refs(self, graph: Mapping[str, object]) -> tuple[ModelRef, ...]:
        nodes = graph.get("nodes")
        if not isinstance(nodes, list):
            return ()
        refs: list[ModelRef] = []
        for raw_node in nodes:
            if not isinstance(raw_node, Mapping) or raw_node.get("descriptorId") != "transform.trained_model":
                continue
            config = raw_node.get("config")
            if not isinstance(config, Mapping):
                raise ValidationFailed("trained model node config is invalid")
            model_ref = required_text(config.get("modelRef"), "modelRef")
            require_trained_model_import(graph, model_ref)
            branch, fallbacks = trained_model_branch_config(config)
            definition = self.trained_model_inference_port.resolve(
                model_ref,
                branch=branch,
                fallback_branches=fallbacks,
            )
            validate_trained_model_config(config, definition)
            refs.append(
                ModelRef(
                    model_id=definition.model_ref,
                    model_version=definition.version,
                    provider="trained_model",
                    revision=definition.revision,
                    parameters_fingerprint=_json_hash(dict(config)),
                    executable_reference=definition.executable_reference,
                    definition_snapshot=trained_model_definition_snapshot(definition),
                )
            )
        return tuple(refs)

    def _compile_deployment(
        self,
        pipeline_id: str,
        version_id: str,
        graph: Mapping[str, object],
        plan: PipelineExecutionPlan,
        plan_payload: Mapping[str, object],
        ctx: RequestContext,
    ) -> dict[str, object]:
        if _requires_graph_v2_runtime(plan):
            return _engine_neutral_plan_summary(pipeline_id, version_id, plan_payload)
        return self.pipeline_compiler_service.compile_graph(
            pipeline_id=pipeline_id,
            version_id=version_id,
            graph=graph,
            ctx=ctx,
        )

    def _resolve_sources(
        self,
        graph: Mapping[str, object],
        ctx: RequestContext,
    ) -> PipelineSourceResolution:
        resolver = PipelineSourceContractResolver(
            dataset_repository=self.dataset_repository,
            dataset_version_repository=self.dataset_version_repository,
            dataset_quality_repository=self.dataset_quality_repository,
            media_repository=self.media_repository,
            source_management_repository=self.source_management_repository,
        )
        with self.engine.begin() as conn:
            return resolver.resolve(transaction=conn, graph=graph, ctx=ctx)

    def list_deployments(
        self,
        pipeline_id: str,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            rows = self.pipeline_execution_repository.list_deployments(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
                limit=bounded_pipeline_limit(limit),
            )
        return {"items": [_deployment_payload(row) for row in rows], "nextCursor": None}

    def replay_deployment(
        self,
        idempotency_key: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object] | None:
        """Return a durable deployment receipt without starting a new promotion."""
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        key = required_text(idempotency_key, "Idempotency-Key")
        with self.engine.begin() as conn:
            receipt = deployment_receipt(
                self.pipeline_execution_repository,
                self.pipeline_repository,
                conn,
                ctx,
                key,
            )
            if receipt is None:
                return None
            row, version = receipt
        return project_committed_deployment(lambda: _deployment_result(row, version, {"replayed": True}, None))

    def _commit_deployment(
        self,
        ctx: RequestContext,
        version: PipelineVersionRow,
        idempotency_key: str,
        request_fingerprint: str,
        plan: dict[str, object],
        compiled: dict[str, object],
        options: Mapping[str, object] | None,
    ) -> dict[str, object]:
        now = _now()
        record = _deployment_record(ctx, version, idempotency_key, request_fingerprint, plan, options, now)
        with self.engine.begin() as conn:
            self.pipeline_repository.lock_pipeline_for_deployment(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=str(version["pipeline_id"]),
            )
            replay = _locked_deployment_replay(
                self.pipeline_execution_repository,
                self.pipeline_repository,
                conn,
                ctx,
                idempotency_key,
                request_fingerprint,
            )
            if replay is not None:
                replayed_deployment, replayed_version = replay
                return project_committed_deployment(
                    lambda: _deployment_result(replayed_deployment, replayed_version, {"replayed": True}, options)
                )
            _require_expected_current_deployment(self.pipeline_execution_repository, conn, ctx, version, options)
            deployment = self.pipeline_execution_repository.insert_deployment(transaction=conn, record=record)
            _require_matching_deployment(deployment, request_fingerprint)
            if deployment["id"] == record.deployment_id:
                deployed = self._mark_version(conn, ctx, version, plan, now)
                self._audit(conn, ctx, deployment)
            else:
                deployed = require_stored_version(self.pipeline_repository, conn, ctx, str(deployment["version_id"]))
        return project_committed_deployment(lambda: _deployment_result(deployment, deployed, compiled, options))

    def _deployment_replay(
        self,
        ctx: RequestContext,
        idempotency_key: str,
        request_fingerprint: str,
        options: Mapping[str, object] | None,
    ) -> dict[str, object] | None:
        with self.engine.begin() as conn:
            receipt = deployment_receipt(
                self.pipeline_execution_repository,
                self.pipeline_repository,
                conn,
                ctx,
                idempotency_key,
            )
            if receipt is None:
                return None
            row, version = receipt
            _require_matching_deployment(row, request_fingerprint)
        return project_committed_deployment(lambda: _deployment_result(row, version, {"replayed": True}, options))

    def _version(self, ctx: RequestContext, pipeline_id: str, version_id: str) -> PipelineVersionRow:
        with self.engine.begin() as conn:
            version = self.pipeline_repository.version_by_id(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                version_id=version_id,
            )
        if version is None:
            raise NotFound("pipeline version not found", details={"version_id": version_id})
        if version["pipeline_id"] != pipeline_id:
            raise ValidationFailed(
                "pipeline version belongs to a different pipeline",
                details={"version_id": version_id},
            )
        return version

    def _mark_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        version: PipelineVersionRow,
        plan: dict[str, object],
        deployed_at: str,
    ) -> PipelineVersionRow:
        row = self.pipeline_repository.mark_version_deployed(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            version_id=str(version["id"]),
            execution_plan=plan,
            plan_fingerprint=str(plan["planFingerprint"]),
            compiler_version=str(plan["compilerVersion"]),
            deployed_at=deployed_at,
        )
        if row is None:
            raise NotFound("pipeline version not found", details={"version_id": version["id"]})
        return row

    def _require_write_open(self, ctx: RequestContext, resource_id: str) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="deploy_pipeline_version",
            resource_type="pipeline_version",
            resource_id=resource_id,
        )

    def _audit(self, conn: TransactionContext, ctx: RequestContext, row: PipelineDeploymentRow) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="pipeline.deployment.promoted",
            resource_type="pipeline_deployment",
            resource_id=str(row["id"]),
            action="promote",
            after_ref={
                "pipelineId": row["pipeline_id"],
                "versionId": row["version_id"],
                "planFingerprint": row["plan_fingerprint"],
            },
        )


def _deployment_record(
    ctx: RequestContext,
    version: PipelineVersionRow,
    idempotency_key: str,
    request_fingerprint: str,
    plan: dict[str, object],
    options: Mapping[str, object] | None,
    now: str,
) -> PipelineDeploymentRecord:
    return PipelineDeploymentRecord(
        deployment_id=_new_id("pdep"),
        tenant_id=ctx.tenant_id,
        pipeline_id=str(version["pipeline_id"]),
        version_id=str(version["id"]),
        status="PROMOTED",
        execution_plan=plan,
        plan_fingerprint=str(plan["planFingerprint"]),
        compiler_version=str(plan["compilerVersion"]),
        processor_pins=_processor_pins(plan),
        model_pins=_json_rows(plan.get("modelRefs")),
        function_pins=_function_pins(plan),
        compute_profile=_json_object(plan.get("computeProfile")),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        promoted_by=ctx.actor_user_id,
        promoted_at=now,
        rolled_back_from_id=_optional_text(options, "rolledBackFromId"),
        created_by=ctx.actor_user_id,
        created_at=now,
    )


def _deployment_request_fingerprint(
    pipeline_id: str,
    version_id: str,
    options: Mapping[str, object] | None,
) -> str:
    return _json_hash({"pipelineId": pipeline_id, "versionId": version_id, "options": dict(options or {})})


def _deployment_result(
    deployment: PipelineDeploymentRow,
    version: PipelineVersionRow,
    compiled: Mapping[str, object],
    options: Mapping[str, object] | None,
) -> dict[str, object]:
    return {
        "deployment": _deployment_payload(deployment),
        "version": version_payload(version),
        "compiled": dict(compiled),
        "options": dict(options or {}),
    }


def _requires_graph_v2_runtime(plan: PipelineExecutionPlan) -> bool:
    return any(node.runtime_capability not in LEGACY_DEPLOYMENT_CAPABILITIES for node in plan.nodes)


def _engine_neutral_plan_summary(
    pipeline_id: str,
    version_id: str,
    plan: Mapping[str, object],
) -> dict[str, object]:
    nodes = _json_rows(plan.get("nodes"))
    edges = _json_rows(plan.get("edges"))
    artifacts = _json_rows(plan.get("artifacts"))
    capabilities = sorted({str(node.get("runtimeCapability")) for node in nodes})
    return {
        "pipelineId": pipeline_id,
        "versionId": version_id,
        "executionKind": "pipeline_graph_v2",
        "engineNeutral": True,
        "graphSchemaVersion": plan.get("graphSchemaVersion"),
        "planFingerprint": plan.get("planFingerprint"),
        "runtimeCapabilities": capabilities,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "artifactCount": len(artifacts),
        "resultArtifactIds": _json_texts(plan.get("resultArtifactIds")),
    }


def _deployment_payload(row: PipelineDeploymentRow) -> dict[str, object]:
    return {
        "id": row["id"],
        "pipelineId": row["pipeline_id"],
        "versionId": row["version_id"],
        "deploymentNumber": row["deployment_number"],
        "status": row["status"],
        "executionPlan": row["execution_plan"],
        "planFingerprint": row["plan_fingerprint"],
        "compilerVersion": row["compiler_version"],
        "processorPins": row["processor_pins"],
        "modelPins": row["model_pins"],
        "functionPins": row["function_pins"],
        "computeProfile": row["compute_profile"],
        "idempotencyKey": row["idempotency_key"],
        "requestFingerprint": row["request_fingerprint"],
        "promotedBy": row["promoted_by"],
        "promotedAt": row["promoted_at"],
        "rolledBackFromId": row["rolled_back_from_id"],
        "createdAt": row["created_at"],
    }


def _processor_pins(plan: Mapping[str, object]) -> list[dict[str, object]]:
    rows = _json_rows(plan.get("nodes"))
    return [
        {"descriptorId": row.get("descriptorId"), "specVersion": row.get("specVersion")}
        for row in rows
        if str(row.get("runtimeCapability") or "").startswith("media")
    ]


def _function_pins(plan: Mapping[str, object]) -> list[dict[str, object]]:
    rows = _json_rows(plan.get("nodes"))
    return [
        {"nodeId": row.get("nodeId"), "configFingerprint": _json_hash(_json_object(row.get("config")))}
        for row in rows
        if row.get("descriptorId") in {"transform.sql", "transform.python"}
    ]


def _json_rows(value: object) -> list[dict[str, object]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _json_texts(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _json_object(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}
