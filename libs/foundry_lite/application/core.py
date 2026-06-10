from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import insert

from foundry_lite.application.core_services import (
    ActionServiceMixin,
    DatasetServiceMixin,
    DemoServiceMixin,
    MaterializationServiceMixin,
    ObjectServiceMixin,
    OntologyServiceMixin,
    RuntimeServiceMixin,
    TransformServiceMixin,
)
from foundry_lite.application.dependencies import CoreDependencies
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
from foundry_lite.infrastructure import schema as db
from foundry_lite.observability.tracing import trace_public_methods

__all__ = [
    "CommitResult",
    "FoundryLiteCore",
    "StagedFileStats",
    "_dataset_ref_parts",
    "_json_ready",
    "_normalize_duckdb_type",
    "_required_row",
]


@trace_public_methods
class FoundryLiteCore(
    DemoServiceMixin,
    MaterializationServiceMixin,
    ActionServiceMixin,
    ObjectServiceMixin,
    OntologyServiceMixin,
    TransformServiceMixin,
    DatasetServiceMixin,
    RuntimeServiceMixin,
):
    """Facade for the MVP closed loop.

    Public use cases stay on this facade for compatibility. The implementation is split into
    focused service mixins so Dataset, Transform, Ontology, Object, Action, Materialization,
    runtime event, and demo orchestration responsibilities evolve independently.
    """

    def __init__(
        self,
        *,
        db_url: str | None = None,
        storage_root: str | Path | None = None,
        dependencies: CoreDependencies | None = None,
    ) -> None:
        if dependencies is not None and (db_url is not None or storage_root is not None):
            raise ValueError("pass either dependencies or db_url/storage_root, not both")
        if dependencies is None:
            from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

            dependencies = create_local_core_dependencies(db_url=db_url, storage_root=storage_root)
        self.root = dependencies.root
        self.storage_root = dependencies.storage_root
        self.engine = dependencies.engine
        self.policy = dependencies.policy
        self.dataset_storage = dependencies.dataset_storage
        if dependencies.initialize_schema is not None:
            dependencies.initialize_schema(self.engine)
        self.bootstrap()

    def reset(self, *, confirm_dev: bool = False) -> None:
        if not confirm_dev:
            raise ValidationFailed("reset is destructive and requires confirm_dev=True")
        db.metadata.drop_all(self.engine)
        db.create_database(self.engine)
        if self.storage_root.exists():
            shutil.rmtree(self.storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.bootstrap()

    def bootstrap(self) -> None:
        with self.engine.begin() as conn:
            if self._select_by_id(conn, db.tenants, DEFAULT_TENANT_ID) is None:
                conn.execute(
                    insert(db.tenants).values(
                        id=DEFAULT_TENANT_ID,
                        name="Demo Tenant",
                        created_at=_now(),
                    )
                )
            if self._select_by_id(conn, db.users, DEFAULT_ACTOR_USER_ID) is None:
                conn.execute(
                    insert(db.users).values(
                        id=DEFAULT_ACTOR_USER_ID,
                        tenant_id=DEFAULT_TENANT_ID,
                        email="demo@foundry-lite.local",
                        roles=["admin", "data_engineer", "ops_manager", "finance"],
                        created_at=_now(),
                    )
                )
