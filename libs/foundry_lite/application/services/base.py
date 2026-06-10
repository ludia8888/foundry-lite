from __future__ import annotations

from pathlib import Path
from typing import Any

from foundry_lite.application.ports import (
    ComputeAdapter,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    ObjectIndexRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    RuntimeRepository,
)
from foundry_lite.application.ports.action_repository import ActionRepository
from foundry_lite.application.ports.dataset_quality_repository import DatasetQualityRepository
from foundry_lite.application.ports.materialization_repository import MaterializationRepository
from foundry_lite.application.ports.ontology_repository import OntologyRepository
from foundry_lite.application.ports.transform_repository import TransformRepository
from foundry_lite.security.policy import PolicyService


class CoreServiceMixin:
    """Dependency contract a service mixin can rely on from its FoundryLiteCore facade host.

    The attributes declared here mirror :class:`CoreDependencies` (see
    ``application/dependencies.py``). The mixin itself does not store the
    handles -- ``FoundryLiteCore.__init__`` wires them after the composition
    root builds a :class:`CoreDependencies` instance.

    A service mixin may freely access ``self.<attr>`` for any name declared
    here, plus any method defined on another service mixin in the facade's
    MRO. ``scripts/quality/check_service_mixin_dependencies.py`` enforces
    that no service mixin invents an attribute outside this contract.

    The intent is to keep the mixin tree honest: dependency growth happens
    in :class:`CoreDependencies` (the single composition-root container)
    and is mirrored here, never invented inside individual service mixins.
    """

    root: Path
    storage_root: Path
    compute_adapter: ComputeAdapter
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    object_index_repository: ObjectIndexRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    runtime_repository: RuntimeRepository
    dataset_storage: DatasetStorageAdapter
    engine: Any
    policy: PolicyService
    action_repository: ActionRepository
    ontology_repository: OntologyRepository
    transform_repository: TransformRepository
    materialization_repository: MaterializationRepository
    dataset_quality_repository: DatasetQualityRepository

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
