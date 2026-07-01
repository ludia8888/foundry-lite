"""Application service helpers for demo protocols workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from foundry_lite.application.action_types import ActionApplyResponse
from foundry_lite.application.ports import (
    DatasetCheckConfig,
    DatasetRow,
    ObjectIndexRebuildResult,
    ObjectPayload,
    OntologyApplyResult,
    TransformRow,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.domain.context import RequestContext


class DemoActionApplier(Protocol):
    def apply_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        simulate_writeback_failure: bool = False,
        simulate_writeback_retryable: bool = False,
        simulate_writeback_outcome_unknown: bool = False,
        simulate_writeback_compensation_required: bool = False,
    ) -> ActionApplyResponse: ...


class DemoDatasetIngest(Protocol):
    def upload_csv(
        self,
        dataset_ref: str,
        csv_path: str | Path,
        *,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
    ) -> CommitResult: ...


class DemoDatasetRegistry(Protocol):
    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
    ) -> DatasetRow: ...


class DemoMaterializer(Protocol):
    def materialize(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult: ...


class DemoObjectIndexer(Protocol):
    def index_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult: ...


class DemoObjectQuery(Protocol):
    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> ObjectPayload: ...


class DemoOntologyApplier(Protocol):
    def apply_ontology(
        self,
        yaml_path: str | Path,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyApplyResult: ...


class DemoTransformRunner(Protocol):
    def register_transform(
        self,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[DatasetCheckConfig] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
        language: str = "sql",
    ) -> TransformRow: ...

    def run_transform(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult: ...
