"""Governed Action media/attachment upload and reference resolution."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePath
from typing import BinaryIO

from foundry_lite.application.action_types import ActionMediaUploadResult
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.action_file_scanner import ActionFileScanner, ActionFileScanResult
from foundry_lite.application.ports.media_repository import (
    MediaItemVersionRecord,
    MediaSetRecord,
    MediaTransactionRecord,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.action_media_contracts import (
    ActionDefinitionV3,
    ActionOntologyLookup,
    ActionParameterV3,
    resolve_action_media_upload_contract,
)
from foundry_lite.application.services.action_media_parameters import (
    resolve_action_media_parameters,
)
from foundry_lite.application.services.action_media_scanning import (
    ActionParameterUpload,
    prepare_action_upload,
    scan_evidence,
    source_fingerprint,
)
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, ValidationFailed


class ActionMediaService(CoreService):
    """Resolve untrusted media IDs and provisionally upload files for Action forms."""

    required_dependencies = (
        "engine",
        "policy",
        "action_file_scanner",
        "media_repository",
    )
    required_collaborators = (
        "media_transaction_service",
        "media_upload_service",
        "ontology_lookup_service",
        "runtime_service",
    )
    media_transaction_service: MediaTransactionService
    media_upload_service: MediaUploadService
    ontology_lookup_service: ActionOntologyLookup
    runtime_service: ActionRuntimeBoundary
    action_file_scanner: ActionFileScanner

    def upload_parameter(
        self,
        action_api_name: str,
        parameter_name: str,
        *,
        object_type: str,
        object_id: str,
        file_name: str,
        source: BinaryIO,
        supplied_mime_type: str,
        idempotency_key: str,
        format: str | None,
        ctx: RequestContext,
    ) -> ActionMediaUploadResult:
        upload = ActionParameterUpload(
            action_api_name,
            parameter_name,
            object_type,
            object_id,
            file_name,
            source,
            supplied_mime_type,
            idempotency_key,
            format,
            ctx,
        )
        return self._upload(upload)

    def _upload(self, upload: ActionParameterUpload) -> ActionMediaUploadResult:
        if not upload.idempotency_key.strip():
            raise ValidationFailed("Idempotency-Key is required")
        contract, parameter, kind, media_set = self._upload_contract(
            upload.ctx, upload.action_api_name, upload.parameter_name, upload.object_type, upload.object_id
        )
        prepared = prepare_action_upload(
            upload,
            parameter,
            self.action_file_scanner,
            self.engine,
            self.runtime_service,
        )
        transaction_id, is_replay = self._stage_and_commit(
            upload.ctx,
            contract,
            parameter,
            media_set,
            upload.file_name,
            upload.source,
            upload.supplied_mime_type,
            upload.format,
            upload.idempotency_key,
            prepared.request_fingerprint,
            prepared.scan,
        )
        version = self._committed_upload_version(upload.ctx, transaction_id)
        reference = self._resolved_upload_reference(upload.ctx, contract, upload.parameter_name, version)
        return _upload_result(
            upload.action_api_name,
            upload.parameter_name,
            kind,
            reference,
            transaction_id,
            is_replay,
            prepared.scan,
        )

    def _upload_contract(
        self,
        ctx: RequestContext,
        action_api_name: str,
        parameter_name: str,
        object_type: str,
        object_id: str,
    ) -> tuple[ActionDefinitionV3, ActionParameterV3, str, MediaSetRecord]:
        resolved = resolve_action_media_upload_contract(
            self.engine,
            self.policy,
            self.runtime_service,
            self.ontology_lookup_service,
            self.media_repository,
            ctx,
            action_api_name,
            parameter_name,
            object_type,
            object_id,
        )
        return resolved.contract, resolved.parameter, resolved.reference_kind, resolved.media_set

    def _stage_and_commit(
        self,
        ctx: RequestContext,
        contract: ActionDefinitionV3,
        parameter: ActionParameterV3,
        media_set: MediaSetRecord,
        file_name: str,
        source: BinaryIO,
        supplied_mime_type: str,
        format: str | None,
        idempotency_key: str,
        request_fingerprint: str,
        scan: ActionFileScanResult,
    ) -> tuple[str, bool]:
        transaction_id = self.media_transaction_service.open(
            ctx,
            media_set_id=media_set.media_set_id,
            idempotency_key=_transaction_key(contract, parameter, idempotency_key),
            request_fingerprint=request_fingerprint,
        )
        transaction, versions = self._upload_state(ctx, transaction_id, request_fingerprint)
        if transaction.status == "COMMITTED":
            return transaction_id, True
        if not versions:
            self._upload_source(
                ctx, contract, parameter, media_set, transaction_id, file_name, source, supplied_mime_type, format, scan
            )
        self.media_transaction_service.commit(
            ctx,
            media_transaction_id=transaction_id,
            before_commit=lambda conn: self._mark_provisional(conn, ctx, transaction_id),
        )
        return transaction_id, bool(versions)

    def _upload_state(
        self, ctx: RequestContext, transaction_id: str, request_fingerprint: str
    ) -> tuple[MediaTransactionRecord, list[MediaItemVersionRecord]]:
        with self.engine.begin() as transaction:
            row = self.media_repository.transaction_by_id(
                transaction=transaction, tenant_id=ctx.tenant_id, media_transaction_id=transaction_id
            )
            if row is None:
                raise InvariantViolation("Action media transaction disappeared")
            if row.request_fingerprint != request_fingerprint:
                raise ConflictDetected("Action media upload idempotency key was reused for another file")
            versions = self.media_repository.fetch_transaction_versions(
                transaction=transaction, tenant_id=ctx.tenant_id, media_transaction_id=transaction_id
            )
        if len(versions) > 1:
            raise InvariantViolation("Action media upload transaction contains more than one file")
        return row, versions

    def _upload_source(
        self,
        ctx: RequestContext,
        contract: ActionDefinitionV3,
        parameter: ActionParameterV3,
        media_set: MediaSetRecord,
        transaction_id: str,
        file_name: str,
        source: BinaryIO,
        supplied_mime_type: str,
        format: str | None,
        scan: ActionFileScanResult,
    ) -> None:
        logical_path = _logical_path(contract, parameter, file_name, source)
        upload = self.media_upload_service.initiate(
            ctx, media_set_id=media_set.media_set_id, logical_path=logical_path, supplied_mime_type=supplied_mime_type
        )
        self.media_upload_service.complete(
            ctx,
            inputs=MediaUploadInput(
                media_set_id=media_set.media_set_id,
                media_transaction_id=transaction_id,
                logical_path=logical_path,
                supplied_mime_type=supplied_mime_type,
                schema_type=media_set.schema_type,
                format=_upload_format(media_set, file_name, format),
                security_envelope=_upload_envelope(ctx, media_set, _parameter_media_kind(parameter), scan),
                probe_metadata={
                    "actionApiName": contract.api_name,
                    "parameter": parameter.api_name,
                    "malwareScan": scan_evidence(scan),
                },
            ),
            upload=upload,
            source=source,
        )

    def _mark_provisional(self, transaction: TransactionContext, ctx: RequestContext, transaction_id: str) -> None:
        versions = self.media_repository.fetch_transaction_versions(
            transaction=transaction, tenant_id=ctx.tenant_id, media_transaction_id=transaction_id
        )
        self.media_repository.mark_versions_for_retention(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            media_item_version_ids=[version.media_item_version_id for version in versions],
            marked_at=_now(),
        )

    def _committed_upload_version(self, ctx: RequestContext, transaction_id: str) -> MediaItemVersionRecord:
        with self.engine.begin() as transaction:
            versions = self.media_repository.fetch_committed_versions(
                transaction=transaction, tenant_id=ctx.tenant_id, media_transaction_id=transaction_id
            )
        if len(versions) != 1:
            raise InvariantViolation("Action media upload did not produce exactly one committed version")
        return versions[0]

    def _resolved_upload_reference(
        self,
        ctx: RequestContext,
        contract: ActionDefinitionV3,
        parameter_name: str,
        version: MediaItemVersionRecord,
    ) -> dict[str, object]:
        with self.engine.begin() as transaction:
            values = resolve_action_media_parameters(
                transaction,
                ctx,
                self.policy,
                self.media_repository,
                contract,
                {parameter_name: version.media_item_version_id},
            )
        reference = values.get(parameter_name)
        if not isinstance(reference, Mapping):
            raise InvariantViolation("Action media upload reference was not canonicalized")
        return dict(reference)


def _upload_result(
    action_api_name: str,
    parameter_name: str,
    kind: str,
    reference: Mapping[str, object],
    transaction_id: str,
    is_replay: bool,
    scan: ActionFileScanResult,
) -> ActionMediaUploadResult:
    return {
        "actionApiName": action_api_name,
        "parameter": parameter_name,
        "referenceKind": kind,
        "reference": reference,
        "mediaTransactionId": transaction_id,
        "uploadState": "provisional",
        "isRetentionMarked": True,
        "isIdempotentReplay": is_replay,
        "malwareScan": scan_evidence(scan),
    }


def _transaction_key(contract: ActionDefinitionV3, parameter: ActionParameterV3, key: str) -> str:
    return f"action-media:{contract.api_name}:{parameter.api_name}:{key}"


def _logical_path(contract: ActionDefinitionV3, parameter: ActionParameterV3, file_name: str, source: BinaryIO) -> str:
    safe_name = PurePath(file_name.replace("\\", "/")).name or "upload.bin"
    content_hash, _ = source_fingerprint(source)
    return f"actions/{contract.api_name}/{parameter.api_name}/{content_hash[:24]}-{safe_name}"


def _upload_format(media_set: MediaSetRecord, file_name: str, value: str | None) -> str:
    candidate = (value or PurePath(file_name).suffix.lstrip(".") or media_set.primary_format).lower()
    if candidate not in media_set.allowed_input_formats:
        raise ValidationFailed(
            "Action media upload format is not allowed by its Media Set",
            details={"format": candidate, "allowedFormats": list(media_set.allowed_input_formats)},
        )
    return candidate


def _upload_envelope(
    ctx: RequestContext, media_set: MediaSetRecord, reference_kind: str, scan: ActionFileScanResult
) -> dict[str, object]:
    payload: dict[str, object] = {
        "tenantId": ctx.tenant_id,
        "classification": media_set.classification,
        "hasLegalHold": False,
        "actionParameterKind": reference_kind,
        "actionUploadActor": ctx.actor_user_id,
        "malwareScan": scan_evidence(scan),
    }
    if media_set.retention_policy_id is not None:
        payload["retentionPolicyId"] = media_set.retention_policy_id
    return payload


def _parameter_media_kind(parameter: ActionParameterV3) -> str:
    if parameter.data_type in {"media", "attachment"}:
        return parameter.data_type
    return str(parameter.metadata["itemType"])
