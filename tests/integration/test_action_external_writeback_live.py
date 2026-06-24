"""L8 live external-writeback ratchet: real action external side effect against live MinIO.

The action external-writeback surface already implements outcome_unknown + compensation semantics,
but until L8 they were only ever exercised through ``simulate_writeback_*`` flags. L8 makes the
writeback REAL — a real ``PUT``/``HEAD`` against a live MinIO bucket through
``S3ExternalWritebackAdapter`` — and promotes the two deferred matrix rules to enforced.

This proves the two rules end-to-end against the real adapter:

- ``test_action_external_timeout_is_outcome_unknown_not_failed`` — a real external write whose
  target endpoint is dead (a connection timeout) records the action as ``outcome_unknown`` (NOT
  failed), and a replay is the same run (idempotent, not a blind new write).
- ``test_action_external_success_local_failure_requires_real_compensation`` — a real external write
  to live MinIO SUCCEEDS (the object exists, verified by a real HEAD), then the local mutation fails
  -> the writeback is ``compensation_required`` (not a silent local-only rollback), and
  ``reconcile_action_writeback`` does a REAL ``remote_lookup`` (HEAD finds the object) to resolve it.

MinIO runs via testcontainers (available in the coverage lane, like the other media S3 integration
tests); the test never skips, so the real adapter glue stays measured. Reuses ``FOUNDRY_LITE_S3_*``
semantics through ``S3ExternalWritebackConfig`` — no new env var.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from time import monotonic, sleep
from typing import Any, cast
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.action_repository import ObjectEditRecord
from foundry_lite.domain.errors import ExternalCompensationRequired, ExternalOutcomeUnknown
from foundry_lite.infrastructure.adapters.s3_external_writeback import (
    S3ExternalWritebackAdapter,
    S3ExternalWritebackConfig,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from testcontainers.core.container import DockerContainer

from tests.conftest import prepare_indexed_demo

MINIO_ACCESS_KEY = "foundry_lite"
MINIO_SECRET_KEY = "foundry_lite_password"


@dataclass(frozen=True)
class MinioServer:
    endpoint_url: str


@pytest.fixture(scope="session")
def minio_server() -> Iterator[MinioServer]:
    container = (
        DockerContainer("minio/minio")
        .with_env("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)
        .with_command("server /data --address :9000")
        .with_exposed_ports(9000)
    )
    container.start()
    try:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}"
        _wait_until(lambda: _minio_ready(endpoint))
        yield MinioServer(endpoint_url=endpoint)
    finally:
        container.stop()


class _InjectedLocalCommitFailure(RuntimeError):
    pass


class _LocalCommitFailingActionRepository:
    """Wraps the real action repository, failing the local object-edit insert exactly once."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.enabled = True

    def __getattr__(self, name: str) -> object:
        return getattr(self.delegate, name)

    def insert_object_edit(self, *, transaction: Any, record: ObjectEditRecord) -> None:
        if self.enabled:
            raise _InjectedLocalCommitFailure("injected local commit failure after external write landed")
        self.delegate.insert_object_edit(transaction=transaction, record=record)


def _s3_client(endpoint_url: str) -> Any:
    boto3 = import_module("boto3")
    botocore_config = import_module("botocore.config")
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
        config=botocore_config.Config(s3={"addressing_style": "path"}),
    )


def _live_adapter(endpoint_url: str, bucket: str) -> S3ExternalWritebackAdapter:
    return S3ExternalWritebackAdapter(
        S3ExternalWritebackConfig(
            bucket=bucket,
            endpoint_url=endpoint_url,
            access_key_id=MINIO_ACCESS_KEY,
            secret_access_key=MINIO_SECRET_KEY,
        )
    )


def _dead_endpoint_adapter(bucket: str) -> S3ExternalWritebackAdapter:
    # A real adapter pointed at an unreachable endpoint with a tiny connect timeout: write() raises a
    # botocore connection/timeout error, which the adapter maps to an AMBIGUOUS (outcome_unknown) receipt.
    boto3 = import_module("boto3")
    botocore_config = import_module("botocore.config")
    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:1",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
        config=botocore_config.Config(
            s3={"addressing_style": "path"}, connect_timeout=1, read_timeout=1, retries={"max_attempts": 0}
        ),
    )
    return S3ExternalWritebackAdapter(S3ExternalWritebackConfig(bucket=bucket), client=client)


