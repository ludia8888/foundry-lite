"""Application-layer models and helpers for foundry."""

from __future__ import annotations

import logging
import shutil
from types import TracebackType
from typing import Literal, Self, cast, overload

from foundry_lite.application.core_services import CoreServices
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.facades import (
    ActionGateway,
    AipWorkspace,
    AuthGateway,
    ConnectorWorkspace,
    DatasetWorkspace,
    DeveloperConsole,
    ErasureGateway,
    FdeMcpGateway,
    FunctionGateway,
    InsightReviewWorkspace,
    MaterializationRunner,
    MediaWorkspace,
    ObjectStore,
    OntologyMcpActionRuntimeAdapter,
    OntologyMcpGateway,
    OntologyRegistry,
    OperationsConsole,
    PipelineWorkspace,
    ResourceWorkspace,
    SourceWorkspace,
    SupplyChainDemo,
    TransformPipeline,
    VirtualTableGateway,
    build_governed_release_workspace,
)
from foundry_lite.application.foundry_lifecycle import FoundryRuntimeLifecycle
from foundry_lite.application.model_catalog_bootstrap import ensure_model_catalog
from foundry_lite.application.osdk import (
    OsdkActionInvoker,
    OsdkActionType,
    OsdkHost,
    OsdkObjectSet,
    OsdkObjectType,
    osdk_resource,
)
from foundry_lite.application.ports.action_function_executor import ActionFunctionExecutionRequest
from foundry_lite.application.ports.workflow_adapter import WorkflowStartRequest
from foundry_lite.application.primitives import (
    CommitResult,
    StagedFileStats,
    _dataset_ref_parts,
    _json_ready,
    _normalize_duckdb_type,
    _now,
    _required_row,
)
from foundry_lite.application.services.workflow_orchestration_service import CONNECTOR_SYNC_WORKFLOW_NAME
from foundry_lite.domain.context import DEFAULT_ACTOR_USER_ID, DEFAULT_TENANT_ID, DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.observability.tracing import trace_public_methods

__all__ = ["CommitResult", "FoundryLite", "StagedFileStats", "_dataset_ref_parts"]
__all__ += ["_json_ready", "_normalize_duckdb_type", "_required_row"]

_LOGGER = logging.getLogger(__name__)


