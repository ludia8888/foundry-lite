from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.application.action_types import ActionApplyResponse
from foundry_lite.application.core_retry import retry_materialization_name, retry_materialization_result
from foundry_lite.application.core_services import CoreServices
from foundry_lite.application.demo_types import SupplyChainDemoResult
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.ports import (
    DatasetInspectionPayload,
    DatasetRow,
    DatasetVersionRow,
    LineageEdgeRow,
    ObjectIndexRebuildResult,
    ObjectLinkPayload,
    ObjectPayload,
    ObjectQueryResult,
    ObjectSetPayload,
    ObjectSetQueryResult,
    OntologyApplyResult,
    RestSourceConfig,
    RuntimeRetryResult,
    RuntimeRunDetail,
    RuntimeRunQueryResult,
    RuntimeRunSnapshot,
    StreamArchiveConfig,
    TabularRow,
    TransformCheck,
    TransformRetryResult,
    TransformRow,
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
from foundry_lite.domain.context import DEFAULT_ACTOR_USER_ID, DEFAULT_TENANT_ID, RequestContext
from foundry_lite.domain.errors import ValidationFailed
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
class FoundryLiteCore:
    """Facade for the MVP closed loop.

    Public use cases stay on this facade for compatibility. The implementation is
    delegated to constructor-injected application services so Dataset, Transform,
    Ontology, Object, Action, Materialization, runtime event, and demo orchestration
    responsibilities evolve independently without facade-level multiple inheritance.
    """

    def __init__(
        self,
        *,
        dependencies: CoreDependencies,
    ) -> None:
        # Sprint 02A §4.3: FoundryLiteCore accepts a fully-wired
        # CoreDependencies bag only. Convenience constructors that pull infrastructure into the
        # application layer live in apps/* composition roots and the
        # test conftest, never inside the facade itself.
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
        self._services = CoreServices.create(dependencies)
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

    def create_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
        description: str | None = None,
        owner_team: str | None = None,
        classification: str | None = None,
    ) -> DatasetRow:
        return self._services.dataset.registry.create_dataset(
            dataset_ref,
            ctx=ctx,
            primary_key=primary_key,
            storage_kind=storage_kind,
            description=description,
            owner_team=owner_team,
            classification=classification,
        )

    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
    ) -> DatasetRow:
        return self._services.dataset.registry.ensure_dataset(
            dataset_ref,
            ctx=ctx,
            primary_key=primary_key,
            storage_kind=storage_kind,
        )

    def find_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow | None:
        return self._services.dataset.registry.find_dataset(dataset_ref, ctx=ctx)

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow:
        return self._services.dataset.registry.get_dataset(dataset_ref, ctx=ctx)

    def list_dataset_versions(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[DatasetVersionRow]:
        return self._services.dataset.registry.list_dataset_versions(dataset_ref, ctx=ctx)

    def preview_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 100,
        version: str = "latest",
    ) -> list[TabularRow]:
        return self._services.dataset.registry.preview_dataset(dataset_ref, ctx=ctx, limit=limit, version=version)

    def inspect_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        version: str = "latest",
    ) -> DatasetInspectionPayload:
        return self._services.dataset.registry.inspect_dataset(dataset_ref, ctx=ctx, version=version)

    def upload_csv(
        self,
        dataset_ref: str,
        csv_path: str | Path,
        *,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
    ) -> CommitResult:
        return self._services.dataset.ingest.upload_csv(dataset_ref, csv_path, ctx=ctx, sync_name=sync_name)

    def sync_connector_snapshot(
        self,
        dataset_ref: str,
        *,
        connector_name: str,
        resource_name: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        cursor: Mapping[str, object] | None = None,
        rest: RestSourceConfig | None = None,
    ) -> CommitResult:
        return self._services.dataset.ingest.sync_connector_snapshot(
            dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            ctx=ctx,
            sync_name=sync_name,
            cursor=cursor,
            rest=rest,
        )

    def ingest_webhook_event(
        self,
        dataset_ref: str,
        *,
        connector_name: str,
        resource_name: str,
        payload: Mapping[str, object],
        raw_body: bytes,
        signature: str,
        secret: str,
        ctx: RequestContext | None = None,
    ) -> CommitResult:
        return self._services.dataset.ingest.ingest_webhook_event(
            dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            payload=payload,
            raw_body=raw_body,
            signature=signature,
            secret=secret,
            ctx=ctx,
        )

    def archive_stream_events(
        self,
        dataset_ref: str,
        *,
        stream: StreamArchiveConfig,
        ctx: RequestContext | None = None,
        after_offset: int | None = None,
        sync_name: str | None = None,
    ) -> CommitResult | None:
        return self._services.dataset.ingest.archive_stream_events(
            dataset_ref,
            stream=stream,
            ctx=ctx,
            after_offset=after_offset,
            sync_name=sync_name,
        )

    def register_transform(
        self,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
        language: str = "sql",
    ) -> TransformRow:
        return self._services.transform.register_transform(
            api_name,
            entrypoint=entrypoint,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            ctx=ctx,
            mode=mode,
            language=language,
        )

    def run_transform(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult:
        return self._services.transform.run_transform(api_name, ctx=ctx)

    def retry_transform_run(self, transform_run_id: str, *, ctx: RequestContext | None = None) -> TransformRetryResult:
        return self._services.transform.retry_transform_run(transform_run_id, ctx=ctx)

    def apply_ontology(
        self,
        yaml_path: str | Path,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyApplyResult:
        return self._services.ontology.apply_ontology(yaml_path, ctx=ctx)

    def index_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        return self._services.object_store.indexing.index_rebuild(object_type_api_name, ctx=ctx)

    def index_replay_run(
        self,
        index_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        return self._services.object_store.indexing.index_replay_run(index_run_id, ctx=ctx)

    def get_links(
        self,
        object_type_api_name: str,
        object_id: str,
        link_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[ObjectLinkPayload]:
        return self._services.object_store.links.get_links(object_type_api_name, object_id, link_type_api_name, ctx=ctx)

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> ObjectPayload:
        return self._services.object_store.query.get_object(
            object_type_api_name,
            object_id,
            ctx=ctx,
            include_explain=include_explain,
        )

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ObjectQueryResult:
        return self._services.object_store.query.query_objects(
            object_type_api_name,
            ctx=ctx,
            filter_ast=filter_ast,
            order_by=order_by,
            limit=limit,
            cursor=cursor,
        )

    def create_object_set(
        self,
        name: str,
        object_type_api_name: str,
        *,
        set_type: str,
        ctx: RequestContext | None = None,
        definition: Mapping[str, object] | None = None,
        object_ids: list[str] | None = None,
        filter_ast: Mapping[str, object] | None = None,
        visibility: str = "private",
        ttl_seconds: int | None = None,
    ) -> ObjectSetPayload:
        return self._services.object_store.sets.create_object_set(
            name,
            object_type_api_name,
            set_type=set_type,
            ctx=ctx,
            definition=definition,
            object_ids=object_ids,
            filter_ast=filter_ast,
            visibility=visibility,
            ttl_seconds=ttl_seconds,
        )

    def get_object_set(
        self,
        set_id: str,
        *,
        ctx: RequestContext | None = None,
        include_items: bool = True,
    ) -> ObjectSetPayload:
        return self._services.object_store.sets.get_object_set(set_id, ctx=ctx, include_items=include_items)

    def query_object_sets(
        self,
        *,
        ctx: RequestContext | None = None,
        object_type_api_name: str | None = None,
    ) -> ObjectSetQueryResult:
        return self._services.object_store.sets.query_object_sets(ctx=ctx, object_type_api_name=object_type_api_name)

    def cleanup_expired_object_sets(self, *, ctx: RequestContext | None = None) -> dict[str, int]:
        return self._services.object_store.sets.cleanup_expired_object_sets(ctx=ctx)

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
    ) -> ActionApplyResponse:
        return self._services.action.apply_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx,
            simulate_writeback_failure=simulate_writeback_failure,
        )

    def materialize(
        self,
        api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> CommitResult:
        return self._services.materialization.materialize(api_name, ctx=ctx)

    def lineage_for_resource(
        self,
        resource_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[LineageEdgeRow]:
        return self._services.runtime.lineage_for_resource(resource_id, ctx=ctx)

    def list_runs(self, *, ctx: RequestContext | None = None) -> RuntimeRunSnapshot:
        return self._services.runtime.list_runs(ctx=ctx)

    def query_runs(
        self,
        *,
        ctx: RequestContext | None = None,
        run_type: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> RuntimeRunQueryResult:
        return self._services.runtime.query_runs(
            ctx=ctx,
            run_type=run_type,
            status=status,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )

    def run_detail(self, run_type: str, run_id: str, *, ctx: RequestContext | None = None) -> RuntimeRunDetail:
        return self._services.runtime.run_detail(run_type, run_id, ctx=ctx)

    def retry_dead_letter_event(self, event_id: str, *, ctx: RequestContext | None = None) -> RuntimeRetryResult:
        ctx = ctx or RequestContext()
        plan = self._services.runtime.dead_letter_event_retry_plan(event_id, ctx=ctx)
        materialization_name = retry_materialization_name(plan)
        materialization_result = None
        if materialization_name is not None:
            commit = self._services.materialization.materialize(materialization_name, ctx=ctx)
            materialization_result = retry_materialization_result(materialization_name, commit)
        result = self._services.runtime.retry_dead_letter_event(event_id, ctx=ctx)
        if materialization_result is not None:
            result["materializationResult"] = materialization_result
        return result

    def run_supply_chain_demo(self, *, ctx: RequestContext | None = None, fresh: bool = False) -> SupplyChainDemoResult:
        if fresh:
            self.reset(confirm_dev=True)
        return self._services.demo.run_supply_chain_demo(ctx=ctx)

    def register_supply_chain_demo_transforms(self, ctx: RequestContext | None = None) -> None:
        self._services.demo.register_supply_chain_demo_transforms(ctx=ctx)

    def seed_supply_chain_demo_files(self) -> None:
        self._services.demo.seed_supply_chain_demo_files()
