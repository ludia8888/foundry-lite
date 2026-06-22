from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest
from foundry_lite.application.facades.erasure_gateway import ErasureGateway
from foundry_lite.application.ports.erasure_repository import ErasureCertificateRecord, ErasureRequestRecord
from foundry_lite.application.services.erasure_service import ErasureService
from foundry_lite.domain.errors import InvariantViolation
from foundry_lite.security.erasure import (
    ErasureActionReceipt,
    ErasureActionType,
    ErasureManifestAction,
    ErasureReceiptStatus,
    ErasureRequest,
    ErasureRetentionPolicy,
    resolve_erasure_subject,
)

_ALL_ACTION_TYPES: tuple[ErasureActionType, ...] = (
    "redact_dataset_row",
    "tombstone_object",
    "remove_search_document",
    "rebuild_materialization",
    "redact_dead_letter_payload",
    "defer_backup_expiration",
    "minimize_audit_evidence",
)


class _FakeEngine:
    @contextmanager
    def begin(self) -> Iterator[None]:
        yield None


class _FakeErasureRepository:
    def __init__(self) -> None:
        self.by_idempotency: dict[tuple[str, str], ErasureRequestRecord] = {}
        self.by_id: dict[tuple[str, str], ErasureRequestRecord] = {}
        self.certificates: dict[tuple[str, str], ErasureCertificateRecord] = {}

    def insert_request_or_get_existing(
        self, *, transaction: object, record: ErasureRequestRecord
    ) -> ErasureRequestRecord | None:
        del transaction
        key = (record.tenant_id, record.idempotency_key)
        if key in self.by_idempotency:
            return self.by_idempotency[key]
        self.by_idempotency[key] = record
        self.by_id[(record.tenant_id, record.request_id)] = record
        return None

    def request_by_id(self, *, transaction: object, tenant_id: str, request_id: str) -> ErasureRequestRecord | None:
        del transaction
        return self.by_id.get((tenant_id, request_id))

    def mark_request_executed(
        self, *, transaction: object, tenant_id: str, request_id: str, manifest_id: str, executed_at: str
    ) -> None:
        del transaction, executed_at
        existing = self.by_id.get((tenant_id, request_id))
        if existing is not None:
            updated = replace(existing, status="executed", manifest_id=manifest_id)
            self.by_id[(tenant_id, request_id)] = updated
            self.by_idempotency[(tenant_id, existing.idempotency_key)] = updated

    def insert_certificate_or_get_existing(
        self, *, transaction: object, record: ErasureCertificateRecord
    ) -> ErasureCertificateRecord | None:
        del transaction
        key = (record.tenant_id, record.request_id)
        if key in self.certificates:
            return self.certificates[key]
        self.certificates[key] = record
        return None

    def certificate_by_request(
        self, *, transaction: object, tenant_id: str, request_id: str
    ) -> ErasureCertificateRecord | None:
        del transaction
        return self.certificates.get((tenant_id, request_id))


def test_request_erasure_persists_redacted_record_idempotently() -> None:
    service, repo = _service()
    request = _request()

    first = service.request_erasure(request)
    replay = service.request_erasure(request)

    assert first.request_id == request.request_id
    # The secret-backed HMAC token is persisted, never the raw subject value.
    assert first.subject_hash == request.subject_hash
    assert "ada@example.com" not in repr(repo.by_id)
    assert replay.request_id == request.request_id
    assert len(repo.by_id) == 1


def test_execute_erasure_runs_executors_and_persists_certificate() -> None:
    service, repo = _service()
    request = _request()
    service.request_erasure(request)
    resolutions = (
        resolve_erasure_subject(
            request,
            [_record("obj_1")],
            identity_fields=("email",),
            resource_type="object_record",
            surface="object_store",
        ),
    )

    certificate = service.execute_erasure(
        request,
        resolutions,
        retention_policy=_retention_policy(),
        executors={action_type: _receipt_for for action_type in _ALL_ACTION_TYPES},
    )

    assert certificate.status in {"CERTIFIED", "CERTIFIED_WITH_DEFERRED_BACKUP"}
    persisted = service.certificate_for(tenant_id=request.tenant_id, request_id=request.request_id)
    assert persisted is not None and persisted.manifest_id == certificate.manifest_id
    executed = repo.request_by_id(transaction=None, tenant_id=request.tenant_id, request_id=request.request_id)
    assert executed is not None and executed.status == "executed" and executed.manifest_id == certificate.manifest_id
    assert "ada@example.com" not in repr(persisted.certificate)


