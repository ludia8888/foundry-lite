from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord, MediaSetSelectionRecord
from foundry_lite.application.services.pipeline_graph_contracts import PipelineV2Node
from foundry_lite.application.services.pipeline_preview_errors import PipelineCodeIsolationRequired
from foundry_lite.application.services.pipeline_preview_runtime import (
    PipelinePreviewRuntime,
    _dataset_security_envelope,
    _media_item_payload,
    _media_set_ref_parts,
    _require_exact_selection,
    _require_model_ref,
    _require_total_bytes,
    _selection_coordinates,
    _selection_path_prefix,
    _selection_version_ids,
    _version_with_source_security,
)
from foundry_lite.application.services.pipeline_preview_structured import (
    output_geospatial,
    source_geospatial,
    source_stream,
)
from foundry_lite.application.services.pipeline_preview_transforms import (
    _processor_parameters,
    bounded_int,
    bridge_or_output,
    chunk,
    document_extract,
    embed_text,
    join,
    limit_value,
    media_to_table_rows,
    object_items,
    process_media,
    required_positive_int,
    required_text,
    select_cast,
    single_input,
    source_dataset,
    source_media,
    string_list,
    union,
    unsupported_code_preview,
)
from foundry_lite.domain.errors import ValidationFailed

_LIMITS: dict[str, object] = {
    "tableRows": 3,
    "mediaItems": 2,
    "totalBytes": 1000,
    "pdfPages": 2,
    "audioVideoSeconds": 10,
    "sceneCount": 2,
}


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def dataset_rows(self, dataset_ref: str, *, limit: int) -> list[dict[str, object]]:
        self.calls.append(("dataset", (dataset_ref, limit)))
        if dataset_ref == "raw.locations":
            return [
                {
                    "id": 1,
                    "geometry": {"type": "Point", "coordinates": [127.0, 37.5]},
                }
            ]
        return [{"id": 1}]

    def stream_rows(self, source_ref: str, *, limit: int) -> list[dict[str, object]]:
        self.calls.append(("stream", (source_ref, limit)))
        return [{"offset": 1}]

    def media_selection(
        self,
        media_set_ref: str,
        *,
        selection: dict[str, object],
        item_limit: int,
        total_byte_limit: int,
    ) -> list[dict[str, object]]:
        self.calls.append(("media", (media_set_ref, selection, item_limit, total_byte_limit)))
        return [{"mediaItemVersionId": "mv-1"}]

    def process_media(
        self,
        items: list[dict[str, object]],
        *,
        processor_id: str,
        parameters: dict[str, object],
    ) -> list[dict[str, object]]:
        self.calls.append(("process", (items, processor_id, parameters)))
        return [{"units": [{"text": "one"}, "invalid", {"text": "two"}]}]

    def embed_content(
        self,
        units: list[dict[str, object]],
        *,
        model_ref: str,
    ) -> list[dict[str, object]]:
        self.calls.append(("embed", (units, model_ref)))
        return [{**unit, "embedding": [1.0]} for unit in units]


def _runtime() -> PipelinePreviewRuntime:
    return cast(PipelinePreviewRuntime, _Runtime())


def _node(descriptor_id: str, **config: object) -> PipelineV2Node:
    return {
        "id": "node-a",
        "kind": "transform",
        "descriptorId": descriptor_id,
        "specVersion": 1,
        "config": dict(config),
    }


def _media_version(**overrides: object) -> MediaItemVersionRecord:
    values: dict[str, object] = {
        "media_item_version_id": "mv-1",
        "tenant_id": "tenant-demo",
        "media_item_id": "mi-1",
        "media_transaction_id": "mtx-1",
        "version_number": 1,
        "blob_key": "docs/report.pdf",
        "content_hash": "a" * 64,
        "byte_size": 12,
        "supplied_mime_type": "application/pdf",
        "sniffed_mime_type": "application/pdf",
        "schema_type": "document",
        "format": "pdf",
        "probe_metadata": {},
        "security_envelope": {"classification": "internal"},
        "source_ref": {"page": 1},
        "status": "COMMITTED",
        "created_at": "2026-07-28T00:00:00Z",
    }
    values.update(overrides)
    return MediaItemVersionRecord(**values)  # type: ignore[arg-type]


