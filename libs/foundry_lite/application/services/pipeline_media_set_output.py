"""Serving Media Set output commit protocol for production Pipeline Graph v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import PipelineExecutionLeaseFence
from foundry_lite.application.services.pipeline_media_output_port_types import (
    MediaDerivativeRecord,
    MediaDerivativeRepository,
    MediaItemVersionRecord,
    MediaRepository,
    MediaSetRecord,
    MediaStorageAdapter,
    MediaTransactionRecord,
    TransactionManager,
)
from foundry_lite.application.services.pipeline_media_output_service_types import (
    MediaCatalogService,
    MediaSetSpec,
    MediaTransactionService,
    MediaUploadService,
)
from foundry_lite.application.services.pipeline_media_set_output_commit_protocol import (
    PipelineMediaSetOutputCommitProtocol,
)
from foundry_lite.application.services.pipeline_media_set_output_contracts import (
    MediaOutputContract,
    MediaOutputEntry,
    MediaOutputTransactionAttempt,
    PipelineMediaDerivativeBytesUnavailable,
    PipelineMediaOutputSourceCorrupt,
    PipelineMediaSetOutputContractMismatch,
)
from foundry_lite.application.services.pipeline_media_set_output_security import (
    require_derivative_security_inheritance,
    require_item_security,
)
from foundry_lite.application.services.pipeline_media_set_output_staging import (
    media_upload_input,
)
from foundry_lite.application.services.pipeline_media_set_output_support import (
    derivative_bytes,
    derivative_logical_path,
    format_for_mime,
    item_text,
    media_output_entry,
    media_set_ref_parts,
    media_transaction_idempotency_key,
    output_contract,
    request_fingerprint,
    require_derivative_coordinates,
    require_item_coordinate,
    require_media_transaction_coordinates,
    require_target_contract,
    required_text,
    schema_for_mime,
    versions_by_entry,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    RuntimeInputs,
    single_input_artifact,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class PipelineMediaSetOutputCommitter:
    """Copy exact committed media bytes through stage, validate, and commit."""

    def __init__(
        self,
        *,
        engine: TransactionManager,
        media_repository: MediaRepository,
        media_derivative_repository: MediaDerivativeRepository,
        media_storage: MediaStorageAdapter,
        media_catalog: MediaCatalogService,
        media_transactions: MediaTransactionService,
        media_uploads: MediaUploadService,
        ctx: RequestContext,
        run_id: str,
        execution_lease_guard: PipelineExecutionLeaseFence,
    ) -> None:
        self._engine = engine
        self._media_repository = media_repository
        self._media_derivative_repository = media_derivative_repository
        self._media_storage = media_storage
        self._media_catalog = media_catalog
        self._media_transactions = media_transactions
        self._media_uploads = media_uploads
        self._ctx = ctx
        self._run_id = run_id
        self._execution_lease_guard = execution_lease_guard

    def commit(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        target_ref = required_text(node.config, "mediaSetRef", node.node_id)
        entries = self._entries(source)
        contract = output_contract(entries)
        target = self._target_media_set(target_ref, contract)
        fingerprint = request_fingerprint(self._run_id, node, source, target_ref, entries)
        transaction_attempt = self._open_transaction(node, target, fingerprint)
        protocol = PipelineMediaSetOutputCommitProtocol(
            media_storage=self._media_storage,
            media_transactions=self._media_transactions,
            ctx=self._ctx,
            require_active_lease=self._execution_lease_guard.require_active,
            transaction_loader=self._transaction,
            versions_loader=self._transaction_versions,
            stage_missing_entries=self._stage_missing_entries,
        )
        return protocol.commit(
            node=node,
            source=source,
            inputs=inputs,
            target_ref=target_ref,
            target=target,
            transaction_attempt=transaction_attempt,
            request_fingerprint=fingerprint,
            entries=entries,
        )

    def _entries(
        self,
        source: PipelineV2RuntimeArtifact,
    ) -> tuple[MediaOutputEntry, ...]:
        if source.status != "COMMITTED" or not source.items:
            raise PipelineMediaOutputSourceCorrupt(
                "pipeline Media Set output requires a non-empty committed input artifact",
                details={"nodeId": source.node_id, "status": source.status},
            )
        if source.artifact_kind == "media_set_selection":
            return tuple(self._selection_entry(item) for item in source.items)
        if source.artifact_kind == "media_derivative_set":
            return tuple(self._derivative_entry(item) for item in source.items)
        raise PipelineMediaSetOutputContractMismatch(
            "pipeline Media Set output received an unsupported artifact kind",
            details={"artifactKind": source.artifact_kind},
        )

    def _selection_entry(self, item: Mapping[str, object]) -> MediaOutputEntry:
        version_id = item_text(item, "mediaItemVersionId")
        version, media_set, logical_path = self._source_version_contract(version_id)
        require_item_coordinate(item, "contentHash", version.content_hash, version_id)
        require_item_security(item, version.security_envelope, version_id)
        self._require_durable_source(
            version.blob_key,
            version.content_hash,
            version.byte_size,
            version_id,
        )
        return media_output_entry(
            source_version=version,
            media_set=media_set,
            logical_path=logical_path,
            blob_key=version.blob_key,
            content_hash=version.content_hash,
            byte_size=version.byte_size,
            mime_type=version.sniffed_mime_type,
            schema_type=version.schema_type,
            format=version.format,
        )

    def _derivative_entry(self, item: Mapping[str, object]) -> MediaOutputEntry:
        derivative_id = item_text(item, "mediaDerivativeId")
        derivative = self._committed_derivative(derivative_id)
        version, _media_set, logical_path = self._source_version_contract(derivative.source_media_item_version_id)
        require_derivative_security_inheritance(version, derivative)
        require_derivative_coordinates(item, derivative)
        blob_key, content_hash, byte_size, mime_type = derivative_bytes(derivative)
        self._require_durable_source(blob_key, content_hash, byte_size, derivative_id)
        output_format = format_for_mime(mime_type)
        return media_output_entry(
            source_version=version,
            media_set=None,
            logical_path=derivative_logical_path(logical_path, derivative, output_format),
            blob_key=blob_key,
            content_hash=content_hash,
            byte_size=byte_size,
            mime_type=mime_type,
            schema_type=schema_for_mime(mime_type),
            format=output_format,
            derivative=derivative,
        )

    def _source_version_contract(
        self,
        version_id: str,
    ) -> tuple[MediaItemVersionRecord, MediaSetRecord, str]:
        with self._engine.begin() as transaction:
            version = self._media_repository.media_item_version_by_id(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                media_item_version_id=version_id,
            )
            if version is None or version.status != "COMMITTED":
                raise PipelineMediaOutputSourceCorrupt(
                    "pipeline Media Set output source version is not committed",
                    details={"mediaItemVersionId": version_id},
                )
            item = self._media_repository.media_item_by_id(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                media_item_id=version.media_item_id,
            )
            media_sets = self._media_repository.get_media_sets(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                ids=[item.media_set_id] if item is not None else [],
            )
        if item is None or not media_sets:
            raise PipelineMediaOutputSourceCorrupt(
                "pipeline Media Set output source catalog coordinates are missing",
                details={"mediaItemVersionId": version_id},
            )
        return version, media_sets[0], item.logical_path

    def _committed_derivative(self, derivative_id: str) -> MediaDerivativeRecord:
        with self._engine.begin() as transaction:
            derivative = self._media_derivative_repository.derivative_by_id(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                media_derivative_id=derivative_id,
            )
        if derivative is None or derivative.status != "COMMITTED":
            raise PipelineMediaDerivativeBytesUnavailable(
                "pipeline Media Set output derivative is not committed",
                details={"mediaDerivativeId": derivative_id},
            )
        return derivative

    def _require_durable_source(
        self,
        blob_key: str,
        content_hash: str,
        byte_size: int,
        source_id: str,
    ) -> None:
        stat = self._media_storage.stat(blob_key)
        if stat.is_present and stat.content_hash == content_hash and stat.byte_size == byte_size:
            return
        raise PipelineMediaOutputSourceCorrupt(
            "pipeline Media Set output source bytes do not match committed metadata",
            details={"sourceId": source_id, "reason": "durable_byte_contract_mismatch"},
        )

    def _target_media_set(
        self,
        target_ref: str,
        contract: MediaOutputContract,
    ) -> MediaSetRecord:
        namespace, name = media_set_ref_parts(target_ref)
        with self._engine.begin() as transaction:
            target = self._media_repository.media_set_by_ref(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                namespace=namespace,
                name=name,
            )
        if target is None:
            target = self._media_catalog.create_media_set(
                self._ctx,
                MediaSetSpec(
                    namespace=namespace,
                    name=name,
                    schema_type=contract.schema_type,
                    primary_format=contract.primary_format,
                    allowed_input_formats=contract.allowed_formats,
                    classification=contract.classification,
                    storage_profile=self._media_storage.profile_name,
                    processing_profile="pipeline_graph_v2",
                ),
            )
        require_target_contract(target, contract, target_ref)
        return target

    def _open_transaction(
        self,
        node: PipelineV2RuntimeNode,
        target: MediaSetRecord,
        request_fingerprint: str,
    ) -> MediaOutputTransactionAttempt:
        generation = 1
        while True:
            transaction_id = self._media_transactions.open(
                self._ctx,
                media_set_id=target.media_set_id,
                idempotency_key=media_transaction_idempotency_key(self._run_id, node.node_id, generation),
                mode="SNAPSHOT",
                request_fingerprint=request_fingerprint,
            )
            transaction = self._transaction(transaction_id)
            require_media_transaction_coordinates(transaction, target, request_fingerprint)
            if transaction.status in {"OPEN", "COMMITTED"}:
                return MediaOutputTransactionAttempt(transaction_id, generation)
            if transaction.status != "ABORTED":
                raise ConflictDetected(
                    "pipeline Media Set output transaction has an unsupported state",
                    details={"mediaTransactionId": transaction_id, "status": transaction.status},
                )
            generation += 1

    def _stage_missing_entries(
        self,
        node: PipelineV2RuntimeNode,
        source: PipelineV2RuntimeArtifact,
        target: MediaSetRecord,
        transaction_attempt: MediaOutputTransactionAttempt,
        request_fingerprint: str,
        entries: Sequence[MediaOutputEntry],
    ) -> None:
        existing = versions_by_entry(self._transaction_versions(transaction_attempt.media_transaction_id))
        for entry in entries:
            if entry.entry_fingerprint not in existing:
                self._stage_entry(
                    node,
                    source,
                    target,
                    transaction_attempt,
                    request_fingerprint,
                    entry,
                )

    def _stage_entry(
        self,
        node: PipelineV2RuntimeNode,
        source: PipelineV2RuntimeArtifact,
        target: MediaSetRecord,
        transaction_attempt: MediaOutputTransactionAttempt,
        request_fingerprint: str,
        entry: MediaOutputEntry,
    ) -> None:
        upload = self._media_uploads.initiate(
            self._ctx,
            media_set_id=target.media_set_id,
            logical_path=entry.logical_path,
            supplied_mime_type=entry.mime_type,
        )
        with self._media_storage.open_stream(entry.blob_key) as body:
            self._media_uploads.complete(
                self._ctx,
                inputs=media_upload_input(
                    run_id=self._run_id,
                    node=node,
                    source=source,
                    target=target,
                    transaction_attempt=transaction_attempt,
                    request_fingerprint=request_fingerprint,
                    entry=entry,
                ),
                upload=upload,
                source=body,
            )

    def _transaction(self, transaction_id: str) -> MediaTransactionRecord:
        with self._engine.begin() as transaction:
            found = self._media_repository.transaction_by_id(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                media_transaction_id=transaction_id,
            )
        if found is None:
            raise ConflictDetected(
                "pipeline Media Set output transaction disappeared",
                details={"mediaTransactionId": transaction_id},
            )
        return found

    def _transaction_versions(self, transaction_id: str) -> list[MediaItemVersionRecord]:
        with self._engine.begin() as transaction:
            return self._media_repository.fetch_transaction_versions(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                media_transaction_id=transaction_id,
            )
