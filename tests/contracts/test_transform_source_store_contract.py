"""Contract tests for transform source storage adapters."""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.transform_source_store import TransformSourceRead, TransformSourceWrite
from foundry_lite.infrastructure.adapters.local_transform_source_store import LocalTransformSourceStore


def test_local_transform_source_store_writes_tenant_scoped_source_atomically(tmp_path: Path) -> None:
    store = LocalTransformSourceStore(tmp_path)
    request = TransformSourceWrite(
        tenant_id="tenant-demo",
        api_name="clean_rows",
        language="python",
        source_code="def transform(rows):\n    return rows\n",
    )

    first = store.write_source(request)
    second = store.write_source(request)

    path = Path(first.entrypoint)
    assert first == second
    assert path.parent.name == "tenant-demo"
    assert path.name.startswith("clean_rows-")
    assert path.suffix == ".py"
    assert path.read_text(encoding="utf-8") == request.source_code
    assert store.read_source(TransformSourceRead(first.entrypoint)).source_code == request.source_code
    assert list(path.parent.glob("*.tmp")) == []


def test_local_transform_source_store_replaces_existing_source_without_partial_file(tmp_path: Path) -> None:
    store = LocalTransformSourceStore(tmp_path)
    original = TransformSourceWrite("tenant-demo", "clean_rows", "sql", "select 1")
    replacement = TransformSourceWrite("tenant-demo", "clean_rows", "sql", "select 2")

    first = store.write_source(original)
    second = store.write_source(replacement)

    assert first.entrypoint == second.entrypoint
    assert first.content_hash != second.content_hash
    assert Path(second.entrypoint).read_text(encoding="utf-8") == "select 2"


def test_local_transform_source_store_sanitizes_path_tokens_without_escaping_root(tmp_path: Path) -> None:
    store = LocalTransformSourceStore(tmp_path)

    artifact = store.write_source(TransformSourceWrite("../other-tenant", "../clean_rows", "sql", "select 1"))

    path = Path(artifact.entrypoint)
    assert path.is_relative_to(tmp_path.resolve())
    assert ".." not in path.relative_to(tmp_path.resolve()).parts


def test_local_transform_source_store_declares_operator_safe_failure_contract(tmp_path: Path) -> None:
    contract = LocalTransformSourceStore(tmp_path).failure_contract()

    assert contract.adapter_profile == "local-transform-source-store"
    assert [(mode.operation, mode.kind, mode.is_retryable) for mode in contract.modes] == [
        ("write_source", "unavailable", True),
        ("read_source", "not_found", False),
        ("read_source", "validation", False),
        ("read_source", "unavailable", True),
    ]


def test_local_transform_source_store_reports_missing_source_without_leaking_path(tmp_path: Path) -> None:
    store = LocalTransformSourceStore(tmp_path)

    with pytest.raises(AdapterError) as captured:
        store.read_source(TransformSourceRead(str(tmp_path / "secret-name.sql")))

    assert captured.value.failure.operation == "read_source"
    assert captured.value.failure.kind == "not_found"
    assert captured.value.failure.is_retryable is False
    assert "secret-name" not in str(captured.value.details)


def test_local_transform_source_store_rejects_non_utf8_source(tmp_path: Path) -> None:
    path = tmp_path / "invalid.sql"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(AdapterError) as captured:
        LocalTransformSourceStore(tmp_path).read_source(TransformSourceRead(str(path)))

    assert captured.value.failure.kind == "validation"
    assert captured.value.failure.is_retryable is False
