"""Per-port contract for ``ExternalWritebackAdapter`` (L8 real action external writeback).

The port has three deliberate reach-out shapes, and conflating any two is the failure mode the
action saga guards against. This contract proves both a Fake adapter and the real
``S3ExternalWritebackAdapter`` (driven by a stubbed boto3 client — deterministic, no Docker) agree
on them:

- a clean write -> ``LANDED`` with a remote_resource_id;
- a timeout / connection error while writing -> ``AMBIGUOUS`` (outcome unknown, NOT a failure);
- ``remote_lookup`` finds a present object -> ``LANDED``; a genuine 404 -> ``ABSENT``; the same key
  resolves the same way on replay (idempotent re-read).

The live MinIO end-to-end path is proven in
``tests/integration/test_action_external_writeback_live.py``.
"""

from __future__ import annotations

from typing import Any, Protocol

import pytest
from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackPayload,
    ExternalWriteTarget,
    RemoteOutcome,
    RemoteOutcomeStatus,
    UnavailableExternalWritebackAdapter,
    WriteReceipt,
)
from foundry_lite.infrastructure.adapters.s3_external_writeback import (
    S3ExternalWritebackAdapter,
    S3ExternalWritebackConfig,
)


class ExternalWritebackAdapterUnderTest(Protocol):
    @property
    def profile_name(self) -> str: ...

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt: ...

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome: ...

    def arm_timeout(self) -> None: ...


class _FakeExternalWritebackAdapter:
    """In-memory adapter: a landed key is remembered, a timeout is armed explicitly."""

    profile_name = "fake-external-writeback"

    def __init__(self) -> None:
        self._landed: set[str] = set()
        self._timeout_armed = False

    def arm_timeout(self) -> None:
        self._timeout_armed = True

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        del payload
        if self._timeout_armed:
            return WriteReceipt(status=RemoteOutcomeStatus.AMBIGUOUS)
        self._landed.add(target.idempotency_key)
        return WriteReceipt(status=RemoteOutcomeStatus.LANDED, remote_resource_id=f"fake/{target.idempotency_key}")

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        if target.idempotency_key in self._landed:
            return RemoteOutcome(status=RemoteOutcomeStatus.LANDED, remote_resource_id=f"fake/{target.idempotency_key}")
        return RemoteOutcome(status=RemoteOutcomeStatus.ABSENT)


class _ConnectTimeout(Exception):
    """Mimics a botocore connect/read timeout (transient, ambiguous)."""


class _NotFound(Exception):
    def __init__(self) -> None:
        super().__init__("missing")
        self.response = {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class _StubS3Client:
    """Minimal boto3-like S3 client: stores objects in memory, can be armed to time out on PUT."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._timeout_armed = False

    def arm_timeout(self) -> None:
        self._timeout_armed = True

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803 - boto3 kwargs
        del Bucket
        if self._timeout_armed:
            raise _ConnectTimeout("connect timeout")
        self._objects[Key] = Body

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        del Bucket
        if Key not in self._objects:
            raise _NotFound()
        return {"ContentLength": len(self._objects[Key])}


class _S3AdapterUnderTest:
    def __init__(self) -> None:
        self._client = _StubS3Client()
        self._adapter = S3ExternalWritebackAdapter(S3ExternalWritebackConfig(bucket="external"), client=self._client)

    @property
    def profile_name(self) -> str:
        return self._adapter.profile_name

    def arm_timeout(self) -> None:
        self._client.arm_timeout()

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        return self._adapter.write(target, payload)

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        return self._adapter.remote_lookup(target)


@pytest.fixture(params=["fake", "s3-stub"])
def adapter(request: pytest.FixtureRequest) -> ExternalWritebackAdapterUnderTest:
    if request.param == "fake":
        return _FakeExternalWritebackAdapter()
    return _S3AdapterUnderTest()


def _target(key: str = "idem-1") -> ExternalWriteTarget:
    return ExternalWriteTarget(uri="s3://external/order-1", idempotency_key=key)


def test_external_writeback_adapter_contract_clean_write_lands(adapter: ExternalWritebackAdapterUnderTest) -> None:
    receipt = adapter.write(_target(), {"params": {"status": "APPROVED"}})

    assert receipt.status is RemoteOutcomeStatus.LANDED
    assert receipt.remote_resource_id


def test_external_writeback_adapter_contract_timeout_is_ambiguous_not_failure(
    adapter: ExternalWritebackAdapterUnderTest,
) -> None:
    adapter.arm_timeout()

    receipt = adapter.write(_target(), {"params": {"status": "APPROVED"}})

    # A timeout is outcome-unknown, never a guaranteed failure (the write may have landed).
    assert receipt.status is RemoteOutcomeStatus.AMBIGUOUS


def test_external_writeback_adapter_contract_remote_lookup_resolves_present_and_absent(
    adapter: ExternalWritebackAdapterUnderTest,
) -> None:
    absent = adapter.remote_lookup(_target("never-written"))
    adapter.write(_target("landed-key"), {"params": {"status": "APPROVED"}})
    present = adapter.remote_lookup(_target("landed-key"))
    present_replay = adapter.remote_lookup(_target("landed-key"))

    assert absent.status is RemoteOutcomeStatus.ABSENT
    assert present.status is RemoteOutcomeStatus.LANDED
    # An idempotent re-read of the same key resolves identically (never a blind new write).
    assert present_replay.status is RemoteOutcomeStatus.LANDED
    assert present_replay.remote_resource_id == present.remote_resource_id


def test_external_writeback_adapter_contract_default_adapter_is_unavailable() -> None:
    # The default adapter has no vendor bundled, so a real writeback raises (seam stays injectable).
    unavailable = UnavailableExternalWritebackAdapter()
    with pytest.raises(Exception, match="external_writeback_unavailable"):
        unavailable.write(_target(), {})
    with pytest.raises(Exception, match="external_writeback_unavailable"):
        unavailable.remote_lookup(_target())
