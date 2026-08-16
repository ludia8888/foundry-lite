from __future__ import annotations

import threading

from foundry_lite.application.ports.action_run_orchestrator import ActionRunDispatchRequest
from foundry_lite.infrastructure.adapters.action_run_orchestrator import LocalActionRunOrchestrator


def test_action_run_orchestrator_contract_dispatches_once_and_scopes_cancel_to_tenant() -> None:
    adapter = LocalActionRunOrchestrator()
    delivered = threading.Event()
    adapter.register_driver(lambda request: delivered.set())
    request = ActionRunDispatchRequest("tenant-a", "run-1", "ApproveOrder", "req-1", "idem-1", {})

    first = adapter.dispatch(request)
    replay = adapter.dispatch(request)

    assert delivered.wait(timeout=2)
    assert first.status == "dispatched"
    assert replay.status == "already_dispatched"
    assert adapter.cancel("tenant-b", first.workflow_run_id) is False
    assert adapter.cancel("tenant-a", first.workflow_run_id) is True
    adapter.close()


def test_action_run_orchestrator_close_drains_work_and_rejects_new_dispatch() -> None:
    adapter = LocalActionRunOrchestrator()
    release = threading.Event()
    completed = threading.Event()

    def drive(_request: ActionRunDispatchRequest) -> None:
        assert release.wait(2)
        completed.set()

    adapter.register_driver(drive)
    accepted = adapter.dispatch(ActionRunDispatchRequest("tenant-a", "run-1", "Approve", "req-1", "idem-1", {}))
    release.set()
    adapter.close()
    rejected = adapter.dispatch(ActionRunDispatchRequest("tenant-a", "run-2", "Approve", "req-2", "idem-2", {}))

    assert accepted.status == "dispatched"
    assert completed.is_set()
    assert rejected.status == "unknown"
    assert adapter._worker is not None
    assert not adapter._worker.is_alive()
    adapter.close()
