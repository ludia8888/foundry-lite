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