def test_source_handlers_apply_limits_and_selection_compatibility() -> None:
    runtime = _runtime()
    dataset = _node("source.dataset", datasetRef="raw.orders")
    media = _node("source.media_set", mediaSetRef="media.docs", mediaItemVersionIds=["mv-1"])

    assert source_dataset(dataset, {}, _LIMITS, runtime) == [{"id": 1}]
    assert source_media(media, {}, _LIMITS, runtime) == [{"mediaItemVersionId": "mv-1"}]
    typed_runtime = cast(_Runtime, runtime)
    assert typed_runtime.calls == [
        ("dataset", ("raw.orders", 3)),
        ("media", ("media.docs", {"mode": "version_ids", "versionIds": ["mv-1"]}, 2, 1000)),
    ]

    media["config"]["selection"] = {"mode": "path_prefix", "pathPrefix": "invoices/"}
    source_media(media, {}, _LIMITS, runtime)
    assert cast(tuple[object, ...], typed_runtime.calls[-1][1])[1] == {
        "mode": "path_prefix",
        "pathPrefix": "invoices/",
    }


def test_select_cast_preserves_security_and_covers_supported_scalar_casts() -> None:
    node = _node(
        "transform.select_cast",
        columns=[
            {"source": "number", "name": "as_string", "type": "string"},
            {"source": "number", "name": "as_int", "type": "integer"},
            {"source": "number", "name": "as_float", "type": "double"},
            {"source": "truth", "name": "as_bool", "type": "boolean"},
            {"source": "missing", "name": "as_none", "type": "string"},
            {"source": "number", "name": "unchanged", "type": "custom"},
        ],
    )
    inputs = {"input": [{"number": "2", "truth": "yes", "securityEnvelope": {"classification": "internal"}}]}

    assert select_cast(node, inputs, _LIMITS, _runtime()) == [
        {
            "as_string": "2",
            "as_int": 2,
            "as_float": 2.0,
            "as_bool": True,
            "as_none": None,
            "unchanged": "2",
            "securityEnvelope": {"classification": "internal"},
        }
    ]

    for value, expected in ((True, True), ("no", False), ("1", True), ("0", False)):
        node["config"]["columns"] = [{"source": "value", "name": "value", "type": "bool"}]
        assert select_cast(node, {"input": [{"value": value}]}, _LIMITS, _runtime()) == [{"value": expected}]


def test_preview_transform_validation_fails_closed_on_bad_shapes() -> None:
    node = _node("transform.select_cast")
    failures = (
        lambda: select_cast(node, {"input": [{}]}, _LIMITS, _runtime()),
        lambda: select_cast(
            _node("transform.select_cast", columns=["bad"]),
            {"input": [{}]},
            _LIMITS,
            _runtime(),
        ),
        lambda: select_cast(
            _node("transform.select_cast", columns=[{"name": "value", "type": "bool"}]),
            {"input": [{"value": "maybe"}]},
            _LIMITS,
            _runtime(),
        ),
        lambda: single_input({}, node),
        lambda: string_list([], "ids", node),
        lambda: required_text({}, "field", node),
        lambda: required_positive_int(True, "count", node),
        lambda: bounded_int("3", 1, 1, 5),
        lambda: limit_value({}, "tableRows"),
        lambda: _processor_parameters(
            {"processingBounds": "bad"},
            _LIMITS,
            processor_id="asr_v1@1",
        ),
    )

    for failure in failures:
        with pytest.raises(ValidationFailed):
            failure()