def test_execute_erasure_rejects_action_without_executor() -> None:
    service, _ = _service()
    request = _request()
    resolutions = (
        resolve_erasure_subject(
            request,
            [_record("obj_1")],
            identity_fields=("email",),
            resource_type="object_record",
            surface="object_store",
        ),
    )

    with pytest.raises(InvariantViolation, match="no erasure executor"):
        service.execute_erasure(request, resolutions, retention_policy=_retention_policy(), executors={})


class _FakeSearchAdapter:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str, str]] = []

    def delete_document(self, *, tenant_id: str, object_type: str, document_id: str) -> None:
        self.deleted.append((tenant_id, object_type, document_id))


class _FakeRuntimeRepository:
    def __init__(self) -> None:
        self.audits: list[object] = []
        self.deleted_dlq: list[tuple[str, str]] = []

    def insert_audit_event(self, *, transaction: object, record: object) -> None:
        del transaction
        self.audits.append(record)

    def delete_dead_letter_event(self, *, transaction: object, tenant_id: str, event_id: str) -> bool:
        del transaction
        self.deleted_dlq.append((tenant_id, event_id))
        return True


def test_run_erasure_via_gateway_deletes_search_documents_and_defers_backup() -> None:
    repo = _FakeErasureRepository()
    search = _FakeSearchAdapter()
    runtime = _FakeRuntimeRepository()
    service = ErasureService(
        engine=_FakeEngine(), erasure_repository=repo, runtime_repository=runtime, search_adapter=search
    )
    gateway = ErasureGateway(service)
    request = _request()
    resolutions = (
        resolve_erasure_subject(
            request,
            [{"id": "Order:O-1", "tenant_id": "tenant-a", "email": "ada@example.com"}],
            identity_fields=("email",),
            resource_type="search_document",
            surface="Order",
        ),
        resolve_erasure_subject(
            request,
            [{"id": "nightly-2026-06", "tenant_id": "tenant-a", "email": "ada@example.com"}],
            identity_fields=("email",),
            resource_type="backup_snapshot",
            surface="nightly_backup",
        ),
        resolve_erasure_subject(
            request,
            [{"id": "dlq-evt-1", "tenant_id": "tenant-a", "email": "ada@example.com"}],
            identity_fields=("email",),
            resource_type="dead_letter_record",
            surface="record_dlq",
        ),
    )

    certificate = gateway.run(request, resolutions, retention_policy=_retention_policy())

    # The real search executor removed the subject's document from the serving projection,
    # and the backup action is certified as deferred (crypto-shred at retention boundary).
    assert ("tenant-a", "Order", "Order:O-1") in search.deleted
    # The dead-letter record carrying the subject is redacted/removed.
    assert ("tenant-a", "dlq-evt-1") in runtime.deleted_dlq
    # The execution is audited (raw-value-free).
    assert len(runtime.audits) == 1
    assert "ada@example.com" not in repr(runtime.audits[0])
    assert certificate.status == "CERTIFIED_WITH_DEFERRED_BACKUP"
    persisted = gateway.certificate_for(tenant_id=request.tenant_id, request_id=request.request_id)
    assert persisted is not None
    executed = repo.request_by_id(transaction=None, tenant_id=request.tenant_id, request_id=request.request_id)
    assert executed is not None and executed.status == "executed"


def _service() -> tuple[ErasureService, _FakeErasureRepository]:
    repo = _FakeErasureRepository()
    service = ErasureService(
        engine=_FakeEngine(),
        erasure_repository=repo,
        runtime_repository=_FakeRuntimeRepository(),
        search_adapter=_FakeSearchAdapter(),
    )
    return service, repo


def _request() -> ErasureRequest:
    return ErasureRequest(
        tenant_id="tenant-a",
        request_id="erase_req_1",
        subject_kind="email",
        subject_value="ada@example.com",
        requested_by="privacy-officer",
        idempotency_key="erase-key-1",
        reason="user_request",
        requested_at="2026-06-19T00:00:00Z",
        subject_token_secret="tenant-a-erasure-secret",
        subject_token_key_id="erasure-key-1",
    )


def _retention_policy() -> ErasureRetentionPolicy:
    return ErasureRetentionPolicy(
        backup_retention_until="2026-07-19T00:00:00Z",
        crypto_shredding_key_ref="kms://tenant-a/customer-erasure",
    )


def _record(resource_id: str) -> dict[str, object]:
    return {"id": resource_id, "tenant_id": "tenant-a", "email": "ada@example.com"}


def _receipt_for(action: ErasureManifestAction) -> ErasureActionReceipt:
    status: ErasureReceiptStatus = "DEFERRED" if action.status == "DEFERRED" else "APPLIED"
    return ErasureActionReceipt(
        action_type=action.action_type,
        resource=action.resource,
        status=status,
        completed_at="2026-07-20T00:00:00Z",
        evidence={"executor": "unit-test"},
    )
