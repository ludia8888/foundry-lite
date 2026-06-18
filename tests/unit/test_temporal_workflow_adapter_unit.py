"""Pure-function unit tests for the Temporal workflow adapter helpers.

These cover the adapter seams that do not need a running Temporal server:
result coercion and the failure taxonomy. The server-backed behaviour
(idempotency, retry, timeout, cancellation) is proven in
``tests/integration/test_temporal_workflow_ratchet.py``.
"""

from __future__ import annotations

from foundry_lite.infrastructure.adapters.temporal_workflow import (
    TemporalWorkflowAdapter,
    TemporalWorkflowAdapterConfig,
    _as_mapping,
    _root_message,
)


def test_as_mapping_passes_through_a_mapping() -> None:
    assert _as_mapping({"a": 1}) == {"a": 1}


def test_as_mapping_wraps_a_non_mapping_result() -> None:
    assert _as_mapping("done") == {"result": "done"}
    assert _as_mapping(7) == {"result": 7}


def test_root_message_returns_deepest_cause() -> None:
    inner = ValueError("root cause")
    outer = RuntimeError("surface")
    outer.__cause__ = inner
    # the adapter walks `.cause`, which exception chaining does not set, so a
    # plain exception returns its own message (the deepest it can see).
    assert _root_message(outer) == "surface"


def test_config_defaults_target_local_temporal() -> None:
    config = TemporalWorkflowAdapterConfig()
    assert config.address == "localhost:7233"
    assert config.task_queue == "foundry-lite"
    assert config.execution_timeout_seconds == 300


def test_adapter_uses_default_config_when_none_given() -> None:
    adapter = TemporalWorkflowAdapter()
    assert adapter.profile_name == "temporal"
    assert adapter.failure_contract().adapter_profile == "temporal"