def test_union_join_and_output_cover_inner_and_outer_row_semantics() -> None:
    node = _node("transform.join", leftKey="id", rightKey="key", joinType="inner")
    inputs = {
        "left": [{"id": 1, "left": "a"}, {"id": 2, "left": "b"}],
        "right": [{"key": 1, "right": "x"}, {"key": 3, "right": "y"}],
    }

    assert join(node, inputs, _LIMITS, _runtime()) == [{"id": 1, "left": "a", "key": 1, "right": "x"}]
    node["config"]["joinType"] = "left"
    assert join(node, inputs, _LIMITS, _runtime())[-1] == {"id": 2, "left": "b"}
    node["config"]["joinType"] = "right"
    assert join(node, inputs, _LIMITS, _runtime())[-1] == {"key": 3, "right": "y"}
    node["config"]["joinType"] = "full outer"
    assert len(join(node, inputs, _LIMITS, _runtime())) == 3
    assert union(node, inputs, {"tableRows": 2}, _runtime()) == inputs["left"]
    assert bridge_or_output(node, {"input": inputs["left"]}, {"tableRows": 1}, _runtime()) == [inputs["left"][0]]


def test_media_document_embedding_and_chunk_handlers_preserve_evidence() -> None:
    runtime = _runtime()
    media_node = _node(
        "transform.document_extract",
        processorId="pdf_text_v1@1",
        parameters={"pageSelection": {"start": 2, "limit": 99}},
    )
    inputs = {"media": [{"mediaItemVersionId": "mv-1"}]}

    assert process_media(media_node, inputs, _LIMITS, runtime)[0]["units"]
    assert document_extract(media_node, inputs, _LIMITS, runtime) == [{"text": "one"}, {"text": "two"}]
    process_call = cast(tuple[object, ...], cast(_Runtime, runtime).calls[-1][1])
    assert cast(dict[str, object], process_call[2])["pageSelection"] == {"start": 2, "limit": 2}

    embed_node = _node("transform.embedding.text", modelRef="embed-v1")
    assert embed_text(embed_node, {"input": [{"text": "one"}]}, _LIMITS, runtime) == [
        {"text": "one", "embedding": [1.0]}
    ]

    chunk_node = _node("transform.chunk", chunkSize=3, overlap=1)
    chunks = chunk(chunk_node, {"input": [{"text": "one two three four five"}]}, _LIMITS, runtime)
    assert [item["text"] for item in chunks] == ["one two three", "three four five"]
    assert chunk(chunk_node, {"input": [{"text": ""}]}, _LIMITS, runtime) == []


def test_media_reference_projection_and_code_preview_isolation() -> None:
    node = _node("bridge.media_to_table_rows")
    item = {
        "mediaItemVersionId": "mv-1",
        "mimeType": "application/pdf",
        "contentHash": "a" * 64,
        "sourceLocator": {"page": 1},
        "securityEnvelope": {"classification": "confidential"},
    }

    assert media_to_table_rows(node, {"input": [item]}, _LIMITS, _runtime()) == [
        {
            "mediaReference": {
                "mediaItemVersionId": "mv-1",
                "mimeType": "application/pdf",
                "contentHash": "a" * 64,
                "sourceLocator": {"page": 1},
            },
            "mediaItemVersionId": "mv-1",
            "securityEnvelope": {"classification": "confidential"},
        }
    ]
    assert object_items([{"ok": True}, "bad"]) == [{"ok": True}]
    assert object_items("bad") == []

    with pytest.raises(PipelineCodeIsolationRequired) as raised:
        unsupported_code_preview(_node("transform.python"), {}, _LIMITS, _runtime())
    assert raised.value.details["executionAttempted"] is False


def test_structured_stream_and_geospatial_previews_validate_rows_without_commits() -> None:
    runtime = _runtime()
    stream_node = _node("source.stream", sourceRef="managed.orders")
    geo_source = _node(
        "source.geospatial",
        resourceRef="raw.locations",
        geometryField="geometry",
    )
    geo_output = _node("output.geospatial", geometryField="geometry")
    geo_rows = [
        {
            "id": 1,
            "geometry": {"type": "Point", "coordinates": [127.0, 37.5]},
        }
    ]

    assert source_stream(stream_node, {}, {"tableRows": True}, runtime) == [{"offset": 1}]
    assert source_geospatial(geo_source, {}, _LIMITS, runtime) == geo_rows
    assert output_geospatial(geo_output, {"input": geo_rows}, _LIMITS, runtime) == geo_rows
    assert cast(_Runtime, runtime).calls[:2] == [
        ("stream", ("managed.orders", 50)),
        ("dataset", ("raw.locations", 3)),
    ]

    with pytest.raises(ValidationFailed, match="exactly one"):
        output_geospatial(geo_output, {}, _LIMITS, runtime)
    with pytest.raises(ValidationFailed, match="config is required"):
        source_stream(_node("source.stream"), {}, _LIMITS, runtime)
    with pytest.raises(ValidationFailed, match="spatial encoding"):
        output_geospatial(
            geo_output,
            {"input": [{"geometry": {"type": "Point"}}]},
            _LIMITS,
            runtime,
        )


