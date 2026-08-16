"""Strict and bounded Python Function OSDK runner boundaries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest
from foundry_lite.infrastructure.runners import python_function_osdk as osdk


class _Bridge:
    def __init__(self, *responses: Mapping[str, object]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def call(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.requests.append(dict(request))
        return self.responses.pop(0)


def test_query_bridge_uses_atomic_nonce_path_and_validates_response_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(osdk, "uuid4", lambda: SimpleNamespace(hex="request-a"))
    response = tmp_path / "response-nonce-a-request-a.json"
    response.write_text(json.dumps({"status": "succeeded", "result": {"items": []}}), encoding="utf-8")

    assert osdk.QueryBridge(tmp_path, "nonce-a", 1).call({"operation": "fetchPage"}) == {"items": []}
    request = tmp_path / "request-nonce-a-request-a.json"
    assert json.loads(request.read_text(encoding="utf-8")) == {"operation": "fetchPage"}
    assert not request.with_suffix(".tmp").exists()

    response.write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="query failed"):
        osdk.QueryBridge(tmp_path, "nonce-a", 1).call({})
    response.write_text(json.dumps({"status": "succeeded", "result": []}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid result"):
        osdk.QueryBridge(tmp_path, "nonce-a", 1).call({})


def test_query_bridge_times_out_and_refuses_non_json_numbers(tmp_path: Path) -> None:
    with pytest.raises(TimeoutError, match="timed out"):
        osdk.QueryBridge(tmp_path, "nonce", 0).call({"operation": "fetchPage"})
    with pytest.raises(ValueError, match="Out of range float values"):
        osdk.QueryBridge(tmp_path, "nonce", 1).call({"value": float("nan")})


@pytest.mark.parametrize(
    "config",
    [
        {"directory": "", "nonce": "n", "timeoutSeconds": 1},
        {"directory": "/tmp", "nonce": "", "timeoutSeconds": 1},
        {"directory": "/tmp", "nonce": "n", "timeoutSeconds": True},
        {"directory": "/tmp", "nonce": "n", "timeoutSeconds": 0},
        {"directory": "/tmp", "nonce": "n", "timeoutSeconds": float("nan")},
    ],
)
def test_configure_rejects_unbounded_or_ambiguous_bridge_manifests(config: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="queryBridge manifest is invalid"):
        osdk.configure({"queryBridge": config})
    osdk.configure({})


def test_object_and_property_expressions_preserve_osdk_mapping_semantics() -> None:
    table = osdk.OntologyObject({"objectId": "T-1", "properties": {"capacity": 4, "status": "FREE"}})
    assert table.capacity == 4
    assert table["status"] == "FREE"
    assert table["objectId"] == "T-1"
    assert set(table) == {"objectId", "properties", "capacity", "status"}
    assert len(table) == 4
    assert table.to_payload()["objectId"] == "T-1"
    with pytest.raises(AttributeError, match="missing"):
        _ = table.missing

    expression = osdk.PropertyExpression("capacity")
    assert expression == 4
    assert (expression > 4) == {"property": "capacity", "op": "gt", "value": 4}
    assert (expression >= 4) == {"property": "capacity", "op": "gte", "value": 4}
    assert (expression < 4) == {"property": "capacity", "op": "lt", "value": 4}
    assert (expression <= 4) == {"property": "capacity", "op": "lte", "value": 4}


def test_object_set_builds_exact_filter_order_descriptor_and_aggregate_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _Bridge({"groups": [], "totalGroups": 0})
    monkeypatch.setattr(osdk, "_bridge", bridge)
    object_set = (
        osdk.FoundryClient()
        .ontology.objects.DiningTable.where(status={"$eq": "FREE"})
        .where(osdk.PropertyExpression("capacity") >= 4)
        .order_by_fields(capacity="desc")
    )
    result = object_set.aggregate(group_by=["status"], select=[{"name": "count", "function": "count"}])

    assert result == {"groups": [], "totalGroups": 0}
    assert bridge.requests[0] == {
        "operation": "aggregate",
        "objectType": "DiningTable",
        "filter": {
            "and": [
                {"property": "status", "op": "eq", "value": "FREE"},
                {"property": "capacity", "op": "gte", "value": 4},
            ]
        },
        "orderBy": [{"property": "capacity", "direction": "desc"}],
        "groupBy": ["status"],
        "select": [{"name": "count", "function": "count"}],
    }
    assert object_set.to_descriptor()["$foundryObjectSet"]["objectType"] == "DiningTable"  # type: ignore[index]


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda: osdk.ObjectSet("T").where({}), "must not be empty"),
        (lambda: osdk.ObjectSet("T").where(status={"$eq": "A", "$gt": "B"}), "one operator"),
        (lambda: osdk.ObjectSet("T").where(status={"$unknown": "A"}), "unsupported"),
        (lambda: osdk.ObjectSet("T").order_by_fields(), "non-empty"),
        (lambda: osdk.ObjectSet("T").order_by_fields(name="sideways"), "asc or desc"),
    ],
)
def test_object_set_authoring_mistakes_fail_before_bridge_io(operation: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        operation()  # type: ignore[operator]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"items": "invalid"}, "did not contain items"),
        ({"items": [{"objectId": "T-1"}, 7]}, "invalid item"),
        ({"items": [], "nextCursor": 7}, "invalid cursor"),
    ],
)
def test_object_set_pages_reject_partial_or_ambiguous_results(
    response: Mapping[str, object],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(osdk, "_bridge", _Bridge(response))
    with pytest.raises(RuntimeError, match=message):
        osdk.ObjectSet("DiningTable").fetch_page()


def test_object_set_all_rejects_repeated_cursor_and_enforces_page_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated = _Bridge({"items": [], "nextCursor": "same"}, {"items": [], "nextCursor": "same"})
    monkeypatch.setattr(osdk, "_bridge", repeated)
    with pytest.raises(RuntimeError, match="repeated a cursor"):
        osdk.ObjectSet("DiningTable").all()

    monkeypatch.setattr(osdk, "_MAX_OBJECT_SET_PAGES", 2)
    unbounded = _Bridge({"items": [], "nextCursor": "one"}, {"items": [], "nextCursor": "two"})
    monkeypatch.setattr(osdk, "_bridge", unbounded)
    with pytest.raises(RuntimeError, match="page limit"):
        osdk.ObjectSet("DiningTable").all()


@pytest.mark.parametrize(
    "descriptor",
    [
        {"objectType": "", "filter": None, "orderBy": []},
        {"objectType": "T", "filter": [], "orderBy": []},
        {"objectType": "T", "filter": None, "orderBy": "name"},
        {"objectType": "T", "filter": None, "orderBy": [{}]},
        {"objectType": "T", "filter": None, "orderBy": [{"property": "name", "direction": "sideways"}]},
    ],
)
def test_wrapped_object_set_descriptors_are_strict(descriptor: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="ObjectSet"):
        osdk.wrap_inputs({"tables": {"$foundryObjectSet": descriptor}})


def test_import_compatibility_and_output_serialization_are_recursive() -> None:
    osdk.install_import_compatibility()
    from functions.api import Double, Float, Integer, Long, function  # type: ignore[import-not-found]
    from ontology_sdk.ontology.object_sets import DiningTableObjectSet  # type: ignore[import-not-found]
    from ontology_sdk.ontology.objects import DiningTable  # type: ignore[import-not-found]

    assert (Integer, Long, Float, Double) == (int, int, float, float)
    assert function(lambda: 1)() == 1
    assert (DiningTable.object_type.capacity > 4) == {"property": "capacity", "op": "gt", "value": 4}
    assert DiningTableObjectSet is osdk.ObjectSet
    assert osdk.serialize_output(
        {"sets": [osdk.ObjectSet("DiningTable")], "object": osdk.OntologyObject({"objectId": "T-1"})}
    ) == {
        "sets": [{"$foundryObjectSet": {"objectType": "DiningTable", "filter": None, "orderBy": []}}],
        "object": {"objectId": "T-1"},
    }
