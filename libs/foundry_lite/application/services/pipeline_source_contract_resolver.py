"""Read-only resolution of Pipeline Builder sources to committed repository truth."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    DatasetQualityRepository,
    DatasetRepository,
    DatasetRow,
    DatasetVersionRepository,
    DatasetVersionRow,
    SourceManagementRepository,
    SourceSyncRunRow,
    TransactionContext,
)
from foundry_lite.application.ports.media_repository import (
    MediaRepository,
    MediaSetSelectionRecord,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_graph_contracts import (
    JsonObject,
    PipelineV2Node,
)
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph
from foundry_lite.application.services.pipeline_source_contract_builders import (
    build_dataset_source_contract,
    build_media_source_contract,
)
from foundry_lite.application.services.pipeline_source_contracts import (
    PipelineSourceContract,
    PipelineSourceContractResolutionFailed,
    PipelineSourceResolution,
)
from foundry_lite.application.services.pipeline_structured_source_contracts import (
    build_geospatial_source_contract,
    build_stream_source_contract,
    is_committed_stream_run,
)
from foundry_lite.domain.context import RequestContext

_MAX_MEDIA_SELECTION_ITEMS = 10_000
_COMMITTED_DATASET_VERSION_STATUSES = frozenset({"ACTIVE", "COMMITTED"})


class PipelineSourceContractResolver:
    """Resolve logical graph references through tenant-scoped repository ports."""

    def __init__(
        self,
        *,
        dataset_repository: DatasetRepository,
        dataset_version_repository: DatasetVersionRepository,
        dataset_quality_repository: DatasetQualityRepository,
        media_repository: MediaRepository,
        source_management_repository: SourceManagementRepository,
    ) -> None:
        self._dataset_repository = dataset_repository
        self._dataset_version_repository = dataset_version_repository
        self._dataset_quality_repository = dataset_quality_repository
        self._media_repository = media_repository
        self._source_management_repository = source_management_repository

    def resolve(
        self,
        *,
        transaction: TransactionContext,
        graph: Mapping[str, object],
        ctx: RequestContext,
    ) -> PipelineSourceResolution:
        canonical = normalize_pipeline_graph(graph)
        contracts: list[PipelineSourceContract] = []
        warnings: list[Mapping[str, object]] = []
        for node in canonical["nodes"]:
            resolved = self._resolve_node(transaction, node, ctx)
            if resolved is None:
                continue
            contract, node_warnings = resolved
            contracts.append(contract)
            warnings.extend(node_warnings)
        return PipelineSourceResolution(tuple(contracts), tuple(warnings))

    def _resolve_node(
        self,
        transaction: TransactionContext,
        node: PipelineV2Node,
        ctx: RequestContext,
    ) -> tuple[PipelineSourceContract, list[JsonObject]] | None:
        if node["descriptorId"] == "source.dataset":
            return self._resolve_dataset(transaction, node, ctx)
        if node["descriptorId"] == "source.media_set":
            return self._resolve_media_set(transaction, node, ctx)
        if node["descriptorId"] == "source.stream":
            return self._resolve_stream(transaction, node, ctx)
        if node["descriptorId"] == "source.geospatial":
            return self._resolve_geospatial(transaction, node, ctx)
        return None

    def _resolve_stream(
        self,
        transaction: TransactionContext,
        node: PipelineV2Node,
        ctx: RequestContext,
    ) -> tuple[PipelineSourceContract, list[JsonObject]]:
        source_ref = _required_config_text(node, "sourceRef")
        sync = self._source_management_repository.sync_by_name(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            sync_name=source_ref,
        )
        if sync is None:
            raise _source_failure(node, source_ref, "source_not_found")
        if sync["capability"].lower() != "streaming" or not sync["target_dataset_ref"]:
            raise _source_failure(node, source_ref, "source_is_not_checkpointed_stream")
        run = self._latest_committed_stream_run(ctx, node, source_ref)
        dataset_node = _backing_dataset_node(node, str(sync["target_dataset_ref"]), str(run["dataset_version_id"]))
        dataset_contract, warnings = self._resolve_dataset(transaction, dataset_node, ctx)
        contract = build_stream_source_contract(
            node=node,
            source_ref=source_ref,
            sync=sync,
            run=run,
            dataset_contract=dataset_contract,
        )
        return contract, warnings

    def _latest_committed_stream_run(
        self,
        ctx: RequestContext,
        node: PipelineV2Node,
        source_ref: str,
    ) -> SourceSyncRunRow:
        runs = self._source_management_repository.list_sync_runs(
            tenant_id=ctx.tenant_id,
            sync_name=source_ref,
        )
        run = next((item for item in runs if is_committed_stream_run(item)), None)
        if run is None:
            raise _source_failure(node, source_ref, "source_has_no_committed_checkpoint")
        return run

    def _resolve_geospatial(
        self,
        transaction: TransactionContext,
        node: PipelineV2Node,
        ctx: RequestContext,
    ) -> tuple[PipelineSourceContract, list[JsonObject]]:
        resource_ref = _required_config_text(node, "resourceRef")
        version_id = _optional_config_text(node, "datasetVersionId")
        dataset_node = _backing_dataset_node(node, resource_ref, version_id)
        dataset_contract, warnings = self._resolve_dataset(transaction, dataset_node, ctx)
        contract = build_geospatial_source_contract(
            node=node,
            resource_ref=resource_ref,
            dataset_contract=dataset_contract,
        )
        return contract, warnings

    def _resolve_dataset(
        self,
        transaction: TransactionContext,
        node: PipelineV2Node,
        ctx: RequestContext,
    ) -> tuple[PipelineSourceContract, list[JsonObject]]:
        dataset_ref = _required_config_text(node, "datasetRef")
        namespace, name = _split_resource_ref(dataset_ref, node)
        dataset = self._dataset_repository.active_dataset_by_ref(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            namespace=namespace,
            name=name,
        )
        if dataset is None:
            raise _source_failure(node, dataset_ref, "source_not_found")
        version = self._dataset_version(transaction, node, dataset, dataset_ref, ctx)
        schema = self._dataset_quality_repository.schema_by_version(
            transaction=transaction,
            dataset_id=dataset["id"],
            schema_version=version["schema_version"],
        )
        if schema is None:
            raise _source_failure(node, dataset_ref, "source_schema_not_found")
        contract = build_dataset_source_contract(
            node=node,
            dataset_ref=dataset_ref,
            dataset=dataset,
            version=version,
            schema=schema,
            ctx=ctx,
        )
        return contract, _schema_drift_warnings(node, contract)

    def _dataset_version(
        self,
        transaction: TransactionContext,
        node: PipelineV2Node,
        dataset: DatasetRow,
        dataset_ref: str,
        ctx: RequestContext,
    ) -> DatasetVersionRow:
        requested_id = _optional_config_text(node, "datasetVersionId")
        if requested_id is None:
            version = self._dataset_version_repository.latest_version_by_dataset_id(
                transaction=transaction,
                dataset_id=dataset["id"],
            )
        else:
            version = self._dataset_version_repository.version_by_dataset_id_and_id(
                transaction=transaction,
                dataset_id=dataset["id"],
                version_id=requested_id,
            )
        if version is None:
            reason = "source_has_no_committed_version" if requested_id is None else "source_version_not_committed"
            raise _source_failure(node, dataset_ref, reason, version_id=requested_id)
        if (
            version["tenant_id"] != ctx.tenant_id
            or version["status"].upper() not in _COMMITTED_DATASET_VERSION_STATUSES
        ):
            raise _source_failure(node, dataset_ref, "source_version_not_committed", version_id=version["id"])
        return version

    def _resolve_media_set(
        self,
        transaction: TransactionContext,
        node: PipelineV2Node,
        ctx: RequestContext,
    ) -> tuple[PipelineSourceContract, list[JsonObject]]:
        media_set_ref = _required_config_text(node, "mediaSetRef")
        namespace, name = _split_resource_ref(media_set_ref, node)
        media_set = self._media_repository.media_set_by_ref(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            namespace=namespace,
            name=name,
        )
        if media_set is None:
            raise _source_failure(node, media_set_ref, "source_not_found")
        requested_ids, path_prefix = _media_selection(node)
        selected = self._media_repository.select_media_set_versions(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            media_set_id=media_set.media_set_id,
            media_item_version_ids=requested_ids or None,
            logical_path_prefix=path_prefix,
            limit=_MAX_MEDIA_SELECTION_ITEMS + 1,
        )
        _require_complete_media_selection(node, media_set_ref, requested_ids, selected)
        contract = build_media_source_contract(
            node=node,
            media_set_ref=media_set_ref,
            media_set=media_set,
            selected=selected,
            ctx=ctx,
        )
        return contract, _schema_drift_warnings(node, contract)


def _media_selection(node: PipelineV2Node) -> tuple[list[str], str | None]:
    selection = node["config"].get("selection")
    nested = selection if isinstance(selection, Mapping) else {}
    raw_ids = nested.get("versionIds") or node["config"].get("mediaItemVersionIds")
    prefix = nested.get("logicalPathPrefix") or node["config"].get("logicalPathPrefix")
    return _text_list(raw_ids, node, "mediaItemVersionIds"), _optional_text(prefix)


def _backing_dataset_node(
    node: PipelineV2Node,
    dataset_ref: str,
    version_id: str | None,
) -> PipelineV2Node:
    config: JsonObject = {"datasetRef": dataset_ref}
    if version_id is not None:
        config["datasetVersionId"] = version_id
    return {
        "id": node["id"],
        "kind": "source",
        "descriptorId": "source.dataset",
        "specVersion": 1,
        "config": config,
    }


def _require_complete_media_selection(
    node: PipelineV2Node,
    media_set_ref: str,
    requested_ids: Sequence[str],
    selected: Sequence[MediaSetSelectionRecord],
) -> None:
    if len(selected) > _MAX_MEDIA_SELECTION_ITEMS:
        raise _source_failure(node, media_set_ref, "source_selection_too_large")
    if not selected:
        raise _source_failure(node, media_set_ref, "source_has_no_committed_version")
    selected_ids = {item.version.media_item_version_id for item in selected}
    missing = sorted(set(requested_ids) - selected_ids)
    if missing:
        raise _source_failure(
            node,
            media_set_ref,
            "source_version_not_committed",
            missing_version_ids=missing,
        )


def _schema_drift_warnings(
    node: PipelineV2Node,
    contract: PipelineSourceContract,
) -> list[JsonObject]:
    configured = node["config"].get("schema")
    if not isinstance(configured, list):
        return []
    actual = _schema_columns(contract.schema_contract)
    if _json_hash({"schema": configured}) == _json_hash({"schema": actual}):
        return []
    return [
        {
            "code": "source_schema_drift",
            "nodeId": node["id"],
            "resourceRef": contract.resource_ref,
            "graphSchemaIgnored": True,
            "resolvedSchemaHash": contract.schema_hash,
            "resolvedVersionIds": [pin.version_id for pin in contract.version_pins],
        }
    ]


def _schema_columns(schema: Mapping[str, object]) -> list[JsonObject]:
    for key in ("columns", "fields"):
        value = schema.get(key)
        if isinstance(value, (list, tuple)):
            return [{str(k): item for k, item in row.items()} for row in value if isinstance(row, Mapping)]
    return []


def _required_config_text(node: PipelineV2Node, field: str) -> str:
    value = _optional_config_text(node, field)
    if value is None:
        raise _source_failure(node, "", "source_ref_required", field=field)
    return value


def _optional_config_text(node: PipelineV2Node, field: str) -> str | None:
    return _optional_text(node["config"].get(field))


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _split_resource_ref(resource_ref: str, node: PipelineV2Node) -> tuple[str, str]:
    if "." not in resource_ref:
        raise _source_failure(node, resource_ref, "source_ref_invalid")
    namespace, name = resource_ref.rsplit(".", 1)
    if not namespace or not name:
        raise _source_failure(node, resource_ref, "source_ref_invalid")
    return namespace, name


def _text_list(value: object, node: PipelineV2Node, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise _source_failure(node, "", "source_selection_invalid", field=field)
    result = [_optional_text(item) for item in value]
    if any(item is None for item in result):
        raise _source_failure(node, "", "source_selection_invalid", field=field)
    return sorted({str(item) for item in result})


def _source_failure(
    node: PipelineV2Node,
    resource_ref: str,
    reason: str,
    **details: object,
) -> PipelineSourceContractResolutionFailed:
    return PipelineSourceContractResolutionFailed(
        "pipeline committed source could not be resolved",
        details={
            "reason": reason,
            "nodeId": node["id"],
            "descriptorId": node["descriptorId"],
            "resourceRef": resource_ref,
            **details,
        },
    )
