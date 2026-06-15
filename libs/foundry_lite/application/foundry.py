from __future__ import annotations

import shutil

from foundry_lite.application.core_services import CoreServices
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.facades import (
    ActionGateway,
    DatasetWorkspace,
    MaterializationRunner,
    ObjectStore,
    OntologyRegistry,
    OperationsConsole,
    SupplyChainDemo,
    TransformPipeline,
)
from foundry_lite.application.primitives import (
    CommitResult,
    StagedFileStats,
    _dataset_ref_parts,
    _json_ready,
    _normalize_duckdb_type,
    _now,
    _required_row,
)
from foundry_lite.domain.context import DEFAULT_ACTOR_USER_ID, DEFAULT_TENANT_ID
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.observability.tracing import trace_public_methods

__all__ = ["CommitResult", "FoundryLite", "StagedFileStats", "_dataset_ref_parts"]
__all__ += ["_json_ready", "_normalize_duckdb_type", "_required_row"]


@trace_public_methods
class FoundryLite:
    """Platform root for the MVP closed loop.

    Exposes one facade per bounded context; injected services own each domain
    workflow. Lifecycle (bootstrap/reset) stays on this root.
    """

    def __init__(self, *, dependencies: CoreDependencies) -> None:
        # CoreDependencies is assembled by composition roots, not this facade.
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
        self.dataset_repository = dependencies.dataset_repository
        self.dataset_transaction_repository = dependencies.dataset_transaction_repository
        self.dataset_version_repository = dependencies.dataset_version_repository
        self.object_index_repository = dependencies.object_index_repository
        self.object_read_repository = dependencies.object_read_repository
        self.object_set_repository = dependencies.object_set_repository
        self.runtime_repository = dependencies.runtime_repository
        self.dataset_storage = dependencies.dataset_storage
        services = CoreServices.create(dependencies)
        self._services = services
        # One facade per bounded context.
        self.datasets = DatasetWorkspace(services.dataset)
        self.transforms = TransformPipeline(services.transform)
        self.ontology = OntologyRegistry(services.ontology)
        self.objects = ObjectStore(services.object_store)
        self.actions = ActionGateway(services.action)
        self.materialization = MaterializationRunner(services.materialization)
        self.operations = OperationsConsole(services.runtime, services.materialization)
        self.demo = SupplyChainDemo(services.demo, reset_fresh=lambda: self.reset(confirm_dev=True))
        self.metadata_repository.initialize_schema()
        self.bootstrap()

    def reset(self, *, confirm_dev: bool = False) -> None:
        if not confirm_dev:
            raise ValidationFailed("reset is destructive and requires confirm_dev=True")
        self.metadata_repository.reset_schema()
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
