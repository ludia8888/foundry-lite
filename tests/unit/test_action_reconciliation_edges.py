from __future__ import annotations

import pytest
from foundry_lite.application.ports.action_repository import ActionRunRow, ActionWritebackRecord
from foundry_lite.application.services.action_reconciliation import (
    _already_reconciled_result,
    _is_resolvable_writeback,
    _reconciled_result,
    _reconciled_writeback_response,
    _validate_remote_success,
)
from foundry_lite.domain.errors import ValidationFailed


def test_validate_remote_success_rejects_unresolved_remote_outcomes() -> None:
    with pytest.raises(ValidationFailed, match="only remote success"):
        _validate_remote_success("failed", "crm-1")
    with pytest.raises(ValidationFailed, match="remote resource id is required"):
        _validate_remote_success("succeeded", "")


def test_reconciled_writeback_response_preserves_vendor_evidence() -> None:
    response = _reconciled_writeback_response(
        _writeback(response={"vendorTraceId": "trace-1", "status_code": 202}),
        "succeeded",
        "remote-1",
        "2026-06-19T00:00:00Z",
    )

    assert response == {
        "vendorTraceId": "trace-1",
        "status_code": 200,
        "outcome_unknown": False,
        "reconciled": True,
        "remote_resource_id": "remote-1",
        "last_observed_status": "succeeded",
        "reconciled_at": "2026-06-19T00:00:00Z",
    }


def test_reconciliation_results_include_mutation_and_idempotent_replay_evidence() -> None:
    reconciled = _reconciled_result(
        "run-1",
        "writeback-1",
        "succeeded",
        "remote-1",
        {
            "actionRunId": "run-1",
            "status": "succeeded",
            "target": {"objectId": "order-1"},
            "objectEditId": "edit-1",
            "newObjectVersion": 3,
        },
    )
    already = _already_reconciled_result(
        _writeback(
            response={
                "last_observed_status": "succeeded",
                "remote_resource_id": "persisted-remote",
            }
        ),
        "succeeded",
        "caller-remote",
    )

    assert reconciled["objectEditId"] == "edit-1"
    assert reconciled["newObjectVersion"] == 3
    assert already == {
        "actionRunId": "run-1",
        "writebackId": "writeback-1",
        "status": "reconciled",
        "remoteStatus": "succeeded",
        "remoteResourceId": "persisted-remote",
        "alreadyReconciled": True,
    }


def test_terminal_action_run_is_not_treated_as_reconciliation_candidate() -> None:
    assert not _is_resolvable_writeback(_writeback(), _action_run(status="succeeded"))
    assert _is_resolvable_writeback(_writeback(), _action_run(status="outcome_unknown"))


def _writeback(response: dict[str, object] | None = None) -> ActionWritebackRecord:
    return ActionWritebackRecord(
        writeback_id="writeback-1",
        tenant_id="tenant-demo",
        action_run_id="run-1",
        mode="before_commit",
        connector_id="crm",
        request={"customerId": "customer-1"},
        response=response,
        status="outcome_unknown",
        idempotency_key="idem-1",
        attempts=1,
        created_at="2026-06-19T00:00:00Z",
        completed_at=None,
    )


def _action_run(status: str) -> ActionRunRow:
    return {
        "id": "run-1",
        "tenant_id": "tenant-demo",
        "action_type_id": "atype-1",
        "action_type_api_name": "ApproveOrder",
        "actor_user_id": "user-1",
        "target_object_type_id": "otype-1",
        "target_object_type_api_name": "Order",
        "target_object_id": "O-1001",
        "expected_object_version": 1,
        "parameters": {},
        "status": status,
        "idempotency_key": "idem-1",
        "request_fingerprint": "hash-1",
        "result": None,
        "error": None,
        "created_at": "2026-06-19T00:00:00Z",
        "completed_at": None,
    }
