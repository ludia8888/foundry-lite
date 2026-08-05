"""``source.virtual_table`` runtime: live-only contract, and no secret in the evidence.

Two production properties are pinned here.

A virtual table read is not reproducible — the external system owns its state and issues no
version we control. The runtime therefore refuses a contract that claims a pin, because a pin
here would tell replay and late-data paths a story that is not true.

And the pointer's config is caller-supplied and carries a secret *reference*, so it must not be
echoed wholesale into durable build evidence. Artifact refs and manifests outlive the run and
are read by operators; a connection string reaching them is a credential leak that no later
redaction can undo.
"""

from __future__ import annotations

import pytest
from foundry_lite.application.ports.virtual_table import (
    VirtualTableColumn,
    VirtualTablePredicate,
    VirtualTableQuery,
    VirtualTableReadResult,
    VirtualTableRecord,
    VirtualTableSchema,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeNode,
    PipelineV2SourceContract,
)
from foundry_lite.application.services.pipeline_v2_runtime_virtual_table import (
    PipelineV2VirtualTableRuntime,
)
from foundry_lite.domain.errors import NotFound, ValidationFailed

_SECRET_URL = "postgresql://user:SUPER-SECRET-PASSWORD@db.internal:5432/ongleam"


def _record() -> VirtualTableRecord:
    return VirtualTableRecord(
        rid="vt-1",
        tenant_id="tenant-demo",
        name="youtube_videos",
        parent_rid="folder-1",
        connection_rid="conn-1",
        # A reference, never the URL itself.
        config={"schema": "public", "table": "youtube_videos", "databaseUrlSecretRef": "src/ongleam/url"},
        schema=VirtualTableSchema(
            columns=(VirtualTableColumn("video_id", "text"), VirtualTableColumn("view_count", "integer"))
        ),
        markings=("pii",),
    )


class _Resolver:
    def __init__(self, record: VirtualTableRecord | None) -> None:
        self._record = record

    def resolve(self, *, tenant_id: str, rid: str) -> tuple[VirtualTableRecord, str] | None:
        return None if self._record is None else (self._record, _SECRET_URL)


class _Reader:
    def __init__(self, result: VirtualTableReadResult) -> None:
        self.result = result
        self.calls: list[VirtualTableQuery] = []

    def read(self, *, connection_url: str, config: object, query: VirtualTableQuery) -> VirtualTableReadResult:
        self.calls.append(query)
        return self.result


def _node(**config: object) -> PipelineV2RuntimeNode:
    return PipelineV2RuntimeNode(
        node_id="n-1",
        kind="source",
        descriptor_id="source.virtual_table",
        spec_version=1,
        runtime_capability="virtual_table_runtime",
        config=config,
    )


def _contract(**overrides: object) -> PipelineV2SourceContract:
    fields: dict[str, object] = {
        "node_id": "n-1",
        "descriptor_id": "source.virtual_table",
        "artifact_kind": "virtual_table",
        "resource_ref": "vt-1",
        "source_id": "src-1",
        "schema_contract": {},
        "schema_hash": "h-1",
        "schema_version": None,
        "version_pins": (),
        "security_envelope": {},
        "access_evidence": {},
        "is_live_source": True,
    }
    fields.update(overrides)
    return PipelineV2SourceContract(**fields)  # type: ignore[arg-type]


def _result(**overrides: object) -> VirtualTableReadResult:
    fields: dict[str, object] = {
        "rows": ({"video_id": "vid-1", "view_count": 10},),
        "pushed_down_predicates": (VirtualTablePredicate("view_count", "gt", 5),),
        "local_predicates": (),
        "network_evidence": {"rowsFetched": 1, "table": "public.youtube_videos"},
    }
    fields.update(overrides)
    return VirtualTableReadResult(**fields)  # type: ignore[arg-type]


def _runtime(record: VirtualTableRecord | None = None, reader: _Reader | None = None) -> PipelineV2VirtualTableRuntime:
    return PipelineV2VirtualTableRuntime(
        tenant_id="tenant-demo",
        resolver=_Resolver(record if record is not None else _record()),
        reader=reader if reader is not None else _Reader(_result()),
    )


