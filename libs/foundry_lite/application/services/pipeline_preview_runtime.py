"""Port-backed runtime helpers for non-committing multimodal pipeline previews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Protocol

from foundry_lite.application.ports.source_management_repository import (
    SourceManagementRepository,
)
from foundry_lite.application.services.media.read_access import require_media_version_clearance
from foundry_lite.application.services.media.security_envelopes import (
    validated_media_source_envelope,
)
from foundry_lite.application.services.pipeline_media_reference import required_source_media_reference
from foundry_lite.application.services.pipeline_preview_media_execution import (
    exact_processor_identity,
    invoke_preview_processor,
    processor_descriptor,
)
from foundry_lite.application.services.pipeline_preview_media_payloads import _derivative_payload
from foundry_lite.application.services.pipeline_preview_port_types import (
    DatasetRow,
    EmbeddingModelAdapter,
    GovernedSemanticModelPort,
    MediaItemVersionRecord,
    MediaProcessorDescriptor,
    MediaProcessorRegistry,
    MediaRepository,
    MediaSetRecord,
    MediaSetSelectionRecord,
    MediaStorageAdapter,
    ProcessorSpec,
    TransactionManager,
)
from foundry_lite.application.services.pipeline_semantic_interpretation import (
    interpret_semantic_items,
    semantic_interpretation_spec,
)
from foundry_lite.application.services.pipeline_semantic_row_cache import (
    SemanticCacheContext,
    SemanticRowCacheSession,
)
from foundry_lite.application.services.pipeline_structured_source_contracts import (
    is_committed_stream_run,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed

JsonObject = dict[str, object]


class PipelinePreviewDatasetRegistry(Protocol):
    """Small read boundary needed by the preview executor."""

    def get_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetRow: ...

    def preview_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 100,
        version: str = "latest",
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[JsonObject]: ...


class PipelinePreviewRuntime:
    """Read committed inputs and invoke exact processors without persisting outputs."""

    def __init__(
        self,
        *,
        engine: TransactionManager,
        dataset_registry: PipelinePreviewDatasetRegistry,
        source_management_repository: SourceManagementRepository,
        media_repository: MediaRepository,
        media_storage: MediaStorageAdapter,
        media_processor_registry: MediaProcessorRegistry | None,
        embedding_model_adapter: EmbeddingModelAdapter,
        model_gateway: GovernedSemanticModelPort,
        semantic_cache: SemanticRowCacheSession,
        ctx: RequestContext,
        pipeline_id: str,
        branch_id: str,
        resource_security_policy_fingerprint: str,
        sensitive_fields: frozenset[str] = frozenset(),
    ) -> None:
        self._engine = engine
        self._dataset_registry = dataset_registry
        self._source_management_repository = source_management_repository
        self._media_repository = media_repository
        self._media_storage = media_storage
        self._processor_registry = media_processor_registry
        self._embedding_model = embedding_model_adapter
        self._model_gateway = model_gateway
        self._semantic_cache = semantic_cache
        self._ctx = ctx
        self._pipeline_id = pipeline_id
        self._branch_id = branch_id
        self._resource_security_policy_fingerprint = resource_security_policy_fingerprint
        self._sensitive_fields = sensitive_fields

    def dataset_rows(self, dataset_ref: str, *, limit: int) -> list[JsonObject]:
        dataset = self._dataset_registry.get_dataset(dataset_ref, ctx=self._ctx)
        rows = self._dataset_registry.preview_dataset(dataset_ref, ctx=self._ctx, limit=limit)
        envelope = _dataset_security_envelope(dataset, self._ctx)
        return [{**dict(row), "securityEnvelope": envelope} for row in rows]

    def stream_rows(self, source_ref: str, *, limit: int) -> list[JsonObject]:
        with self._engine.begin() as conn:
            sync = self._source_management_repository.sync_by_name(
                transaction=conn,
                tenant_id=self._ctx.tenant_id,
                sync_name=source_ref,
            )
        if sync is None or sync["capability"].lower() != "streaming" or not sync["target_dataset_ref"]:
            raise ValidationFailed(
                "stream preview requires a managed streaming sync",
                details={"sourceRef": source_ref},
            )
        run = next(
            (
                item
                for item in self._source_management_repository.list_sync_runs(
                    tenant_id=self._ctx.tenant_id, sync_name=source_ref
                )
                if is_committed_stream_run(item)
            ),
            None,
        )
        if run is None:
            raise ValidationFailed("stream preview has no committed checkpoint", details={"sourceRef": source_ref})
        dataset_ref = str(sync["target_dataset_ref"])
        dataset = self._dataset_registry.get_dataset(dataset_ref, ctx=self._ctx)
        rows = self._dataset_registry.preview_dataset(
            dataset_ref,
            ctx=self._ctx,
            limit=limit,
            version=str(run["dataset_version_id"]),
        )
        envelope = _dataset_security_envelope(dataset, self._ctx)
        checkpoint = dict(run["checkpoint_end"])
        return [{**dict(row), "securityEnvelope": envelope, "streamCheckpoint": checkpoint} for row in rows]

    def media_selection(
        self,
        media_set_ref: str,
        *,
        selection: Mapping[str, object],
        item_limit: int,
        total_byte_limit: int,
    ) -> list[JsonObject]:
        media_set = self._media_set(media_set_ref)
        version_ids, path_prefix = _selection_coordinates(selection, item_limit)
        with self._engine.begin() as conn:
            selected = self._media_repository.select_media_set_versions(
                transaction=conn,
                tenant_id=self._ctx.tenant_id,
                media_set_id=media_set.media_set_id,
                media_item_version_ids=version_ids,
                logical_path_prefix=path_prefix,
                limit=item_limit,
            )
        _require_exact_selection(version_ids, selected, media_set_ref)
        versions = [item.version for item in selected]
        _require_total_bytes(versions, total_byte_limit)
        for version in versions:
            require_media_version_clearance(self._ctx, version)
        envelopes = [
            validated_media_source_envelope(media_set, item.version, tenant_id=self._ctx.tenant_id) for item in selected
        ]
        return [_media_item_payload(item, media_set_ref, envelopes[index]) for index, item in enumerate(selected)]

    def process_media(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        processor_id: str,
        parameters: Mapping[str, object],
    ) -> list[JsonObject]:
        spec, descriptor = self._processor_spec(processor_id, parameters)
        return [self._process_media_item(item, spec, descriptor) for item in items]

    def embed_content(
        self,
        units: Sequence[Mapping[str, object]],
        *,
        model_ref: str,
    ) -> list[JsonObject]:
        _require_model_ref(self._embedding_model, model_ref)
        texts = [str(unit.get("text") or "") for unit in units]
        vectors = self._embedding_model.embed(texts)
        return [
            {**dict(unit), "embedding": list(vector), "embeddingModelVersion": self._embedding_model.model_version}
            for unit, vector in zip(units, vectors, strict=True)
        ]

    def interpret_semantics(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        config: Mapping[str, object],
        node_id: str,
        descriptor_id: str,
        spec_version: str,
        max_concurrency: int,
        request_timeout_seconds: int,
    ) -> list[JsonObject]:
        spec = semantic_interpretation_spec(config)
        return interpret_semantic_items(
            items,
            spec=spec,
            gateway=self._model_gateway,
            ctx=self._ctx,
            include_trial_evidence=True,
            sensitive_fields=self._sensitive_fields,
            cache=self._semantic_cache,
            cache_context=SemanticCacheContext(
                pipeline_id=self._pipeline_id,
                scope_kind="branch",
                scope_id=self._branch_id,
                node_id=node_id,
                descriptor_id=descriptor_id,
                spec_version=spec_version,
                cache_generation=spec.cache_generation,
                resource_security_policy_fingerprint=self._resource_security_policy_fingerprint,
            ),
            max_concurrency=max_concurrency,
            request_timeout_seconds=request_timeout_seconds,
        )

    def _committed_version(self, version_id: str) -> MediaItemVersionRecord:
        with self._engine.begin() as conn:
            version = self._media_repository.media_item_version_by_id(
                transaction=conn,
                tenant_id=self._ctx.tenant_id,
                media_item_version_id=version_id,
            )
        if version is None:
            raise NotFound("media version not found", details={"media_item_version_id": version_id})
        if version.status != "COMMITTED":
            raise ConflictDetected(
                "media preview requires a committed media version",
                details={"media_item_version_id": version_id, "status": version.status},
            )
        return version

    def _media_set(self, media_set_ref: str) -> MediaSetRecord:
        namespace, name = _media_set_ref_parts(media_set_ref)
        with self._engine.begin() as conn:
            media_set = self._media_repository.media_set_by_ref(
                transaction=conn,
                tenant_id=self._ctx.tenant_id,
                namespace=namespace,
                name=name,
            )
        if media_set is None:
            raise NotFound("media set not found", details={"mediaSetRef": media_set_ref})
        return media_set

    def _processor_spec(
        self,
        processor_id: str,
        parameters: Mapping[str, object],
    ) -> tuple[ProcessorSpec, MediaProcessorDescriptor]:
        registry = self._processor_registry
        if registry is None:
            raise ValidationFailed("media processor registry is unavailable")
        processor, version = exact_processor_identity(processor_id)
        descriptor = processor_descriptor(registry, processor, version)
        spec = ProcessorSpec(
            processor=processor,
            processor_version=version,
            model=descriptor.model.name,
            model_version=descriptor.model.version,
            parameters=dict(parameters),
        )
        return spec, descriptor

    def _process_media_item(
        self,
        item: Mapping[str, object],
        spec: ProcessorSpec,
        descriptor: MediaProcessorDescriptor,
    ) -> JsonObject:
        version_id = str(item.get("mediaItemVersionId") or "")
        version = _version_with_source_security(self._committed_version(version_id), item)
        registry = self._processor_registry
        assert registry is not None
        processor = registry.resolve(spec, input_format=version.format)
        result = invoke_preview_processor(processor, self._media_storage, self._ctx, version, spec)
        reference = required_source_media_reference(item)
        return _derivative_payload(result, version, descriptor, reference)


def _version_with_source_security(
    version: MediaItemVersionRecord,
    item: Mapping[str, object],
) -> MediaItemVersionRecord:
    envelope = item.get("securityEnvelope")
    if not isinstance(envelope, Mapping):
        raise ValidationFailed(
            "media preview source security envelope is missing",
            details={"mediaItemVersionId": version.media_item_version_id},
        )
    return replace(
        version,
        security_envelope={str(key): value for key, value in envelope.items()},
        has_legal_hold=bool(envelope.get("hasLegalHold", version.has_legal_hold)),
    )


def _require_total_bytes(versions: Sequence[MediaItemVersionRecord], byte_limit: int) -> None:
    total = sum(version.byte_size for version in versions)
    if total > byte_limit:
        raise ValidationFailed(
            "media preview exceeds the byte budget",
            details={"totalBytes": total, "byteLimit": byte_limit},
        )


def _require_model_ref(adapter: EmbeddingModelAdapter, model_ref: str) -> None:
    if not adapter.is_available:
        raise ValidationFailed("embedding model is unavailable for preview")
    if model_ref != adapter.model_version:
        raise ValidationFailed(
            "embedding model version does not match the pinned runtime model",
            details={"modelRef": model_ref, "runtimeModelVersion": adapter.model_version},
        )


def _media_item_payload(
    selection: MediaSetSelectionRecord,
    media_set_ref: str,
    security_envelope: Mapping[str, object],
) -> JsonObject:
    version = selection.version
    return {
        "mediaSetRef": media_set_ref,
        "mediaSetId": selection.media_set_id,
        "mediaItemVersionId": version.media_item_version_id,
        "mediaItemId": version.media_item_id,
        "logicalPath": selection.logical_path,
        "contentHash": version.content_hash,
        "byteSize": version.byte_size,
        "format": version.format,
        "mimeType": version.sniffed_mime_type,
        "sourceLocator": {
            "mediaSetRef": media_set_ref,
            "logicalPath": selection.logical_path,
            **dict(version.source_ref or {}),
        },
        "securityEnvelope": dict(security_envelope),
    }


def _dataset_security_envelope(
    dataset: DatasetRow,
    ctx: RequestContext,
) -> JsonObject:
    return {
        "tenantId": ctx.tenant_id,
        "resourceType": "dataset",
        "resourceId": dataset["id"],
        "classification": dataset["classification"] or "public",
    }


def _selection_coordinates(
    selection: Mapping[str, object],
    item_limit: int,
) -> tuple[list[str] | None, str | None]:
    mode = selection.get("mode", "current_heads")
    path_prefix = _selection_path_prefix(selection.get("logicalPathPrefix"))
    if mode == "current_heads":
        return None, path_prefix
    if mode != "version_ids":
        raise ValidationFailed("media selection mode is unsupported", details={"mode": mode})
    return _selection_version_ids(selection.get("versionIds"), item_limit), None


def _selection_path_prefix(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("media selection logicalPathPrefix must be non-empty text")
    return value.strip()


def _selection_version_ids(value: object, item_limit: int) -> list[str]:
    version_ids = value
    if not isinstance(version_ids, list) or not version_ids:
        raise ValidationFailed("media version selection requires versionIds")
    if not all(isinstance(value, str) and value.strip() for value in version_ids):
        raise ValidationFailed("media version selection requires non-empty string versionIds")
    return list(dict.fromkeys(str(value).strip() for value in version_ids))[:item_limit]


def _require_exact_selection(
    expected_ids: Sequence[str] | None,
    selected: Sequence[MediaSetSelectionRecord],
    media_set_ref: str,
) -> None:
    if expected_ids is None:
        return
    actual = {item.version.media_item_version_id for item in selected}
    missing = [version_id for version_id in expected_ids if version_id not in actual]
    if missing:
        raise NotFound(
            "media selection contains versions outside the committed Media Set",
            details={"mediaSetRef": media_set_ref, "missingMediaItemVersionIds": missing},
        )


def _media_set_ref_parts(media_set_ref: str) -> tuple[str, str]:
    if "." not in media_set_ref:
        raise ValidationFailed("media set reference must be namespace.name", details={"mediaSetRef": media_set_ref})
    namespace, name = media_set_ref.split(".", 1)
    if not namespace or not name:
        raise ValidationFailed("media set reference must be namespace.name", details={"mediaSetRef": media_set_ref})
    return namespace, name
