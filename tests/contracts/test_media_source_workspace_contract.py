from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_source_workspace import MediaSourceWorkspaceRequest
from foundry_lite.infrastructure.adapters.local_media_source_workspace import LocalMediaSourceWorkspace


def test_local_media_source_workspace_materializes_and_removes_bytes(tmp_path) -> None:
    workspace = LocalMediaSourceWorkspace(tmp_path)
    observed_path: Path | None = None

    with workspace.materialize(MediaSourceWorkspaceRequest("source.pdf"), lambda sink: sink.write(b"pdf")) as source:
        observed_path = Path(source.source_path)
        assert observed_path.read_bytes() == b"pdf"

    assert observed_path is not None
    assert not observed_path.exists()
    assert list((tmp_path / "media-source-workspaces").iterdir()) == []


def test_local_media_source_workspace_cleans_up_when_consumer_fails(tmp_path) -> None:
    workspace = LocalMediaSourceWorkspace(tmp_path)

    with pytest.raises(_ConsumerFailure):
        with workspace.materialize(MediaSourceWorkspaceRequest("source.bin"), lambda sink: sink.write(b"data")):
            raise _ConsumerFailure

    assert list((tmp_path / "media-source-workspaces").iterdir()) == []


def test_local_media_source_workspace_preserves_writer_failure_and_cleans_up(tmp_path) -> None:
    workspace = LocalMediaSourceWorkspace(tmp_path)

    def fail_write(_sink) -> None:
        raise _WriterFailure

    with pytest.raises(_WriterFailure):
        with workspace.materialize(MediaSourceWorkspaceRequest("source.bin"), fail_write):
            pytest.fail("writer failure must prevent workspace consumption")

    assert list((tmp_path / "media-source-workspaces").iterdir()) == []


def test_local_media_source_workspace_rejects_escaping_file_name(tmp_path) -> None:
    workspace = LocalMediaSourceWorkspace(tmp_path)

    with pytest.raises(AdapterError) as raised:
        with workspace.materialize(MediaSourceWorkspaceRequest("../source.pdf"), lambda sink: None):
            pytest.fail("invalid workspace path must not be materialized")

    assert raised.value.failure.kind == "validation"
    assert not (tmp_path / "media-source-workspaces").exists()
    assert {mode.kind for mode in workspace.failure_contract().modes} == {"validation", "unavailable"}


class _ConsumerFailure(Exception):
    pass


class _WriterFailure(Exception):
    pass