@pytest.mark.integration_scenario("action-external-writeback-live")
def test_action_external_timeout_is_outcome_unknown_not_failed(minio_server: MinioServer, tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    bucket = f"flite-writeback-{uuid4().hex}"
    uri = f"s3://{bucket}/order/O-1001"
    foundry.actions._action.set_external_writeback_adapter(_dead_endpoint_adapter(bucket))
    idempotency_key = "real-writeback-timeout"

    with pytest.raises(ExternalOutcomeUnknown) as raised:
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "ERP write timed out"},
            idempotency_key=idempotency_key,
            external_writeback_uri=uri,
            ctx=ctx,
        )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    unknown_runs = [r for r in snapshot["actionRuns"] if r["idempotency_key"] == idempotency_key]
    writebacks = [w for w in snapshot["actionWritebacks"] if w["idempotency_key"] == idempotency_key]
    # A timeout is outcome_unknown, not a guaranteed failure: the local mutation never applied.
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert raised.value.details["state"] == "OUTCOME_UNKNOWN"
    assert unknown_runs[0]["status"] == "outcome_unknown"
    assert writebacks[0]["status"] == "outcome_unknown"
    assert cast(dict, writebacks[0]["request"])["networkCall"] is True

    replay = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "ERP write timed out"},
        idempotency_key=idempotency_key,
        external_writeback_uri=uri,
        ctx=ctx,
    )
    replay_snapshot = foundry.operations.list_runs(ctx=ctx)
    replay_runs = [r for r in replay_snapshot["actionRuns"] if r["idempotency_key"] == idempotency_key]
    # The replay attaches to the same run (idempotent), never a blind new external write.
    assert replay["idempotentReplay"] is True
    assert replay["status"] == "outcome_unknown"
    assert replay["actionRunId"] == unknown_runs[0]["id"]
    assert [r["id"] for r in replay_runs] == [unknown_runs[0]["id"]]


@pytest.mark.integration_scenario("action-external-writeback-live")
def test_action_external_success_local_failure_requires_real_compensation(
    minio_server: MinioServer, tmp_path: Path
) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    failing_repository = _LocalCommitFailingActionRepository(dependencies.action_repository)
    foundry = FoundryLite(dependencies=replace(dependencies, action_repository=failing_repository))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    bucket = f"flite-writeback-{uuid4().hex}"
    client = _s3_client(minio_server.endpoint_url)
    client.create_bucket(Bucket=bucket)
    foundry.actions._action.set_external_writeback_adapter(_live_adapter(minio_server.endpoint_url, bucket))
    idempotency_key = "real-writeback-compensation"
    uri = f"s3://{bucket}/order/O-1001"

    with pytest.raises(ExternalCompensationRequired) as raised:
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "ERP accepted before local commit"},
            idempotency_key=idempotency_key,
            external_writeback_uri=uri,
            ctx=ctx,
        )

    # The external write provably LANDED: a real object exists in MinIO under the idempotency key.
    landed = client.get_object(Bucket=bucket, Key=f"external-writebacks/{idempotency_key}")
    assert json.loads(landed["Body"].read())["uri"] == uri

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    compensation_runs = [r for r in snapshot["actionRuns"] if r["idempotency_key"] == idempotency_key]
    writebacks = [w for w in snapshot["actionWritebacks"] if w["idempotency_key"] == idempotency_key]
    writeback_id = cast(str, [w for w in writebacks if w["status"] == "compensation_required"][0]["id"])
    # Not a silent local-only rollback: the run is compensation_required, the object stays untouched.
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert raised.value.details["state"] == "COMPENSATION_REQUIRED"
    assert compensation_runs[0]["status"] == "compensation_required"
    assert any(w["status"] == "compensation_required" for w in writebacks)

    # Resolve via a REAL remote_lookup (a real HEAD against MinIO finds the landed object). The local
    # mutation that failed earlier now succeeds, so reconciliation commits the action.
    failing_repository.enabled = False
    result = foundry.operations.reconcile_action_writeback(
        writeback_id,
        external_writeback_uri=uri,
        ctx=ctx,
    )

    reconciled = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert result["status"] == "reconciled"
    assert result["remoteStatus"] == "succeeded"
    assert reconciled["objectVersion"] == order["objectVersion"] + 1
    assert reconciled["properties"]["status"] == "APPROVED"


def _minio_ready(endpoint_url: str) -> bool:
    try:
        with urlopen(f"{endpoint_url}/minio/health/ready", timeout=2) as response:  # noqa: S310
            response.read()
            return response.status == 200
    except (ConnectionError, TimeoutError, URLError, json.JSONDecodeError):
        return False


def _wait_until(condition: Callable[[], bool], *, timeout_seconds: float = 60, interval_seconds: float = 1) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if condition():
            return
        sleep(interval_seconds)
    raise TimeoutError("condition not met before timeout")
