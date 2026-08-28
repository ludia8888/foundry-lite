"""Contract tests for OSDK release artifact stores."""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.osdk_release_artifact_store import OsdkReleaseArtifactWrite
from foundry_lite.infrastructure.adapters.local_osdk_release_artifact_store import LocalOsdkReleaseArtifactStore


def _write_request(**overrides: object) -> OsdkReleaseArtifactWrite:
    return OsdkReleaseArtifactWrite(
        tenant_id=str(overrides.get("tenant_id", "tenant-demo")),
        app_id=str(overrides.get("app_id", "osdk_app_orders")),
        version=str(overrides.get("version", "0.1.0")),
        file_name=str(overrides.get("file_name", "orders-osdk-0.1.0.zip")),
        content=b"deterministic-package",
    )


def test_local_osdk_release_artifact_store_writes_and_reads_deterministic_artifact(tmp_path: Path) -> None:
    store = LocalOsdkReleaseArtifactStore(tmp_path)

    first = store.write_artifact(_write_request())
    second = store.write_artifact(_write_request())
    content = store.read_artifact(first.storage_uri)

    assert first == second
    assert content.content == b"deterministic-package"
    assert content.content_hash == first.content_hash
    assert Path(first.storage_uri).is_relative_to(tmp_path.resolve())
    assert list(Path(first.storage_uri).parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("tenant_id", "../other"), ("app_id", "app/other"), ("version", ".."), ("file_name", "../x.zip")],
)
def test_local_osdk_release_artifact_store_rejects_path_escape(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    store = LocalOsdkReleaseArtifactStore(tmp_path)

    with pytest.raises(AdapterError) as captured:
        store.write_artifact(_write_request(**{field: value}))

    assert captured.value.failure.kind == "validation"
    assert captured.value.failure.is_retryable is False


def test_local_osdk_release_artifact_store_rejects_read_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-osdk.zip"
    outside.write_bytes(b"outside")

    with pytest.raises(AdapterError) as captured:
        LocalOsdkReleaseArtifactStore(tmp_path).read_artifact(str(outside))

    assert captured.value.failure.kind == "validation"
    assert "outside-osdk" not in str(captured.value.details)


def test_local_osdk_release_artifact_store_reports_missing_artifact(tmp_path: Path) -> None:
    missing = tmp_path / "tenant-demo" / "app" / "0.1.0" / "missing.zip"

    with pytest.raises(AdapterError) as captured:
        LocalOsdkReleaseArtifactStore(tmp_path).read_artifact(str(missing))

    assert captured.value.failure.kind == "not_found"
    assert captured.value.failure.is_retryable is False


def test_local_osdk_release_artifact_store_declares_failure_contract(tmp_path: Path) -> None:
    contract = LocalOsdkReleaseArtifactStore(tmp_path).failure_contract()

    assert contract.adapter_profile == "local-osdk-release-artifact-store"
    assert [(mode.operation, mode.kind, mode.is_retryable) for mode in contract.modes] == [
        ("write_artifact", "validation", False),
        ("write_artifact", "unavailable", True),
        ("read_artifact", "validation", False),
        ("read_artifact", "not_found", False),
        ("read_artifact", "unavailable", True),
    ]
