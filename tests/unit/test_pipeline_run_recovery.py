from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast

import foundry_lite.application.services.pipeline_run_output_recovery as pipeline_output_recovery
import foundry_lite.application.services.pipeline_run_recovery as pipeline_run_recovery
import pytest
from foundry_lite.application.ports import PipelineExecutionLeaseFence
from foundry_lite.application.ports.media_repository import PipelineMediaCommitVersionRow
from foundry_lite.application.ports.pipeline_repository import (
    PipelineRepository,
    PipelineRunRow,
    PipelineVersionRow,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.services.pipeline_run_recovery import (
    PipelineExecutionLeaseLost,
    PipelineTerminalCommitError,
    is_stale_pipeline_execution,
    pipeline_execution_heartbeat,
    replayed_pipeline_run_action,
)
from foundry_lite.application.services.pipeline_run_terminal import succeed_pipeline_run
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation
from foundry_lite.security.tenant_context import current_tenant_id


def test_only_expired_execution_lease_is_stale() -> None:
    now = datetime(2026, 7, 28, 6, 0, tzinfo=UTC)
    long_running_with_live_lease = {
        "status": "executing",
        "started_at": (now - timedelta(hours=2)).isoformat(),
        "execution_lease_token": "live-lease",
        "execution_lease_expires_at": (now + timedelta(minutes=1)).isoformat(),
    }
    expired = {
        "status": "executing",
        "execution_lease_token": "expired-lease",
        "execution_lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
    }
    legacy_without_lease = {"status": "executing", "started_at": (now - timedelta(days=1)).isoformat()}

    assert is_stale_pipeline_execution(long_running_with_live_lease, now=now) is False
    assert is_stale_pipeline_execution(expired, now=now) is True
    assert is_stale_pipeline_execution(legacy_without_lease, now=now) is False
    assert replayed_pipeline_run_action(expired) == "fail_stale"


def test_queued_replay_executes_while_terminal_replay_only_reads() -> None:
    assert replayed_pipeline_run_action({"status": "running"}) == "execute"
    assert replayed_pipeline_run_action({"status": "succeeded"}) == "read"


def test_success_terminal_transaction_failure_requires_reconciliation() -> None:
    row = cast(PipelineRunRow, {"id": "run-committed-output"})
    version = cast(PipelineVersionRow, {"id": "version-1"})

    with pytest.raises(PipelineTerminalCommitError, match="success terminal transaction failed"):
        succeed_pipeline_run(
            cast(TransactionManager, _FailingTransactionManager()),
            cast(PipelineRepository, object()),
            cast(RuntimeEvidenceBoundary, object()),
            RequestContext(tenant_id="tenant-a"),
            row,
            version,
            {},
            [],
            [],
            cast(PipelineExecutionLeaseFence, object()),
        )


def test_geospatial_dataset_transaction_recovery_preserves_contract_and_deduplicates_passport() -> None:
    transaction_output = pipeline_output_recovery._dataset_commit_output(
        {
            "transaction_id": "tx-geo-1",
            "dataset_id": "ds-geo-1",
            "dataset_ref": "geo.locations",
            "version_id": "ver-geo-1",
            "version_number": 7,
            "manifest_uri": "memory://geo/manifest.json",
            "row_count": 1,
            "schema_hash": "geo-schema-hash",
            "metadata": {
                "pipelineRunId": "run-geo-1",
                "pipelineNodeId": "output",
                "descriptorId": "output.geospatial",
                "geospatialSpec": {
                    "encoding": "geojson",
                    "geometryField": "geometry",
                    "coordinateReferenceSystem": "EPSG:4326",
                },
            },
            "committed_at": "2026-07-28T00:00:00Z",
        },
        {"output"},
    )
    assert transaction_output is not None
    passport = pipeline_output_recovery.RecoveredPipelineOutput(
        node_id="output",
        artifact_kind="geospatial_series",
        plane="geospatial",
        status="COMMITTED",
        is_serving=True,
        ref={
            "resourceRef": "geo.locations",
            "datasetId": "ds-geo-1",
            "versionId": "ver-geo-1",
            "transactionId": "tx-geo-1",
        },
        manifest={"geospatialSpec": {"geometryField": "geometry"}},
        artifact_id="artifact-geo-1",
        recovery_source="ARTIFACT_PASSPORT",
        evidence_id="artifact-geo-1",
    )

    assert transaction_output.artifact_kind == "geospatial_series"
    assert transaction_output.plane == "geospatial"
    assert transaction_output.ref["resourceRef"] == "geo.locations"
    assert "datasetRef" not in transaction_output.ref
    assert transaction_output.manifest == {
        "resourceRef": "geo.locations",
        "versionNumber": 7,
        "rowCount": 1,
        "manifestUri": "memory://geo/manifest.json",
        "schemaHash": "geo-schema-hash",
        "geospatialSpec": {
            "encoding": "geojson",
            "geometryField": "geometry",
            "coordinateReferenceSystem": "EPSG:4326",
        },
        "commitKind": "SERVING_ASSET",
    }
    assert pipeline_output_recovery._output_key(transaction_output) == pipeline_output_recovery._output_key(passport)


def test_media_transaction_recovery_rejects_missing_durable_lineage() -> None:
    malformed = cast(
        PipelineMediaCommitVersionRow,
        {
            "transaction_id": "mtx-corrupt",
            "source_ref": None,
        },
    )

    with pytest.raises(InvariantViolation, match="inconsistent recovery lineage"):
        pipeline_output_recovery._media_pipeline_lineage(malformed)


def test_execution_heartbeat_renews_the_durable_lease(monkeypatch) -> None:
    renewed = Event()
    repository = _LeaseRenewalRepository(renewed)
    transaction_manager = _TransactionManager()
    monkeypatch.setattr(pipeline_run_recovery, "_HEARTBEAT_INTERVAL_SECONDS", 0.001)
    row = cast(PipelineRunRow, {"id": "run-a", "execution_lease_token": "lease-a"})

    with pipeline_execution_heartbeat(
        cast(TransactionManager, transaction_manager),
        cast(PipelineRepository, repository),
        RequestContext(tenant_id="tenant-a"),
        row,
    ):
        assert renewed.wait(timeout=1)

    assert repository.tokens
    assert set(repository.tokens) == {"lease-a"}
    assert set(transaction_manager.tenant_ids) == {"tenant-a"}


def test_execution_heartbeat_fences_commits_immediately_after_renewal_loss(monkeypatch) -> None:
    attempted = Event()
    repository = _LostLeaseRepository(attempted)
    monkeypatch.setattr(pipeline_run_recovery, "_HEARTBEAT_INTERVAL_SECONDS", 0.001)
    row = cast(PipelineRunRow, {"id": "run-lost", "execution_lease_token": "lease-lost"})

    with pytest.raises(PipelineExecutionLeaseLost, match="lost before commit"):
        with pipeline_execution_heartbeat(
            cast(TransactionManager, _TransactionManager()),
            cast(PipelineRepository, repository),
            RequestContext(tenant_id="tenant-a"),
            row,
        ) as guard:
            assert attempted.wait(timeout=1)
            guard.require_active()


class _TransactionManager:
    def __init__(self) -> None:
        self.tenant_ids: list[str | None] = []

    def begin(self) -> "_Transaction":
        self.tenant_ids.append(current_tenant_id())
        return _Transaction()


class _FailingTransactionManager:
    def begin(self) -> object:
        raise RuntimeError("terminal database unavailable after output commit")


class _Transaction:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *args: object) -> None:
        return None


class _LeaseRenewalRepository:
    def __init__(self, renewed: Event) -> None:
        self._renewed = renewed
        self.tokens: list[str] = []

    def renew_run_execution_lease(self, **kwargs: object) -> PipelineRunRow:
        self.tokens.append(str(kwargs["execution_lease_token"]))
        self._renewed.set()
        return cast(PipelineRunRow, {"id": kwargs["run_id"], "status": "executing"})


class _LostLeaseRepository:
    def __init__(self, attempted: Event) -> None:
        self._attempted = attempted

    def renew_run_execution_lease(self, **_kwargs: object) -> None:
        self._attempted.set()
        return None