def test_a_live_read_produces_rows_and_is_not_marked_serving() -> None:
    """The rows belong to the external system; there is no committed artifact of ours to serve."""
    artifact = _runtime().source_virtual_table(_node(), _contract())

    assert artifact.items == ({"video_id": "vid-1", "view_count": 10},)
    assert artifact.status == "LIVE"
    assert artifact.is_serving is False
    assert artifact.artifact_kind == "virtual_table"


def test_the_manifest_records_that_the_source_was_live() -> None:
    artifact = _runtime().source_virtual_table(_node(), _contract())

    assert artifact.manifest["isLiveSource"] is True
    assert artifact.manifest["virtualTableRid"] == "vt-1"


def test_push_down_evidence_travels_into_the_build_record() -> None:
    """A run that fell back to local filtering read more rows; that must stay visible."""
    reader = _Reader(
        _result(
            pushed_down_predicates=(VirtualTablePredicate("view_count", "gt", 5),),
            local_predicates=(VirtualTablePredicate("title", "contains", "리프팅"),),
        )
    )

    artifact = _runtime(reader=reader).source_virtual_table(_node(), _contract())

    assert artifact.manifest["hasFullPushDown"] is False
    assert artifact.manifest["pushedDownPredicates"] == [{"column": "view_count", "operator": "gt"}]
    assert artifact.manifest["localPredicates"] == [{"column": "title", "operator": "contains"}]


def test_no_secret_reaches_the_artifact_ref_or_manifest() -> None:
    """Build evidence outlives the run and is read by operators — a URL here is a leak."""
    artifact = _runtime().source_virtual_table(_node(), _contract())

    evidence = repr(artifact.artifact_ref) + repr(artifact.manifest) + repr(artifact.security_envelope)
    assert "SUPER-SECRET-PASSWORD" not in evidence
    assert "databaseUrlSecretRef" not in evidence, "even the reference key is not build evidence"
    assert artifact.artifact_ref["table"] == "youtube_videos"
    assert artifact.artifact_ref["connectionRid"] == "conn-1"


def test_markings_travel_as_the_security_envelope() -> None:
    artifact = _runtime().source_virtual_table(_node(), _contract())

    assert artifact.security_envelope == {"markings": ["pii"]}


def test_a_contract_that_is_not_declared_live_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="must be declared live"):
        _runtime().source_virtual_table(_node(), _contract(is_live_source=False, version_pins=()))


def test_a_contract_for_another_source_kind_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="different source"):
        _runtime().source_virtual_table(_node(), _contract(descriptor_id="source.dataset"))


def test_an_unregistered_pointer_fails_as_not_found() -> None:
    with pytest.raises(NotFound, match="not registered"):
        PipelineV2VirtualTableRuntime(
            tenant_id="tenant-demo", resolver=_Resolver(None), reader=_Reader(_result())
        ).source_virtual_table(_node(), _contract())


def test_the_read_is_projected_to_the_pinned_schema() -> None:
    reader = _Reader(_result())

    _runtime(reader=reader).source_virtual_table(_node(), _contract())

    assert reader.calls[0].projection == ("video_id", "view_count")


def test_plan_predicates_and_limit_reach_the_reader() -> None:
    reader = _Reader(_result())
    node = _node(predicates=[{"column": "sentiment", "operator": "eq", "value": "negative"}], limit=25)

    _runtime(reader=reader).source_virtual_table(node, _contract())

    query = reader.calls[0]
    assert query.limit == 25
    assert [predicate.column for predicate in query.predicates] == ["sentiment"]


@pytest.mark.parametrize("limit", [0, -1, "many", True, None])
def test_an_unusable_plan_limit_falls_back_to_a_bounded_default(limit: object) -> None:
    """A pointer at a large external table must never read without a bound."""
    reader = _Reader(_result())

    _runtime(reader=reader).source_virtual_table(_node(limit=limit), _contract())

    assert reader.calls[0].limit == 1000
