from __future__ import annotations

from foundry_lite.application.services.action_service import ActionService
from foundry_lite.application.services.base import CoreService


class _ApplyRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def apply_action(self, action_api_name: str, **kwargs: object) -> dict[str, object]:
        self.calls.append((action_api_name, kwargs))
        return {"actionRunId": "action_run_1"}


class _BatchApplyRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def apply_action_batch(self, action_api_name: str, **kwargs: object) -> dict[str, object]:
        self.calls.append((action_api_name, kwargs))
        return {"actionRunId": "action_run_batch_1"}


class _ValidationRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def validate_action(self, action_api_name: str, **kwargs: object) -> dict[str, object]:
        self.calls.append((action_api_name, kwargs))
        return {"valid": True}


class _WritebackRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def set_external_writeback_adapter(self, adapter: object) -> None:
        self.calls.append(("set", (adapter,), {}))

    def recover_action_writebacks(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("recover", (), kwargs))
        return {"recovered": []}


def test_action_service_forwards_to_focused_use_case_services() -> None:
    service = ActionService()
    apply = _ApplyRecorder()
    batch_apply = _BatchApplyRecorder()
    validation = _ValidationRecorder()
    writeback = _WritebackRecorder()
    service.bind_collaborators(
        {
            "action_apply_service": apply,
            "action_async_run_service": object(),
            "action_batch_apply_service": batch_apply,
            "action_branch_service": object(),
            "action_definition_service": object(),
            "action_log_revert_service": object(),
            "action_media_service": object(),
            "action_planning_service": object(),
            "action_validation_service": validation,
            "action_writeback_service": writeback,
        }
    )

    applied = service.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1",
        expected_object_version=1,
        params={"reason": "ok"},
        idempotency_key="idem-1",
    )
    batch_applied = service.apply_action_batch(
        "ApproveOrder",
        object_type="Order",
        targets=[{"object_id": "O-1", "expected_object_version": 1}],
        params={"reason": "ok"},
        idempotency_key="idem-batch-1",
    )
    validated = service.validate_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1",
        expected_object_version=1,
        params={"reason": "ok"},
    )
    recovered = service.recover_action_writebacks(limit=3)

    assert applied == {"actionRunId": "action_run_1"}
    assert batch_applied == {"actionRunId": "action_run_batch_1"}
    assert validated == {"valid": True}
    assert recovered == {"recovered": []}
    assert apply.calls[0][0] == "ApproveOrder"
    assert apply.calls[0][1]["idempotency_key"] == "idem-1"
    assert batch_apply.calls[0][0] == "ApproveOrder"
    assert batch_apply.calls[0][1]["idempotency_key"] == "idem-batch-1"
    assert batch_apply.calls[0][1]["targets"] == [{"object_id": "O-1", "expected_object_version": 1}]
    assert validation.calls[0][0] == "ApproveOrder"
    assert writeback.calls == [("recover", (), {"limit": 3, "ctx": None})]


def test_action_service_no_longer_uses_mixin_inheritance() -> None:
    assert ActionService.__mro__ == (ActionService, CoreService, object)
