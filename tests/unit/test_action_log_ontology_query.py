"""Action Log ontology query: keyset paging, ordering, and filter validation.

The Action Log is served as an ontology object type, so it inherits the keyset-cursor contract:
a page is anchored to the last row's ordering values, and a cursor whose anchor row moved must
be rejected rather than silently resumed at the wrong offset — otherwise an operator scrolling
an audit log skips or repeats entries without any signal.

Structured properties (`parameters`, `result`, `editedObjects`) are the other sharp edge. They
are JSON blobs, so ordering or grouping by them is meaningless and equality filters on them
would push an unindexable predicate into SQL. Both are refused here.
"""

from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports.object_read_repository import ObjectOrderBy, ObjectQueryItem
from foundry_lite.application.services.action_log_ontology_query import (
    _compare_values,
    filtered_sorted_logs,
    log_cursor_page,
    normalized_log_order,
    validate_log_filter,
    validate_log_group_by,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

_CTX = RequestContext(roles=("admin",))


def _item(object_id: str, **properties: object) -> ObjectQueryItem:
    return cast(ObjectQueryItem, {"objectId": object_id, "properties": dict(properties)})


def _order(prop: str, direction: str = "asc") -> ObjectOrderBy:
    return cast(ObjectOrderBy, {"property": prop, "direction": direction})


def _page(items: list[ObjectQueryItem], *, cursor: str | None, limit: int) -> tuple[list[ObjectQueryItem], str | None]:
    return log_cursor_page(
        items,
        ctx=_CTX,
        object_type="ActionLog",
        order_by=[_order("actionRunId")],
        filter_ast=None,
        search_text=None,
        cursor=cursor,
        limit=limit,
    )


# --- ordering -----------------------------------------------------------------------


def test_order_defaults_to_ascending_when_direction_is_omitted() -> None:
    assert normalized_log_order([{"property": "actionRunId"}]) == [{"property": "actionRunId", "direction": "asc"}]


def test_order_by_an_unknown_property_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="orderBy property is unknown"):
        normalized_log_order([{"property": "ghost"}])


@pytest.mark.parametrize("prop", ["parameters", "result", "editedObjects"])
def test_order_by_a_structured_property_is_refused(prop: str) -> None:
    """Ordering a JSON blob has no meaning the caller could rely on."""
    with pytest.raises(ValidationFailed, match="cannot order by a structured property"):
        normalized_log_order([{"property": prop}])


def test_order_direction_must_be_asc_or_desc() -> None:
    with pytest.raises(ValidationFailed, match="asc or desc"):
        normalized_log_order([{"property": "actionRunId", "direction": "sideways"}])


def test_descending_order_reverses_the_comparison() -> None:
    items = [_item("a", actionRunId="r-1"), _item("b", actionRunId="r-3"), _item("c", actionRunId="r-2")]

    ordered = filtered_sorted_logs(items, None, [_order("actionRunId", "desc")], None)

    assert [item["objectId"] for item in ordered] == ["b", "c", "a"]


def test_ties_break_on_object_id_so_paging_is_stable() -> None:
    """Without a total order the same row can appear on two pages."""
    items = [_item("c", status="OK"), _item("a", status="OK"), _item("b", status="OK")]

    ordered = filtered_sorted_logs(items, None, [_order("status")], None)

    assert [item["objectId"] for item in ordered] == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (None, None, 0),
        (None, "x", -1),
        ("x", None, 1),
        ("a", "b", -1),
        (2, 10, -1),
        (2.5, 2.5, 0),
        (True, False, 1),
        ({"a": 1}, {"a": 2}, -1),
    ],
)
def test_value_comparison_orders_mixed_types_deterministically(left: object, right: object, expected: int) -> None:
    assert _compare_values(left, right) == expected


# --- search and filtering -----------------------------------------------------------


def test_search_text_matches_anywhere_in_the_serialized_properties() -> None:
    items = [_item("a", result={"note": "shipment delayed"}), _item("b", result={"note": "ok"})]

    found = filtered_sorted_logs(items, None, [_order("actionRunId")], "DELAYED")

    assert [item["objectId"] for item in found] == ["a"]


def test_filter_on_an_unknown_property_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="unknown properties"):
        validate_log_filter({"op": "eq", "property": "ghost", "value": 1})


@pytest.mark.parametrize("prop", ["parameters", "result", "editedObjects"])
def test_structured_properties_accept_contains_only(prop: str) -> None:
    validate_log_filter({"op": "contains", "property": prop, "value": "x"})

    with pytest.raises(ValidationFailed, match="contains filters only"):
        validate_log_filter({"op": "eq", "property": prop, "value": "x"})


def test_structured_property_rule_reaches_inside_logical_groups() -> None:
    with pytest.raises(ValidationFailed, match="contains filters only"):
        validate_log_filter({"and": [{"op": "eq", "property": "result", "value": "x"}]})


def test_group_by_a_structured_property_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="cannot group by structured properties"):
        validate_log_group_by(["result"])


def test_group_by_a_scalar_property_is_allowed() -> None:
    validate_log_group_by(["actionRunId"])
    validate_log_group_by(None)


# --- keyset paging ------------------------------------------------------------------


def test_first_page_starts_at_the_beginning_and_hands_back_a_cursor() -> None:
    items = [_item(f"o-{index}", actionRunId=f"r-{index}") for index in range(5)]

    page, cursor = _page(items, cursor=None, limit=2)

    assert [item["objectId"] for item in page] == ["o-0", "o-1"]
    assert cursor is not None


def test_cursor_resumes_after_the_anchor_row() -> None:
    items = [_item(f"o-{index}", actionRunId=f"r-{index}") for index in range(5)]
    _, cursor = _page(items, cursor=None, limit=2)

    page, _ = _page(items, cursor=cursor, limit=2)

    assert [item["objectId"] for item in page] == ["o-2", "o-3"]


def test_last_page_reports_no_further_cursor() -> None:
    items = [_item(f"o-{index}", actionRunId=f"r-{index}") for index in range(3)]

    page, cursor = _page(items, cursor=None, limit=3)

    assert len(page) == 3
    assert cursor is None


def test_cursor_whose_anchor_row_changed_is_refused() -> None:
    """Resuming on a mutated anchor would silently skip or repeat audit rows."""
    items = [_item(f"o-{index}", actionRunId=f"r-{index}") for index in range(5)]
    _, cursor = _page(items, cursor=None, limit=2)
    mutated = [_item("o-1", actionRunId="r-CHANGED") if item["objectId"] == "o-1" else item for item in items]

    with pytest.raises(ValidationFailed, match="cursor row changed"):
        _page(mutated, cursor=cursor, limit=2)


def test_cursor_whose_anchor_row_disappeared_is_refused() -> None:
    items = [_item(f"o-{index}", actionRunId=f"r-{index}") for index in range(5)]
    _, cursor = _page(items, cursor=None, limit=2)
    without_anchor = [item for item in items if item["objectId"] != "o-1"]

    with pytest.raises(ValidationFailed, match="cursor object was not found"):
        _page(without_anchor, cursor=cursor, limit=2)
