from __future__ import annotations

import asyncio
from typing import Any

import pytest
from temporalio.service import RPCError, RPCStatusCode

from tests.integration.helpers import temporal_readiness


def test_waits_for_namespace_registration_after_port_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    class WorkflowService:
        async def describe_namespace(self, _request: Any) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RPCError("namespace is not registered", RPCStatusCode.NOT_FOUND, b"")

    class TemporalClient:
        workflow_service = WorkflowService()

    async def connect(_address: str, *, namespace: str) -> TemporalClient:
        assert namespace == "default"
        return TemporalClient()

    monkeypatch.setattr(temporal_readiness.Client, "connect", connect)

    asyncio.run(
        temporal_readiness.wait_for_temporal_namespace(
            "127.0.0.1:7233",
            timeout=1,
            poll_interval=0,
        )
    )

    assert attempts == 2


def test_does_not_hide_non_retryable_namespace_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class WorkflowService:
        async def describe_namespace(self, _request: Any) -> None:
            raise RPCError("permission denied", RPCStatusCode.PERMISSION_DENIED, b"")

    class TemporalClient:
        workflow_service = WorkflowService()

    async def connect(_address: str, *, namespace: str) -> TemporalClient:
        return TemporalClient()

    monkeypatch.setattr(temporal_readiness.Client, "connect", connect)

    with pytest.raises(RPCError, match="permission denied"):
        asyncio.run(temporal_readiness.wait_for_temporal_namespace("127.0.0.1:7233", timeout=1))
