from __future__ import annotations

from pathlib import Path

from foundry_lite_worker import outbox_publisher, source_scheduler


def test_outbox_worker_uses_runtime_composition_root(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    dependencies = object()
    monkeypatch.setattr(
        outbox_publisher,
        "create_runtime_core_dependencies",
        lambda **kwargs: captured.append(kwargs) or dependencies,
    )
    monkeypatch.setattr(
        outbox_publisher,
        "FoundryLite",
        lambda *, dependencies, should_initialize_schema: dependencies if not should_initialize_schema else None,
    )

    result = outbox_publisher._build_foundry(
        outbox_publisher.OutboxPublisherWorkerConfig(
            storage_root=tmp_path,
            db_url="postgresql+psycopg://postgres:secret@postgresql/foundry_lite",
            adapter_profile="s3-storage",
        )
    )

    assert result is dependencies
    assert captured == [
        {
            "db_url": "postgresql+psycopg://postgres:secret@postgresql/foundry_lite",
            "storage_root": tmp_path,
            "adapter_profile": "s3-storage",
        }
    ]


def test_source_scheduler_uses_runtime_composition_root(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    dependencies = object()
    monkeypatch.setattr(
        source_scheduler,
        "create_runtime_core_dependencies",
        lambda **kwargs: captured.append(kwargs) or dependencies,
    )
    monkeypatch.setattr(
        source_scheduler,
        "FoundryLite",
        lambda *, dependencies, should_initialize_schema: dependencies if not should_initialize_schema else None,
    )

    result = source_scheduler._build_foundry(
        source_scheduler.SourceSchedulerWorkerConfig(
            storage_root=tmp_path,
            db_url="postgresql+psycopg://postgres:secret@postgresql/foundry_lite",
            adapter_profile="s3-storage",
        )
    )

    assert result is dependencies
    assert captured == [
        {
            "db_url": "postgresql+psycopg://postgres:secret@postgresql/foundry_lite",
            "storage_root": tmp_path,
            "adapter_profile": "s3-storage",
        }
    ]
