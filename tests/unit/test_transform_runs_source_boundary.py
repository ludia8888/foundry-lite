from __future__ import annotations

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.transform_source_store import (
    TransformSourceArtifact,
    TransformSourceContent,
    TransformSourceRead,
    TransformSourceWrite,
)
from foundry_lite.application.services.transform_runs import _read_transform_source


class _TransformSourceStore:
    profile_name = "test-transform-source-store"

    def __init__(self, sources: dict[str, str]) -> None:
        self.sources = sources
        self.reads: list[TransformSourceRead] = []

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def write_source(self, request: TransformSourceWrite) -> TransformSourceArtifact:
        raise AssertionError(f"unexpected write for {request.api_name}")

    def read_source(self, request: TransformSourceRead) -> TransformSourceContent:
        self.reads.append(request)
        source_code = self.sources[request.entrypoint]
        return TransformSourceContent(source_code, "sha256:test", len(source_code.encode("utf-8")))


def test_sql_transform_source_is_read_through_storage_port() -> None:
    store = _TransformSourceStore({"artifact://clean.sql": "select * from input"})

    source = _read_transform_source(store, "artifact://clean.sql", "sql")

    assert source.sql_template == "select * from input"
    assert store.reads == [TransformSourceRead("artifact://clean.sql")]


def test_python_transform_function_suffix_is_not_sent_to_storage_port() -> None:
    store = _TransformSourceStore({"artifact://clean.py": "def clean(rows):\n    return rows\n"})

    source = _read_transform_source(store, "artifact://clean.py:clean", "python")

    assert source.python_function == "clean"
    assert source.python_source == "def clean(rows):\n    return rows\n"
    assert store.reads == [TransformSourceRead("artifact://clean.py")]
