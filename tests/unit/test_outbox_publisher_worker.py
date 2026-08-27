from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import OutboxEventRecord
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker import outbox_publisher
from foundry_lite_worker.outbox_publisher import (
    OutboxPublisherWorkerConfig,
    config_from_env,
    main,
    publish_outbox_batches,
)


def test_outbox_publisher_worker_publishes_until_empty_batch(tmp_path: Path) -> None:
    foundry, db_url, storage_root = _seeded_foundry(tmp_path, "outbox_worker_1", "outbox_worker_2")
    evidence_path = tmp_path / "artifacts" / "outbox-publisher.json"

    result = publish_outbox_batches(
        OutboxPublisherWorkerConfig(
            db_url=db_url,
            storage_root=storage_root,
            max_batches=3,
            max_empty_batches=1,
            evidence_path=evidence_path,
        )
    )
    rows = _outbox_rows(foundry)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert result.stop_reason == "empty_batches"
    assert result.iterations == 2
    assert result.requested == 2
    assert result.published == 2
    assert result.retrying == 0
    assert result.event_ids == ("outbox_worker_1", "outbox_worker_2")
    assert rows["outbox_worker_1"]["status"] == "published"
    assert rows["outbox_worker_2"]["published_at"] is not None
    assert evidence["published"] == 2
    assert evidence["stopReason"] == "empty_batches"
    assert evidence["tenantIds"] == ["tenant-demo"]