@trace_public_methods
class FoundryLite:
    """Platform root for the MVP closed loop.

    Exposes one facade per bounded context; injected services own each domain
    workflow. Lifecycle (bootstrap/reset) stays on this root.
    """

    def __init__(
        self,
        *,
        dependencies: CoreDependencies,
        should_initialize_schema: bool = True,
        is_stream_adapter_owned: bool = True,
        is_engine_owned: bool = True,
        is_orchestrator_owned: bool = True,
    ) -> None:
        self._lifecycle = FoundryRuntimeLifecycle(
            is_stream_adapter_owned=is_stream_adapter_owned,
            is_engine_owned=is_engine_owned,
            is_orchestrator_owned=is_orchestrator_owned,
        )
        try:
            self._attach_dependencies(dependencies)
            services = CoreServices.create(dependencies)
            self._services = services
            self._attach_facades(services)
            self._bind_local_workflow_drivers(dependencies, services)
            if should_initialize_schema:
                self._initialize_schema_for_unprotected_profile()
            self.bootstrap()
        except BaseException as exc:
            self._lifecycle.close_failed_initialization(
                stream_adapter=dependencies.stream_adapter,
                engine=dependencies.engine,
                pipeline_orchestrator=dependencies.pipeline_dag_orchestrator,
                action_orchestrator=dependencies.action_run_orchestrator,
                primary_error=exc,
            )
            raise

    def _initialize_schema_for_unprotected_profile(self) -> None:
        """Create metadata tables for local runtimes only.

        Protected profiles take their schema from the Alembic migration chain, so
        the metadata repository refuses schema mutation there and
        ``initialize_schema`` raises ``SchemaMutationDisabledError``. Calling it
        unconditionally meant constructing ``FoundryLite`` under a production or
        staging profile always raised, before ``bootstrap()`` ever ran — the API,
        CLI, and every worker failed at startup. Bootstrap remains idempotent, but
        binds its tenant explicitly so protected PostgreSQL RLS can authorize its
        tenant-scoped inserts without granting the runtime role schema ownership.
        """
        if self.runtime_profile.is_protected:
            return
        self.metadata_repository.initialize_schema()

    def _attach_dependencies(self, dependencies: CoreDependencies) -> None:
        self.runtime_profile = dependencies.profile
        self.root = dependencies.root
        self.storage_root = dependencies.storage_root
        self.engine = dependencies.engine
        self.policy = dependencies.policy
        self.action_repository = dependencies.action_repository
        self.ontology_repository = dependencies.ontology_repository
        self.pipeline_repository = dependencies.pipeline_repository
        self.resource_catalog_repository = dependencies.resource_catalog_repository
        self.transform_repository = dependencies.transform_repository
        self.materialization_repository = dependencies.materialization_repository
        self.dataset_quality_repository = dependencies.dataset_quality_repository
        self.compute_adapter = dependencies.compute_adapter
        self.metadata_repository = dependencies.metadata_repository
        self.destructive_development_admin = dependencies.destructive_development_admin
        self.dataset_repository = dependencies.dataset_repository
        self.dataset_transaction_repository = dependencies.dataset_transaction_repository
        self.dataset_version_repository = dependencies.dataset_version_repository
        self.insight_review_repository = dependencies.insight_review_repository
        self.object_index_repository = dependencies.object_index_repository
        self.object_read_repository = dependencies.object_read_repository
        self.object_set_repository = dependencies.object_set_repository
        self.osdk_application_repository = dependencies.osdk_application_repository
        self.ai_run_repository = dependencies.ai_run_repository
        self.runtime_repository = dependencies.runtime_repository
        self.stream_adapter = dependencies.stream_adapter
        self.pipeline_dag_orchestrator = dependencies.pipeline_dag_orchestrator
        self.action_run_orchestrator = dependencies.action_run_orchestrator
        self.dataset_storage = dependencies.dataset_storage
        self.connector_registry_repository = dependencies.connector_registry_repository
        self.source_registry_repository = dependencies.source_registry_repository
        self.source_management_repository = dependencies.source_management_repository
        self.secret_provider = dependencies.secret_provider
        self.secret_vault = dependencies.secret_vault
        self.source_database_adapter = dependencies.source_database_adapter
        self.model_registry_repository = dependencies.model_registry_repository
        self.model_catalog_seed = dependencies.aip.model_catalog_seed

    def close(
        self,
        *,
        should_close_stream: bool | None = None,
        primary_error: BaseException | None = None,
    ) -> None:
        """Release resources without replacing an exception already in flight."""
        self._lifecycle.close(
            stream_adapter=self.stream_adapter,
            engine=self.engine,
            pipeline_orchestrator=self.pipeline_dag_orchestrator,
            action_orchestrator=self.action_run_orchestrator,
            should_close_stream=should_close_stream,
            primary_error=primary_error,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exception_type, traceback
        self.close(primary_error=exception)
        return False

    def _attach_facades(self, services: CoreServices) -> None:
        self._attach_data_facades(services)
        self._attach_aip_facades(services)
        self._attach_operations_facades(services)
        self.demo = SupplyChainDemo(services.demo, reset_fresh=lambda: self.reset(confirm_dev=True))

    def _attach_data_facades(self, services: CoreServices) -> None:
        self.datasets = DatasetWorkspace(services.dataset)
        self.transforms = TransformPipeline(services.transform.entrypoint)
        self.pipelines = PipelineWorkspace(services.pipelines.entrypoint)
        self.resources = ResourceWorkspace(services.resources)
        self.ontology = _ontology_registry(services)
        self.objects = ObjectStore(
            services.object_store,
            services.ontology_search,
            services.action.log_ontology,
        )
        self.actions = ActionGateway(
            services.action.entrypoint,
            services.action.notification_policies,
            services.action.effect_operations,
        )
        self.functions = FunctionGateway(services.function_execution)
        self.auth = AuthGateway(
            services.osdk_oauth_sessions,
            services.osdk_oauth_client_credentials,
            services.osdk_applications.entrypoint,
        )
        self.materialization = MaterializationRunner(services.materialization)
        self.insights = InsightReviewWorkspace(services.insight_review)
        self.media = MediaWorkspace(services.media)
        self.connectors = ConnectorWorkspace(services.connector_onboarding)
        self.sources = _source_workspace(services)
        self.virtual_tables = VirtualTableGateway(services.virtual_table)
        self.erasure = ErasureGateway(services.erasure)
        self.developer_console = DeveloperConsole(services.osdk_applications.entrypoint)

    def _attach_aip_facades(self, services: CoreServices) -> None:
        self.ontology_mcp = OntologyMcpGateway(
            applications=services.osdk_applications.entrypoint,
            objects=self.objects,
            unified_search=services.ontology_search,
            actions=OntologyMcpActionRuntimeAdapter(self.actions, services.action.entrypoint),
            functions=self.functions,
            approvals=services.action_proposal,
            access_sessions=services.osdk_access_sessions,
            rate_limits=services.mcp_rate_limits,
            business_systems=services.fde_pilot,
        )
        fde_mcp = FdeMcpGateway(
            engine=self.engine,
            policy=self.policy,
            ai_run_repository=self.ai_run_repository,
            context_validator=services.fde_context,
            platform_executor=services.fde_platform_tools,
            application_reader=services.osdk_applications.entrypoint,
            application_repository=self.osdk_application_repository,
            access_session_validator=services.osdk_access_sessions,
            rate_limits=services.mcp_rate_limits,
        )
        self.release = build_governed_release_workspace(services, fde_mcp)
        self.aip = AipWorkspace(
            services.agent_runtime,
            services.action_proposal,
            services.approval_execution,
            services.builder_runtime,
            services.logic_runtime,
            services.evals,
            services.fde_runtime,
            fde_mcp,
            services.fde_pilot,
            services.visual_builder,
            services.citation,
        )

    def _attach_operations_facades(self, services: CoreServices) -> None:
        self.operations = OperationsConsole(
            services.action.entrypoint,
            services.runtime,
            services.materialization,
            services.record_dlq,
            services.backup_restore.entrypoint,
            services.iceberg_maintenance,
            services.workflow,
            services.prompt_artifact,
            services.outbox_publisher,
        )

    def _bind_local_workflow_drivers(self, dependencies: CoreDependencies, services: CoreServices) -> None:
        self._bind_local_action_drivers(dependencies, services)
        register_pipeline_driver = getattr(
            dependencies.pipeline_dag_orchestrator,
            "register_driver",
            None,
        )
        if callable(register_pipeline_driver):
            register_pipeline_driver(
                lambda request: (
                    services.pipelines.preview.execute_dispatched_preview(request)
                    if request.is_commit_forbidden
                    else services.pipelines.async_run.execute_dispatched(request)
                )
            )
        register_driver = getattr(dependencies.workflow_adapter, "register_driver", None)
        if not callable(register_driver):
            return
        register_driver(
            CONNECTOR_SYNC_WORKFLOW_NAME,
            lambda request: self._run_local_connector_sync_driver(services, request),
        )

    def _bind_local_action_drivers(self, dependencies: CoreDependencies, services: CoreServices) -> None:
        register_run_driver = getattr(dependencies.action_run_orchestrator, "register_driver", None)
        if callable(register_run_driver):
            register_run_driver(
                lambda request: services.action.distributed.drive(request, worker_id="local-action-worker")
            )
        register_function_driver = getattr(dependencies.action_function_executor, "register_driver", None)
        if callable(register_function_driver):
            register_function_driver(lambda request: self._run_local_action_function(services, request))

    def _run_local_action_function(
        self, services: CoreServices, request: ActionFunctionExecutionRequest
    ) -> dict[str, object]:
        ctx = RequestContext(
            tenant_id=request.tenant_id,
            actor_user_id=request.actor_user_id,
            request_id=request.request_id,
            roles=request.roles,
            application_id=request.application_id,
            client_id=request.client_id,
            token_scopes=request.token_scopes,
            user_attributes=request.user_attributes,
        )
        result = services.function_execution.execute_pinned_function(
            request.function_api_name,
            function_version=request.function_version,
            ontology_version_id=request.ontology_version_id,
            inputs=request.inputs,
            ctx=ctx,
            execution_id=f"{request.run_id}:function",
        )
        return dict(result)

    def _run_local_connector_sync_driver(
        self,
        services: CoreServices,
        request: WorkflowStartRequest,
    ) -> dict[str, object]:
        payload = dict(request.input)
        if not payload.get("configFingerprint"):
            return {
                "processed": True,
                **payload,
                "workflow_name": request.workflow_name,
                "request_id": request.request_id,
            }
        ctx = RequestContext(
            tenant_id=request.tenant_id,
            actor_user_id=DEFAULT_ACTOR_USER_ID,
            request_id=request.request_id,
            roles=DEMO_ADMIN_ROLES,
        )
        return services.connector_onboarding.run_registered_sync_activity(payload, ctx=ctx)

    def reset(self, *, confirm_dev: bool = False) -> None:
        if not confirm_dev:
            raise ValidationFailed("reset is destructive and requires confirm_dev=True")
        self.destructive_development_admin.reset_schema()
        if self.storage_root.exists():
            shutil.rmtree(self.storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.bootstrap()

    def bootstrap(self) -> None:
        with FoundryRuntimeLifecycle.bootstrap_tenant_context(DEFAULT_TENANT_ID):
            self._bootstrap_default_tenant()

    def _bootstrap_default_tenant(self) -> None:
        now = _now()
        self.metadata_repository.ensure_tenant(
            tenant_id=DEFAULT_TENANT_ID,
            name="Demo Tenant",
            created_at=now,
        )
        self.metadata_repository.ensure_user(
            user_id=DEFAULT_ACTOR_USER_ID,
            tenant_id=DEFAULT_TENANT_ID,
            email="demo@foundry-lite.local",
            roles=["admin", "data_engineer", "ops_manager", "finance"],
            created_at=now,
        )
        self._seed_default_model_registry(now)

    def _seed_default_model_registry(self, now: str) -> None:
        try:
            self._ensure_demo_model_registry(now)
        except Exception:
            if self.runtime_profile.is_protected:
                raise
            _LOGGER.warning(
                "Demo model registry seed failed; continuing because runtime profile is local/demo/test.",
                extra={
                    "request_id": "bootstrap",
                    "runtime_profile": self.runtime_profile.name,
                    "tenant_id": DEFAULT_TENANT_ID,
                },
                exc_info=True,
            )

    @overload
    def __call__(
        self,
        resource: OsdkObjectType,
        *,
        ctx: RequestContext | None = None,
    ) -> OsdkObjectSet: ...

    @overload
    def __call__(
        self,
        resource: OsdkActionType,
        *,
        ctx: RequestContext | None = None,
    ) -> OsdkActionInvoker: ...

    def __call__(
        self,
        resource: OsdkObjectType | OsdkActionType,
        *,
        ctx: RequestContext | None = None,
    ) -> OsdkObjectSet | OsdkActionInvoker:
        return osdk_resource(cast(OsdkHost, self), resource, ctx=ctx)

    def _ensure_demo_model_registry(self, now: str) -> None:
        with self.engine.begin() as transaction:
            ensure_model_catalog(self.model_registry_repository, transaction, self.model_catalog_seed, now)


def _ontology_registry(services: CoreServices) -> OntologyRegistry:
    return OntologyRegistry(
        services.ontology.entrypoint,
        services.ontology.insights,
        services.ontology.proposals,
        services.ontology.branches,
    )


def _source_workspace(services: CoreServices) -> SourceWorkspace:
    return SourceWorkspace(services)
