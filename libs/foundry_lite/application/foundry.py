"""Application-layer models and helpers for foundry."""

from __future__ import annotations

import shutil
from typing import cast, overload

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
    FunctionGateway,
    InsightReviewWorkspace,
    MaterializationRunner,
    MediaWorkspace,
    ObjectStore,
    OntologyRegistry,
    OperationsConsole,
    SourceWorkspace,
    SupplyChainDemo,
    TransformPipeline,
)
from foundry_lite.application.osdk import (
    OsdkActionInvoker,
    OsdkActionType,
    OsdkHost,
    OsdkObjectSet,
    OsdkObjectType,
    osdk_resource,
)
from foundry_lite.application.ports.model_registry_repository import (
    ModelAliasRecord,
    ModelProviderRecord,
    ModelRecord,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.primitives import (
    CommitResult,
    StagedFileStats,
    _dataset_ref_parts,
    _json_ready,
    _normalize_duckdb_type,
    _now,
    _required_row,
)
from foundry_lite.domain.context import DEFAULT_ACTOR_USER_ID, DEFAULT_TENANT_ID, RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.observability.tracing import trace_public_methods

__all__ = ["CommitResult", "FoundryLite", "StagedFileStats", "_dataset_ref_parts"]
__all__ += ["_json_ready", "_normalize_duckdb_type", "_required_row"]

_LOCAL_FAKE_CREDENTIAL_REF = "local-fake-credential-ref"


@trace_public_methods
class FoundryLite:
    """Platform root for the MVP closed loop.

    Exposes one facade per bounded context; injected services own each domain
    workflow. Lifecycle (bootstrap/reset) stays on this root.
    """

    def __init__(self, *, dependencies: CoreDependencies) -> None:
        self._attach_dependencies(dependencies)
        services = CoreServices.create(dependencies)
        self._services = services
        self._attach_facades(services)
        self.metadata_repository.initialize_schema()
        self.bootstrap()

    def _attach_dependencies(self, dependencies: CoreDependencies) -> None:
        self.root = dependencies.root
        self.storage_root = dependencies.storage_root
        self.engine = dependencies.engine
        self.policy = dependencies.policy
        self.action_repository = dependencies.action_repository
        self.ontology_repository = dependencies.ontology_repository
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
        self.runtime_repository = dependencies.runtime_repository
        self.dataset_storage = dependencies.dataset_storage
        self.connector_registry_repository = dependencies.connector_registry_repository
        self.source_registry_repository = dependencies.source_registry_repository
        self.source_management_repository = dependencies.source_management_repository
        self.secret_provider = dependencies.secret_provider
        self.secret_vault = dependencies.secret_vault
        self.source_database_adapter = dependencies.source_database_adapter
        self.model_registry_repository = dependencies.model_registry_repository

    def _attach_facades(self, services: CoreServices) -> None:
        self.datasets = DatasetWorkspace(services.dataset)
        self.transforms = TransformPipeline(services.transform.entrypoint)
        self.ontology = _ontology_registry(services)
        self.objects = ObjectStore(services.object_store, services.ontology_search)
        self.actions = ActionGateway(services.action.entrypoint)
        self.functions = FunctionGateway(services.function_execution)
        self.auth = AuthGateway(services.osdk_oauth_sessions)
        self.aip = AipWorkspace(
            services.agent_runtime,
            services.action_proposal,
            services.approval_execution,
            services.builder_runtime,
            services.logic_runtime,
            services.evals,
            services.visual_builder,
        )
        self.materialization = MaterializationRunner(services.materialization)
        self.insights = InsightReviewWorkspace(services.insight_review)
        self.media = MediaWorkspace(services.media)
        self.connectors = ConnectorWorkspace(services.connector_onboarding)
        self.sources = SourceWorkspace(
            services.source_onboarding,
            services.source_management,
            services.source_scheduler,
        )
        self.erasure = ErasureGateway(services.erasure)
        self.developer_console = DeveloperConsole(services.osdk_applications.entrypoint)
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
        self.demo = SupplyChainDemo(services.demo, reset_fresh=lambda: self.reset(confirm_dev=True))

    def reset(self, *, confirm_dev: bool = False) -> None:
        if not confirm_dev:
            raise ValidationFailed("reset is destructive and requires confirm_dev=True")
        self.destructive_development_admin.reset_schema()
        if self.storage_root.exists():
            shutil.rmtree(self.storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.bootstrap()

    def bootstrap(self) -> None:
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
        try:
            self._ensure_demo_model_registry(now)
        except Exception:
            return

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
            self._create_demo_model_registry_rows(transaction, now)

    def _create_demo_model_registry_rows(self, transaction: TransactionContext, now: str) -> None:
        if not self.model_registry_repository.get_providers(
            transaction=transaction,
            tenant_id=DEFAULT_TENANT_ID,
            provider_ids=["local-fake-provider"],
        ):
            self.model_registry_repository.create_provider(transaction=transaction, record=_demo_provider_record(now))
        if not self.model_registry_repository.get_models(
            transaction=transaction,
            tenant_id=DEFAULT_TENANT_ID,
            model_ids=["local-fake-model"],
        ):
            self.model_registry_repository.create_model(transaction=transaction, record=_demo_model_record(now))
        existing = self.model_registry_repository.get_aliases(
            transaction=transaction,
            tenant_id=DEFAULT_TENANT_ID,
            aliases=["default-completion", "gpt-governed"],
        )
        for alias in sorted({"default-completion", "gpt-governed"} - {row.alias for row in existing}):
            self.model_registry_repository.create_alias(
                transaction=transaction,
                record=_demo_alias_record(alias, now),
            )


def _ontology_registry(services: CoreServices) -> OntologyRegistry:
    return OntologyRegistry(
        services.ontology.entrypoint,
        services.ontology.insights,
        services.ontology.proposals,
        services.ontology.branches,
    )


def _demo_provider_record(now: str) -> ModelProviderRecord:
    return ModelProviderRecord(
        provider_id="local-fake-provider",
        tenant_scope=DEFAULT_TENANT_ID,
        provider_type="local-fake",
        profile_name="local-fake",
        region="us-east-1",
        secret_ref=_LOCAL_FAKE_CREDENTIAL_REF,
        retention_policy="zero_retention",
        training_policy="no_train",
        status="active",
        created_at=now,
    )


def _demo_model_record(now: str) -> ModelRecord:
    return ModelRecord(
        model_id="local-fake-model",
        tenant_scope=DEFAULT_TENANT_ID,
        provider_id="local-fake-provider",
        provider_model_id="local-fake-echo",
        revision="2026-06-25",
        lifecycle="stable",
        capabilities_json={"streaming": True, "native_tools": False},
        context_limit=8192,
        output_limit=1024,
        pricing_json={"input_per_1k": 0.002, "output_per_1k": 0.006, "currency": "USD"},
        allowed_classifications=("public", "internal"),
        created_at=now,
    )


def _demo_alias_record(alias: str, now: str) -> ModelAliasRecord:
    return ModelAliasRecord(
        alias=alias,
        tenant_scope=DEFAULT_TENANT_ID,
        environment="prod",
        model_id="local-fake-model",
        version="2026-06-25",
        status="enabled",
        eval_run_id=None,
        effective_at=now,
        retired_at=None,
    )