def test_outbox_publisher_worker_main_prints_operator_summary(tmp_path: Path, capsys) -> None:
    _foundry, db_url, storage_root = _seeded_foundry(tmp_path, "outbox_worker_cli")

    exit_code = main(
        [
            "--db-url",
            db_url,
            "--storage-root",
            str(storage_root),
            "--max-batches",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "STOPPED"
    assert payload["published"] == 1
    assert payload["retrying"] == 0
    assert payload["eventIds"] == ["outbox_worker_cli"]
    assert payload["tenantIds"] == ["tenant-demo"]


def test_outbox_publisher_worker_config_from_env(tmp_path: Path) -> None:
    config = config_from_env(
        {
            "FOUNDRY_LITE_HOME": str(tmp_path / "home"),
            "FOUNDRY_LITE_DB_URL": "sqlite:///worker.db",
            "FOUNDRY_LITE_OUTBOX_STREAM_NAME": "ops-outbox",
            "FOUNDRY_LITE_OUTBOX_LIMIT": "7",
            "FOUNDRY_LITE_OUTBOX_MAX_BATCHES": "3",
            "FOUNDRY_LITE_OUTBOX_MAX_EMPTY_BATCHES": "2",
            "FOUNDRY_LITE_OUTBOX_EVIDENCE_PATH": str(tmp_path / "evidence.json"),
        }
    )

    assert config.storage_root == tmp_path / "home"
    assert config.db_url == "sqlite:///worker.db"
    assert config.stream_name == "ops-outbox"
    assert config.limit == 7
    assert config.max_batches == 3
    assert config.max_empty_batches == 2
    assert config.evidence_path == tmp_path / "evidence.json"


def test_outbox_worker_never_runs_schema_ddl(monkeypatch, tmp_path: Path) -> None:
    dependencies = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(outbox_publisher, "create_runtime_core_dependencies", lambda **_kwargs: dependencies)

    def foundry_lite(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(outbox_publisher, "FoundryLite", foundry_lite)

    outbox_publisher._build_foundry(OutboxPublisherWorkerConfig(storage_root=tmp_path))

    assert captured == {"dependencies": dependencies, "should_initialize_schema": False}


def test_outbox_publisher_worker_closes_runtime_when_publish_raises(monkeypatch, tmp_path: Path) -> None:
    class FailingOutboxPublisher:
        def publish_all_pending_outbox(self, **_kwargs: object) -> object:
            raise RuntimeError("injected publish failure")

    close_calls: list[str] = []
    monkeypatch.setattr(
        outbox_publisher,
        "_build_foundry",
        lambda _config: SimpleNamespace(
            _services=SimpleNamespace(outbox_publisher=FailingOutboxPublisher()),
            close=lambda: close_calls.append("close"),
        ),
    )

    with pytest.raises(RuntimeError, match="injected publish failure"):
        publish_outbox_batches(OutboxPublisherWorkerConfig(storage_root=tmp_path))

    assert close_calls == ["close"]


def test_outbox_publisher_worker_treats_restore_mode_as_a_clean_pause(monkeypatch, tmp_path: Path, capsys) -> None:
    class PausedOutboxPublisher:
        def publish_all_pending_outbox(self, **_kwargs: object) -> object:
            raise ConflictDetected(
                "restore mode keeps outbox publisher paused",
                details={
                    "restore_id": "enterprise-qa-backup-1",
                    "status": "paused",
                    "is_outbox_publisher_paused": True,
                },
            )

    close_calls: list[str] = []
    monkeypatch.setattr(
        outbox_publisher,
        "_build_foundry",
        lambda _config: SimpleNamespace(
            _services=SimpleNamespace(outbox_publisher=PausedOutboxPublisher()),
            close=lambda: close_calls.append("close"),
        ),
    )

    exit_code = main(["--storage-root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "PAUSED"
    assert payload["stopReason"] == "restore_mode"
    assert payload["iterations"] == 0
    assert payload["published"] == 0
    assert close_calls == ["close"]


def test_outbox_publisher_worker_does_not_hide_unrelated_conflicts(monkeypatch, tmp_path: Path) -> None:
    class ConflictingOutboxPublisher:
        def publish_all_pending_outbox(self, **_kwargs: object) -> object:
            raise ConflictDetected("unrelated publisher conflict", details={"resource": "stream"})

    monkeypatch.setattr(
        outbox_publisher,
        "_build_foundry",
        lambda _config: SimpleNamespace(
            _services=SimpleNamespace(outbox_publisher=ConflictingOutboxPublisher()),
            close=lambda: None,
        ),
    )

    with pytest.raises(ConflictDetected, match="unrelated publisher conflict"):
        publish_outbox_batches(OutboxPublisherWorkerConfig(storage_root=tmp_path))


def test_outbox_publisher_worker_publishes_pending_rows_for_every_tenant(tmp_path: Path) -> None:
    foundry, db_url, storage_root = _seeded_foundry(tmp_path, "outbox_demo")
    dependencies = create_local_core_dependencies(db_url=db_url, storage_root=storage_root)
    _insert_outbox(dependencies, event_id="outbox_a", request_id="req-a", tenant_id="tenant-a")
    _insert_outbox(dependencies, event_id="outbox_b", request_id="req-b", tenant_id="tenant-b")

    result = publish_outbox_batches(
        OutboxPublisherWorkerConfig(db_url=db_url, storage_root=storage_root, max_batches=1)
    )

    assert result.published == 3
    assert result.tenant_ids == ("tenant-a", "tenant-b", "tenant-demo")
    assert _outbox_rows(foundry, tenant_id="tenant-a")["outbox_a"]["status"] == "published"
    assert _outbox_rows(foundry, tenant_id="tenant-b")["outbox_b"]["status"] == "published"


def test_outbox_event_registers_an_unseen_tenant_before_publisher_scans(tmp_path: Path) -> None:
    foundry, db_url, storage_root = _seeded_foundry(tmp_path)
    dependencies = create_local_core_dependencies(db_url=db_url, storage_root=storage_root)
    _insert_outbox(
        dependencies,
        event_id="outbox_unseen_tenant",
        request_id="req-unseen",
        tenant_id="tenant-unseen",
    )

    result = publish_outbox_batches(
        OutboxPublisherWorkerConfig(db_url=db_url, storage_root=storage_root, max_batches=1)
    )

    assert "tenant-unseen" in dependencies.metadata_repository.list_tenant_ids()
    assert result.tenant_ids == ("tenant-demo", "tenant-unseen")
    assert _outbox_rows(foundry, tenant_id="tenant-unseen")["outbox_unseen_tenant"]["status"] == "published"


def test_outbox_tenant_registration_rolls_back_with_the_event(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'runtime.db'}",
        storage_root=tmp_path / "runtime",
    )
    FoundryLite(dependencies=dependencies)
    with dependencies.engine.connect() as connection:
        transaction = connection.begin()
        dependencies.runtime_repository.insert_outbox_event(
            transaction=connection,
            record=OutboxEventRecord(
                event_id="outbox_rolled_back",
                tenant_id="tenant-rolled-back",
                event_type="dataset.version.committed",
                aggregate_type="dataset_version",
                aggregate_id="dsv-rolled-back",
                payload={},
                status="pending",
                attempts=0,
                idempotency_key="outbox_rolled_back",
                correlation_id="req-rolled-back",
                created_at="2026-06-10T00:00:00Z",
                published_at=None,
            ),
        )
        transaction.rollback()

    assert "tenant-rolled-back" not in dependencies.metadata_repository.list_tenant_ids()


def _seeded_foundry(tmp_path: Path, *event_ids: str) -> tuple[FoundryLite, str, Path]:
    storage_root = tmp_path / "runtime"
    db_url = f"sqlite:///{tmp_path / 'runtime.db'}"
    dependencies = create_local_core_dependencies(db_url=db_url, storage_root=storage_root)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    for event_id in event_ids:
        _insert_outbox(dependencies, event_id=event_id, request_id=ctx.request_id)
    return foundry, db_url, storage_root


def _outbox_rows(foundry: FoundryLite, *, tenant_id: str = "tenant-demo") -> dict[str, dict[str, object]]:
    snapshot = foundry.operations.list_runs(ctx=_admin_context(tenant_id))
    rows = cast(list[dict[str, object]], snapshot["outboxEvents"])
    return {str(row["id"]): row for row in rows}


def _insert_outbox(
    dependencies: CoreDependencies,
    *,
    event_id: str,
    request_id: str,
    tenant_id: str = "tenant-demo",
) -> None:
    with dependencies.engine.begin() as transaction:
        dependencies.runtime_repository.insert_outbox_event(
            transaction=transaction,
            record=OutboxEventRecord(
                event_id=event_id,
                tenant_id=tenant_id,
                event_type="dataset.version.committed",
                aggregate_type="dataset_version",
                aggregate_id=f"dsv_{event_id}",
                payload={"versionId": f"dsv_{event_id}"},
                status="pending",
                attempts=0,
                idempotency_key=event_id,
                correlation_id=request_id,
                created_at="2026-06-10T00:00:00Z",
                published_at=None,
            ),
        )


def _admin_context(tenant_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        actor_user_id="user-demo-admin",
        roles=DEMO_ADMIN_ROLES,
    )
