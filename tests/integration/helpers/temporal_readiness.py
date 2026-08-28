"""Readiness helper for live Temporal integration tests."""

from __future__ import annotations

import asyncio
from threading import Event

from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

_RETRYABLE_STATUSES = {
    RPCStatusCode.DEADLINE_EXCEEDED,
    RPCStatusCode.NOT_FOUND,
    RPCStatusCode.UNAVAILABLE,
}


async def wait_for_temporal_namespace(
    address: str,
    *,
    namespace: str = "default",
    timeout: float = 60,
    poll_interval: float = 0.25,
) -> None:
    """Wait until Temporal can describe the namespace used by the test."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_error: RPCError | OSError | None = None
    while True:
        try:
            client = await Client.connect(address, namespace=namespace)
            await client.workflow_service.describe_namespace(DescribeNamespaceRequest(namespace=namespace))
            return
        except RPCError as error:
            if error.status not in _RETRYABLE_STATUSES:
                raise
            last_error = error
        except OSError as error:
            last_error = error
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError(f"Temporal namespace {namespace!r} was not ready at {address}") from last_error
        await asyncio.to_thread(Event().wait, min(poll_interval, remaining))
