"""Application service helpers for osdk application sdk workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from foundry_lite.application.ports import (
    OsdkApplicationRepository,
    OsdkApplicationResourceRow,
    OsdkLanguage,
    OsdkReleaseArtifactRow,
    OsdkSdkCompatibilityWindowRow,
    OsdkSdkReleaseChannelRow,
    OsdkSdkVersionBundle,
    OsdkSdkVersionRow,
    RuntimeJsonObject,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.osdk_download_token_signer import (
    OsdkDownloadTokenClaims,
    OsdkDownloadTokenSigner,
)
from foundry_lite.application.ports.osdk_release_artifact_store import OsdkReleaseArtifactStore
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.osdk_application_artifacts import (
    _artifact_record,
    _compatibility_window_record,
    _download_token_record,
    _expires_in_seconds,
    _hash_download_token,
    _is_expired,
    _release_channel_record,
    _release_decision,
    _release_manifest,
    _sdk_bundle,
    _sdk_record,
    artifact_payload,
)
from foundry_lite.application.services.osdk_application_idempotency import OsdkApplicationIdempotencyService
from foundry_lite.application.services.osdk_application_package_builder import build_release_artifact
from foundry_lite.application.services.osdk_application_records import (
    _channel,
    _language,
    _positive_ttl,
    _require_idempotency_key,
)
from foundry_lite.application.services.osdk_application_scope import OsdkApplicationScopeService
from foundry_lite.application.services.osdk_application_types import (
    _DEFAULT_DOWNLOAD_TOKEN_TTL_SECONDS,
    _ArtifactDraft,
    _ReleaseDecision,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied, ValidationFailed
from foundry_lite.security.policy import PolicyService


class _AuditCallable(Protocol):
    def __call__(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        row: Mapping[str, object],
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
    ) -> None: ...


class OsdkApplicationSdkService(CoreService):
    required_dependencies = (
        "engine",
        "policy",
        "osdk_application_repository",
        "osdk_download_token_signer",
        "osdk_release_artifact_store",
    )
    required_collaborators = ("osdk_application_scope_service", "osdk_application_idempotency_service")

    engine: TransactionManager
    osdk_application_repository: OsdkApplicationRepository
    osdk_download_token_signer: OsdkDownloadTokenSigner
    osdk_release_artifact_store: OsdkReleaseArtifactStore
    policy: PolicyService
    osdk_application_scope_service: OsdkApplicationScopeService
    osdk_application_idempotency_service: OsdkApplicationIdempotencyService

    def create_sdk_version(
        self,
        app_id: str,
        *,
        ctx: RequestContext | None = None,
        language: str,
        package_name: str | None = None,
        requested_bump: str | None = None,
        idempotency_key: str,
    ) -> OsdkSdkVersionBundle:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:manage")
        _require_idempotency_key(idempotency_key)
        language_value = _language(language)
        with self.engine.begin() as conn:
            app = self.osdk_application_scope_service._require_application(conn, ctx, app_id)
            resources = self.osdk_application_scope_service._resources(conn, ctx, app_id)
            latest = self._latest_released(conn, ctx, app_id, language_value)
            decision = _release_decision(latest, resources, requested_bump)
            artifact = self._build_release_artifact(app, resources, decision, language_value, package_name)
            row = self._insert_sdk_version(conn, ctx, app, decision, language_value, artifact, idempotency_key)
            artifacts = (
                self._persist_artifact(conn, ctx, row, app, artifact) if decision.release_status == "released" else []
            )
            self._audit_sdk_version(conn, ctx, row, decision)
            return _sdk_bundle(row, artifacts)

    def promote_sdk_version(
        self,
        app_id: str,
        version_id: str,
        *,
        ctx: RequestContext | None = None,
        channel: str,
        idempotency_key: str,
    ) -> OsdkSdkReleaseChannelRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:manage")
        _require_idempotency_key(idempotency_key)
        request = {"appId": app_id, "versionId": version_id, "channel": channel}
        with self.engine.begin() as conn:
            response = self.osdk_application_idempotency_service._idempotent_response(
                conn,
                ctx,
                "osdk.sdk_version.promote",
                idempotency_key,
                request,
                lambda: self._promote_sdk_version_json(conn, ctx, app_id, version_id, channel),
            )
            return cast(OsdkSdkReleaseChannelRow, response)

    def list_release_channels(
        self, app_id: str, *, ctx: RequestContext | None = None, language: str | None = None
    ) -> list[OsdkSdkReleaseChannelRow]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            self.osdk_application_scope_service._require_application(conn, ctx, app_id)
            language_value = _language(language) if language is not None else None
            return self.osdk_application_repository.release_channels_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id, language=language_value
            )

    def create_compatibility_window(
        self,
        app_id: str,
        *,
        ctx: RequestContext | None = None,
        from_version_id: str,
        to_version_id: str,
        supported_until: str | None = None,
        idempotency_key: str,
    ) -> OsdkSdkCompatibilityWindowRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:manage")
        _require_idempotency_key(idempotency_key)
        request = {
            "appId": app_id,
            "fromVersionId": from_version_id,
            "toVersionId": to_version_id,
            "supportedUntil": supported_until,
        }
        with self.engine.begin() as conn:
            response = self.osdk_application_idempotency_service._idempotent_response(
                conn,
                ctx,
                "osdk.sdk_version.compatibility_window.create",
                idempotency_key,
                request,
                lambda: self._create_compatibility_window_json(
                    conn,
                    ctx,
                    app_id,
                    from_version_id,
                    to_version_id,
                    supported_until,
                ),
            )
            return cast(OsdkSdkCompatibilityWindowRow, response)

    def list_compatibility_windows(
        self, app_id: str, *, ctx: RequestContext | None = None
    ) -> list[OsdkSdkCompatibilityWindowRow]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            self.osdk_application_scope_service._require_application(conn, ctx, app_id)
            return self.osdk_application_repository.compatibility_windows_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )

    def install_metadata(self, app_id: str, *, ctx: RequestContext | None = None) -> RuntimeJsonObject:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            self.osdk_application_scope_service._require_application(conn, ctx, app_id)
            versions = self.osdk_application_repository.sdk_versions_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )
            channels = self.osdk_application_repository.release_channels_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )
            windows = self.osdk_application_repository.compatibility_windows_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )
        return {"sdkVersions": versions, "channels": channels, "compatibilityWindows": windows}

    def create_artifact_download_token(
        self,
        app_id: str,
        version_id: str,
        artifact_kind: str,
        *,
        ctx: RequestContext | None = None,
        ttl_seconds: int = _DEFAULT_DOWNLOAD_TOKEN_TTL_SECONDS,
        idempotency_key: str,
    ) -> RuntimeJsonObject:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        _require_idempotency_key(idempotency_key)
        request = {"appId": app_id, "versionId": version_id, "artifactKind": artifact_kind, "ttlSeconds": ttl_seconds}
        with self.engine.begin() as conn:
            return self.osdk_application_idempotency_service._idempotent_download_response(
                conn,
                ctx,
                idempotency_key,
                request,
                lambda request_hash: self._create_download_token_json(
                    conn,
                    ctx,
                    app_id,
                    version_id,
                    artifact_kind,
                    ttl_seconds,
                    request_hash,
                ),
            )

    def read_release_artifact_by_download_token(
        self, token: str, *, ctx: RequestContext | None = None
    ) -> RuntimeJsonObject:
        ctx = ctx or RequestContext()
        now = _now()
        with self.engine.begin() as conn:
            token_row = self.osdk_application_repository.artifact_download_token_by_hash(
                transaction=conn, tenant_id=ctx.tenant_id, token_hash=_hash_download_token(token)
            )
            if token_row is None or token_row["status"] != "active" or _is_expired(token_row["expires_at"], now):
                raise PermissionDenied("OSDK release artifact download token is invalid")
            artifact = self.osdk_application_repository.release_artifact_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, artifact_id=token_row["artifact_id"]
            )
            self.osdk_application_repository.mark_artifact_download_token_used(
                transaction=conn, tenant_id=ctx.tenant_id, token_id=token_row["id"], used_at=now
            )
        if artifact is None:
            raise NotFound("OSDK release artifact not found")
        return artifact_payload(self.osdk_release_artifact_store, artifact)

    def list_sdk_versions(self, app_id: str, *, ctx: RequestContext | None = None) -> list[OsdkSdkVersionRow]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            self.osdk_application_scope_service._require_application(conn, ctx, app_id)
            return self.osdk_application_repository.sdk_versions_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )

    def get_sdk_version(
        self, app_id: str, version_id: str, *, ctx: RequestContext | None = None
    ) -> OsdkSdkVersionBundle:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            row = self._require_sdk_version(conn, ctx, app_id, version_id)
            artifact = self.osdk_application_repository.release_artifact(
                transaction=conn, tenant_id=ctx.tenant_id, sdk_version_id=version_id, artifact_kind=row["language"]
            )
            return _sdk_bundle(row, [artifact] if artifact is not None else [])

    def read_release_artifact(
        self, app_id: str, version_id: str, artifact_kind: str, *, ctx: RequestContext | None = None
    ) -> RuntimeJsonObject:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            self._require_sdk_version(conn, ctx, app_id, version_id)
            row = self.osdk_application_repository.release_artifact(
                transaction=conn, tenant_id=ctx.tenant_id, sdk_version_id=version_id, artifact_kind=artifact_kind
            )
        if row is None:
            raise NotFound("OSDK release artifact not found")
        return artifact_payload(self.osdk_release_artifact_store, row)

    def _promote_sdk_version_json(
        self, conn: TransactionContext, ctx: RequestContext, app_id: str, version_id: str, channel: str
    ) -> RuntimeJsonObject:
        row = self._require_sdk_version(conn, ctx, app_id, version_id)
        if row["release_status"] != "released":
            raise ValidationFailed("only released OSDK SDK versions can be promoted")
        channel_row = self.osdk_application_repository.upsert_release_channel(
            transaction=conn, record=_release_channel_record(ctx, row, _channel(channel))
        )
        self.osdk_application_scope_service._audit(conn, ctx, "osdk.sdk_version.channel_promoted", channel_row)
        return cast(RuntimeJsonObject, channel_row)

    def _create_compatibility_window_json(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        app_id: str,
        from_version_id: str,
        to_version_id: str,
        supported_until: str | None,
    ) -> RuntimeJsonObject:
        old_row = self._require_sdk_version(conn, ctx, app_id, from_version_id)
        new_row = self._require_sdk_version(conn, ctx, app_id, to_version_id)
        if old_row["language"] != new_row["language"]:
            raise ValidationFailed("OSDK compatibility window requires matching SDK languages")
        window = self.osdk_application_repository.insert_compatibility_window(
            transaction=conn, record=_compatibility_window_record(ctx, old_row, new_row, supported_until)
        )
        self.osdk_application_scope_service._audit(conn, ctx, "osdk.sdk_version.compatibility_window_created", window)
        return cast(RuntimeJsonObject, window)

    def _create_download_token_json(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        app_id: str,
        version_id: str,
        artifact_kind: str,
        ttl_seconds: int,
        request_hash: str,
    ) -> RuntimeJsonObject:
        self._require_sdk_version(conn, ctx, app_id, version_id)
        artifact = self._require_release_artifact(conn, ctx, version_id, artifact_kind)
        expires_at = _expires_in_seconds(_now(), _positive_ttl(ttl_seconds))
        raw_token = self.osdk_download_token_signer.sign_token(
            OsdkDownloadTokenClaims(ctx.tenant_id, artifact["id"], expires_at, request_hash)
        )
        row = self.osdk_application_repository.insert_artifact_download_token_or_get_existing(
            transaction=conn,
            record=_download_token_record(ctx, artifact, raw_token, expires_at),
        )
        self.osdk_application_scope_service._audit(conn, ctx, "osdk.release_artifact.download_token_created", artifact)
        return {"downloadToken": raw_token, "artifactId": artifact["id"], "expiresAt": row["expires_at"]}

    def _require_release_artifact(
        self, conn: TransactionContext, ctx: RequestContext, version_id: str, artifact_kind: str
    ) -> OsdkReleaseArtifactRow:
        row = self.osdk_application_repository.release_artifact(
            transaction=conn, tenant_id=ctx.tenant_id, sdk_version_id=version_id, artifact_kind=artifact_kind
        )
        if row is None:
            raise NotFound("OSDK release artifact not found")
        return row

    def _latest_released(
        self, conn: TransactionContext, ctx: RequestContext, app_id: str, language: OsdkLanguage
    ) -> OsdkSdkVersionRow | None:
        rows = self.osdk_application_repository.sdk_versions_for_application(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
        )
        released = [row for row in rows if row["language"] == language and row["release_status"] == "released"]
        return released[0] if released else None

    def _require_sdk_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        app_id: str,
        version_id: str,
    ) -> OsdkSdkVersionRow:
        row = self.osdk_application_repository.sdk_version_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, version_id=version_id
        )
        if row is None or row["app_id"] != app_id:
            raise NotFound("OSDK SDK version not found", details={"appId": app_id, "versionId": version_id})
        return row

    def _build_release_artifact(
        self,
        app: Mapping[str, object],
        resources: Sequence[OsdkApplicationResourceRow],
        decision: _ReleaseDecision,
        language: OsdkLanguage,
        package_name: str | None,
    ) -> _ArtifactDraft:
        manifest = _release_manifest(app, resources, decision, language, package_name)
        return build_release_artifact(
            self.osdk_release_artifact_store,
            manifest,
            language,
            should_persist=decision.release_status == "released",
        )

    def _insert_sdk_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        app: Mapping[str, object],
        decision: _ReleaseDecision,
        language: OsdkLanguage,
        artifact: _ArtifactDraft,
        idempotency_key: str,
    ) -> OsdkSdkVersionRow:
        record = _sdk_record(ctx, app, decision, language, artifact, idempotency_key)
        existing = self.osdk_application_repository.insert_sdk_version_or_get_existing(transaction=conn, record=record)
        row = existing or self.osdk_application_repository.sdk_version_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, version_id=record.sdk_version_id
        )
        if row is None:
            raise RuntimeError("created OSDK SDK version could not be read back")
        return row

    def _persist_artifact(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: OsdkSdkVersionRow,
        app: Mapping[str, object],
        artifact: _ArtifactDraft,
    ) -> list[OsdkReleaseArtifactRow]:
        existing = self.osdk_application_repository.release_artifact(
            transaction=conn, tenant_id=ctx.tenant_id, sdk_version_id=row["id"], artifact_kind=row["language"]
        )
        if existing is not None:
            return [existing]
        record = _artifact_record(ctx, app, row, artifact)
        return [self.osdk_application_repository.insert_release_artifact(transaction=conn, record=record)]

    def _audit_sdk_version(
        self, conn: TransactionContext, ctx: RequestContext, row: OsdkSdkVersionRow, decision: _ReleaseDecision
    ) -> None:
        event = "osdk.sdk_version.released" if decision.release_status == "released" else "osdk.sdk_version.blocked"
        self.osdk_application_scope_service._audit(
            conn, ctx, event, row, after_ref={"sdkVersion": row, "requiredBump": decision.required_bump}
        )
