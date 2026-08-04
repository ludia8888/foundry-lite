from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from foundry_lite.application.ports.action_repository import ActionRunRecord, ActionRunRow
from foundry_lite.application.services.action_apply_service import ActionApplyService
from foundry_lite.application.services.action_helpers import action_request_fingerprint
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied


class _FakeEngine:
    @contextmanager
    def begin(self):
        yield object()


class _AllowPolicy:
    def require(self, _ctx: RequestContext, _permission: str) -> None:
        return None


class _RaceActionRepository:
    def __init__(self) -> None:
        self.lookup_count = 0
        self.insert_or_get_count = 0
        self.existing = _action_run_row()

    def action_run_by_idempotency(self, **_kwargs: object) -> ActionRunRow | None:
        self.lookup_count += 1
        return None

    def insert_action_run_or_get_existing(self, *, transaction: object, record: ActionRunRecord) -> ActionRunRow | None:
        del transaction, record
        self.insert_or_get_count += 1
        return self.existing


class _Ontology:
    def _active_action_type(self, _conn: object, _ctx: RequestContext, action_api_name: str) -> dict[str, Any]:
        return {
            "id": "atype_approve",
            "tenant_id": "tenant-demo",
            "ontology_version_id": "ont_1",
            "api_name": action_api_name,
            "display_name": action_api_name,
            "target_object_type_id": "ot_order",
            "target_api_name": "Order",
            "parameter_schema": {},
            "definition": {"mutations": []},
            "enabled": True,
        }

    def link_type(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("v1 idempotency path must not resolve link types")

    def _active_object_type(self, _conn: object, _ctx: RequestContext, object_type: str) -> dict[str, Any]:
        return {"id": "ot_order", "api_name": object_type}


class _UnexpectedMutation:
    def _object_record(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("idempotency race replay must not reach object mutation")


class _AllowOsdkScope:
    def require_resource_scope(self, *_args: object, **_kwargs: object) -> None:
        return None


class _UnusedWriteback:
    def real_writeback_runner(self, _command: object) -> None:
        # No external writeback URI on these commands, so there is no real runner and the
        # apply takes the single-transaction local path.
        return None


class _WriteOpenRuntime:
    """Runtime stub for a tenant that is not in restore mode (write traffic open)."""

    def __init__(self) -> None:
        self.audits: list[dict[str, object]] = []

    def _require_write_traffic_open(
        self,
        _ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        del operation, resource_type, resource_id
        return None

    def _audit(
        self,
        _conn: object,
        _ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str,
        action: str,
        decision: str = "allow",
        after_ref: dict[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.audits.append(
            {
                "event_type": event_type,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "action": action,
                "decision": decision,
                "after_ref": after_ref or {},
                "correlation_id": correlation_id,
            }
        )


def test_action_apply_service_replays_when_insert_loses_idempotency_race() -> None:
    repository = _RaceActionRepository()
    service = ActionApplyService(engine=_FakeEngine(), policy=_AllowPolicy(), action_repository=repository)
    service.bind_collaborators(
        {
            "action_writeback_service": _UnusedWriteback(),
            "object_index_record_mutation_service": _UnexpectedMutation(),
            "object_records_service": _UnexpectedMutation(),
            "ontology_service": _Ontology(),
            "ontology_lookup_service": _Ontology(),
            "osdk_application_service": _AllowOsdkScope(),
            "runtime_service": _WriteOpenRuntime(),
        }
    )

    response = service.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=1,
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key",
        ctx=RequestContext(roles=("admin",)),
    )

    assert response.get("idempotentReplay") is True
    assert response["actionRunId"] == "action_run_winner"
    assert response.get("objectEditId") == "edit_winner"
    assert response.get("newObjectVersion") == 2
    assert response["target"] == {"objectType": "Order", "objectId": "O-1001"}
    assert repository.lookup_count == 1
    assert repository.insert_or_get_count == 1


def test_protected_runtime_blocks_action_failure_injection_before_run_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "production")
    repository = _RaceActionRepository()
    runtime = _WriteOpenRuntime()
    service = ActionApplyService(engine=_FakeEngine(), policy=_AllowPolicy(), action_repository=repository)
    service.bind_collaborators(
        {
            "action_writeback_service": _UnusedWriteback(),
            "object_index_record_mutation_service": _UnexpectedMutation(),
            "object_records_service": _UnexpectedMutation(),
            "ontology_service": _Ontology(),
            "ontology_lookup_service": _Ontology(),
            "osdk_application_service": _AllowOsdkScope(),
            "runtime_service": runtime,
        }
    )

    with pytest.raises(PermissionDenied, match="failure injection is disabled"):
        service.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=1,
            params={"reason": "Inventory confirmed"},
            idempotency_key="same-key",
            simulate_writeback_outcome_unknown=True,
            ctx=RequestContext(roles=("admin",)),
        )

    assert repository.lookup_count == 0
    assert repository.insert_or_get_count == 0
    assert runtime.audits == [
        {
            "event_type": "action.failure_injection.denied",
            "resource_type": "action_type",
            "resource_id": "ApproveOrder",
            "action": "apply",
            "decision": "deny",
            "after_ref": {
                "runtime_profile": "production",
                "action_api_name": "ApproveOrder",
                "failure_injection": {
                    "simulate_writeback_failure": False,
                    "simulate_writeback_retryable": False,
                    "simulate_writeback_outcome_unknown": True,
                    "simulate_writeback_compensation_required": False,
                },
            },
            "correlation_id": None,
        }
    ]


def test_action_request_fingerprint_includes_writeback_simulation_flags() -> None:
    base = action_request_fingerprint(
        action_api_name="ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=1,
        params={"reason": "Inventory confirmed"},
    )
    simulated = action_request_fingerprint(
        action_api_name="ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=1,
        params={"reason": "Inventory confirmed"},
        simulate_writeback_outcome_unknown=True,
    )
    retryable = action_request_fingerprint(
        action_api_name="ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=1,
        params={"reason": "Inventory confirmed"},
        simulate_writeback_retryable=True,
    )

    assert simulated != base
    assert retryable != base
    assert retryable != simulated


def _action_run_row() -> ActionRunRow:
    return {
        "id": "action_run_winner",
        "tenant_id": "tenant-demo",
        "action_type_id": "atype_approve",
        "action_type_api_name": "ApproveOrder",
        "actor_user_id": "user-demo",
        "target_object_type_id": "ot_order",
        "target_object_type_api_name": "Order",
        "target_object_id": "O-1001",
        "expected_object_version": 1,
        "parameters": {"reason": "Inventory confirmed"},
        "status": "succeeded",
        "idempotency_key": "same-key",
        "request_fingerprint": action_request_fingerprint(
            action_api_name="ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=1,
            params={"reason": "Inventory confirmed"},
        ),
        "result": {
            "actionRunId": "action_run_winner",
            "status": "succeeded",
            "objectEditId": "edit_winner",
            "newObjectVersion": 2,
        },
        "error": None,
        "external_writeback_uri": None,
        "created_at": "2026-06-13T00:00:00Z",
        "completed_at": "2026-06-13T00:00:01Z",
    }