def test_preview_runtime_selection_and_security_helpers_fail_closed() -> None:
    version = _media_version()
    selection = MediaSetSelectionRecord(
        media_set_id="ms-1",
        logical_path="reports/one.pdf",
        version=version,
    )

    secured = _version_with_source_security(
        version,
        {"securityEnvelope": {"classification": "restricted", "hasLegalHold": True}},
    )
    assert secured.security_envelope["classification"] == "restricted"
    assert secured.has_legal_hold is True
    assert _media_item_payload(selection, "docs.reports", {"classification": "internal"})["sourceLocator"] == {
        "mediaSetRef": "docs.reports",
        "logicalPath": "reports/one.pdf",
        "page": 1,
    }
    assert (
        _dataset_security_envelope(
            {"id": "ds-1", "classification": None},  # type: ignore[typeddict-item]
            SimpleNamespace(tenant_id="tenant-demo"),  # type: ignore[arg-type]
        )["classification"]
        == "public"
    )

    with pytest.raises(ValidationFailed, match="security envelope"):
        _version_with_source_security(version, {})
    with pytest.raises(ValidationFailed, match="byte budget"):
        _require_total_bytes([replace(version, byte_size=13)], 12)


def test_preview_runtime_selection_coordinates_are_bounded_and_exact() -> None:
    assert _selection_coordinates({}, 3) == (None, None)
    assert _selection_coordinates({"logicalPathPrefix": " reports/ "}, 3) == (None, "reports/")
    assert _selection_coordinates(
        {"mode": "version_ids", "versionIds": [" mv-1 ", "mv-1", "mv-2"]},
        1,
    ) == (["mv-1"], None)
    assert _selection_version_ids(["mv-1", "mv-1", "mv-2"], 2) == ["mv-1", "mv-2"]
    assert _selection_path_prefix(None) is None
    assert _media_set_ref_parts("docs.reports") == ("docs", "reports")

    failures = (
        lambda: _selection_coordinates({"mode": "path_prefix"}, 1),
        lambda: _selection_path_prefix(" "),
        lambda: _selection_path_prefix(3),
        lambda: _selection_version_ids([], 1),
        lambda: _selection_version_ids([""], 1),
        lambda: _selection_version_ids([1], 1),
        lambda: _media_set_ref_parts("reports"),
        lambda: _media_set_ref_parts(".reports"),
        lambda: _media_set_ref_parts("docs."),
    )
    for failure in failures:
        with pytest.raises(ValidationFailed):
            failure()

    selected = [
        MediaSetSelectionRecord(
            media_set_id="ms-1",
            logical_path="one.pdf",
            version=_media_version(),
        )
    ]
    _require_exact_selection(None, selected, "docs.reports")
    _require_exact_selection(["mv-1"], selected, "docs.reports")
    with pytest.raises(Exception, match="outside the committed Media Set"):
        _require_exact_selection(["mv-missing"], selected, "docs.reports")


def test_preview_runtime_requires_available_pinned_embedding_model() -> None:
    available = SimpleNamespace(is_available=True, model_version="embed-v1")
    _require_model_ref(available, "embed-v1")  # type: ignore[arg-type]

    with pytest.raises(ValidationFailed, match="unavailable"):
        _require_model_ref(SimpleNamespace(is_available=False, model_version="embed-v1"), "embed-v1")  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed, match="does not match"):
        _require_model_ref(available, "embed-v2")  # type: ignore[arg-type]
