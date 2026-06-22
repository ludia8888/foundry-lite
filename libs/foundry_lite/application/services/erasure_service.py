from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from foundry_lite.application.ports.erasure_repository import (
    ErasureCertificateRecord,
    ErasureRequestRecord,
)
from foundry_lite.application.ports.runtime_repository import AuditEventRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.erasure_executors import default_erasure_executors
from foundry_lite.domain.errors import InvariantViolation
from foundry_lite.security.erasure import (
    ErasureActionReceipt,
    ErasureCertificate,
    ErasureManifestAction,
    ErasureRequest,
    ErasureResolution,
    ErasureRetentionPolicy,
    build_erasure_certificate,
    build_erasure_manifest,
)

ErasureActionExecutor = Callable[[ErasureManifestAction], ErasureActionReceipt]

_CERTIFIED_BY = "erasure-service"


class ErasureService(CoreService):
    """Persist erasure requests durably and execute + certify them idempotently."""

    required_dependencies = ("engine", "erasure_repository", "runtime_repository", "search_adapter")
    required_collaborators = ()

    def run_erasure(
        self,
        request: ErasureRequest,
        resolutions: Sequence[ErasureResolution],
        *,
        retention_policy: ErasureRetentionPolicy,
    ) -> ErasureCertificate:
        """Persist the request, then execute it with the production executor map and certify it."""
        self.request_erasure(request)
        executors = default_erasure_executors(search_adapter=self.search_adapter)
        return self.execute_erasure(request, resolutions, retention_policy=retention_policy, executors=executors)

    def request_erasure(self, request: ErasureRequest) -> ErasureRequestRecord:
        """Persist a raw-value-free erasure request, returning the idempotent winner."""
        record = _request_record(request)
        with self.engine.begin() as conn:
            existing = self.erasure_repository.insert_request_or_get_existing(transaction=conn, record=record)
        return existing or record

    def certificate_for(self, *, tenant_id: str, request_id: str) -> ErasureCertificateRecord | None:
        """Return the persisted certificate for one request, if it has been executed."""
        with self.engine.begin() as conn:
            return self.erasure_repository.certificate_by_request(
                transaction=conn, tenant_id=tenant_id, request_id=request_id
            )

    def execute_erasure(
        self,
        request: ErasureRequest,
        resolutions: Sequence[ErasureResolution],
        *,
        retention_policy: ErasureRetentionPolicy,
        executors: Mapping[str, ErasureActionExecutor],
    ) -> ErasureCertificate:
        """Build the manifest, run each action's executor, then persist the certificate."""
        manifest = build_erasure_manifest(request, resolutions, retention_policy=retention_policy)
        receipts = [_run_action(action, executors) for action in manifest.actions]
        certified_at = _now()
        certificate = build_erasure_certificate(
            manifest, receipts, certified_at=certified_at, certified_by=_CERTIFIED_BY
        )
        record = ErasureCertificateRecord(
            certificate_id=certificate.certificate_id,
            tenant_id=request.tenant_id,
            request_id=request.request_id,
            manifest_id=manifest.manifest_id,
            certificate=certificate.redacted_evidence(),
            certified_at=certified_at,
        )
        with self.engine.begin() as conn:
            self.erasure_repository.insert_certificate_or_get_existing(transaction=conn, record=record)
            self.erasure_repository.mark_request_executed(
                transaction=conn,
                tenant_id=request.tenant_id,
                request_id=request.request_id,
                manifest_id=manifest.manifest_id,
                executed_at=certified_at,
            )
            self.runtime_repository.insert_audit_event(
                transaction=conn, record=_audit_record(request, manifest.manifest_id, certificate, certified_at)
            )
        return certificate


def _run_action(action: ErasureManifestAction, executors: Mapping[str, ErasureActionExecutor]) -> ErasureActionReceipt:
    executor = executors.get(action.action_type)
    if executor is None:
        raise InvariantViolation("no erasure executor for action type", details={"actionType": action.action_type})
    return executor(action)


def _audit_record(
    request: ErasureRequest, manifest_id: str, certificate: ErasureCertificate, certified_at: str
) -> AuditEventRecord:
    # Raw-value-free audit proof of the erasure execution (subject_hash, never the value).
    return AuditEventRecord(
        event_id=_new_id("audit"),
        tenant_id=request.tenant_id,
        actor_user_id=request.requested_by,
        event_type="privacy.erasure.executed",
        resource_type="erasure_request",
        resource_id=request.request_id,
        action="erasure:execute",
        decision="allow",
        policy_decision={},
        before_ref={"subjectHash": request.subject_hash},
        after_ref={"certificateId": certificate.certificate_id, "status": certificate.status},
        correlation_id=request.request_id,
        request_id=request.request_id,
        metadata={"manifestId": manifest_id},
        created_at=certified_at,
    )


def _request_record(request: ErasureRequest) -> ErasureRequestRecord:
    return ErasureRequestRecord(
        request_id=request.request_id,
        tenant_id=request.tenant_id,
        subject_kind=request.subject_kind,
        subject_hash=request.subject_hash,
        subject_token_key_id=request.subject_token_key_id,
        requested_by=request.requested_by,
        idempotency_key=request.idempotency_key,
        reason=request.reason,
        status="requested",
        evidence=request.redacted_evidence(),
        requested_at=request.requested_at,
    )
