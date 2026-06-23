from __future__ import annotations

from typing import BinaryIO

from foundry_lite.application.ports.content_index import ContentSearchHit, HybridContentQuery
from foundry_lite.application.ports.media_derivative_repository import MediaDerivativeRecord
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRecord
from foundry_lite.application.ports.media_repository import MediaReference, MediaSetRecord
from foundry_lite.application.services.media.catalog import MediaSetSpec
from foundry_lite.application.services.media.indexing import IndexingOutcome
from foundry_lite.application.services.media.processing import ProcessingOutcome
from foundry_lite.application.services.media.references import ResolvedMediaReference
from foundry_lite.application.services.media.transactions import MediaCommitResult
from foundry_lite.application.services.media.uploads import MediaUploadInput, StagedUpload
from foundry_lite.application.services.media_service import MediaServices
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class MediaWorkspace:
    """Media/Content Plane bounded context: catalog, transactional uploads, references.

    Parallel to ``DatasetWorkspace``. Bytes are immutable per version; a transaction
    commit is the metadata-pointer point that makes a version the serving truth.
    """

    def __init__(self, media: MediaServices) -> None:
        self._media = media

    def create_media_set(
        self,
        ctx: RequestContext,
        *,
        namespace: str,
        name: str,
        schema_type: str,
        primary_format: str,
        allowed_input_formats: tuple[str, ...],
        classification: str,
        transaction_policy: str = "transactional",
        storage_profile: str = "local",
        processing_profile: str = "local",
        retention_policy_id: str | None = None,
    ) -> MediaSetRecord:
        spec = MediaSetSpec(
            namespace=namespace,
            name=name,
            schema_type=schema_type,
            primary_format=primary_format,
            allowed_input_formats=allowed_input_formats,
            classification=classification,
            transaction_policy=transaction_policy,
            storage_profile=storage_profile,
            processing_profile=processing_profile,
            retention_policy_id=retention_policy_id,
        )
        return self._media.catalog.create_media_set(ctx, spec)

    def get_media_set(self, ctx: RequestContext, *, media_set_id: str) -> MediaSetRecord:
        return self._media.catalog.get_media_set(ctx, media_set_id=media_set_id)

    def open_transaction(
        self, ctx: RequestContext, *, media_set_id: str, idempotency_key: str, mode: str = "APPEND"
    ) -> str:
        return self._media.transaction.open(ctx, media_set_id=media_set_id, idempotency_key=idempotency_key, mode=mode)

    def upload(
        self,
        ctx: RequestContext,
        *,
        media_set_id: str,
        media_transaction_id: str,
        logical_path: str,
        source: BinaryIO,
        supplied_mime_type: str,
        schema_type: str,
        format: str,
        security_envelope: dict[str, object],
        probe_metadata: dict[str, object] | None = None,
    ) -> StagedUpload:
        session = self._media.upload.initiate(
            ctx, media_set_id=media_set_id, logical_path=logical_path, supplied_mime_type=supplied_mime_type
        )
        inputs = MediaUploadInput(
            media_set_id=media_set_id,
            media_transaction_id=media_transaction_id,
            logical_path=logical_path,
            supplied_mime_type=supplied_mime_type,
            schema_type=schema_type,
            format=format,
            security_envelope=security_envelope,
            probe_metadata=probe_metadata,
        )
        return self._media.upload.complete(ctx, inputs=inputs, upload=session, source=source)

    def commit(self, ctx: RequestContext, *, media_transaction_id: str) -> MediaCommitResult:
        return self._media.transaction.commit(ctx, media_transaction_id=media_transaction_id)

    def abort(self, ctx: RequestContext, *, media_transaction_id: str, error: dict[str, object] | None = None) -> None:
        self._media.transaction.abort(ctx, media_transaction_id=media_transaction_id, error=error)

    def sweep_orphans(self, ctx: RequestContext, *, older_than: str) -> list[str]:
        return self._media.transaction.sweep_orphans(ctx, older_than=older_than)

    def resolve_reference(self, ctx: RequestContext, *, media_item_version_id: str) -> ResolvedMediaReference:
        return self._media.reference.resolve(ctx, media_item_version_id=media_item_version_id)

    def process(self, ctx: RequestContext, *, media_item_version_id: str, spec: ProcessorSpec) -> ProcessingOutcome:
        return self._media.processing.process(ctx, media_item_version_id=media_item_version_id, spec=spec)

    def resolve_derivative(self, ctx: RequestContext, *, media_derivative_id: str) -> MediaDerivativeRecord:
        return self._media.processing.resolve_derivative(ctx, media_derivative_id=media_derivative_id)

    def sweep_orphan_derivatives(self, ctx: RequestContext, *, older_than: str) -> list[str]:
        return self._media.processing.sweep_orphan_derivatives(ctx, older_than=older_than)

    def configure_content_generation(self, ctx: RequestContext, *, generation: str) -> None:
        self._media.indexing.configure(ctx, generation=generation)

    def index_derivative(self, ctx: RequestContext, *, media_derivative_id: str, generation: str) -> IndexingOutcome:
        return self._media.indexing.index_derivative(
            ctx, media_derivative_id=media_derivative_id, generation=generation
        )

    def rebuild_content_index(
        self, ctx: RequestContext, *, generation: str, source_media_item_version_ids: list[str]
    ) -> IndexingOutcome:
        return self._media.indexing.rebuild(
            ctx, generation=generation, source_media_item_version_ids=source_media_item_version_ids
        )

    def promote_content_generation(self, ctx: RequestContext, *, expected_active: str, generation: str) -> None:
        self._media.indexing.promote(ctx, expected_active=expected_active, generation=generation)

    def search_content(self, ctx: RequestContext, *, query: HybridContentQuery) -> list[ContentSearchHit]:
        return self._media.retrieval.search_content(ctx, query=query)

    def bind_reference(
        self,
        ctx: RequestContext,
        *,
        holder_type: str,
        holder_id: str,
        property_name: str,
        media_item_version_id: str,
        idempotency_key: str,
    ) -> MediaReferenceBindingRecord:
        return self._media.binding.bind(
            ctx,
            holder_type=holder_type,
            holder_id=holder_id,
            property_name=property_name,
            media_item_version_id=media_item_version_id,
            idempotency_key=idempotency_key,
        )

    def resolve_bound_reference(
        self,
        ctx: RequestContext,
        *,
        holder_type: str,
        holder_id: str,
        property_name: str,
        allowed_classifications: tuple[str, ...] | None = None,
    ) -> MediaReference | None:
        return self._media.binding.resolve(
            ctx,
            holder_type=holder_type,
            holder_id=holder_id,
            property_name=property_name,
            allowed_classifications=allowed_classifications,
        )
