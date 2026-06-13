from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from foundry_lite.application.ports.action_repository import ActionRunRecord, ActionRunRow
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.domain.context import RequestContext


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


class _UnexpectedMutation:
    def _object_record(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("idempotency race replay must not reach object mutation")


def test_action_service_replays_when_insert_loses_idempotency_race() -> None:
    repository = _RaceActionRepository()
    service = ActionService(engine=_FakeEngine(), policy=_AllowPolicy(), action_repository=repository)
    service.bind_collaborators(
        {
            "object_indexing_service": _UnexpectedMutation(),
            "object_records_service": _UnexpectedMutation(),
            "ontology_service": _Ontology(),
            "runtime_service": object(),
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

    assert response["idempotentReplay"] is True
    assert response["actionRunId"] == "action_run_winner"
    assert response["target"] == {"objectType": "Order", "objectId": "O-1001"}
    assert repository.lookup_count == 1
    assert repository.insert_or_get_count == 1


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
        "error": None,
        "created_at": "2026-06-13T00:00:00Z",
        "completed_at": "2026-06-13T00:00:01Z",
    }
