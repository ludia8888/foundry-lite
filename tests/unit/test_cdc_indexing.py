from __future__ import annotations

import json
from typing import cast

import pytest
from foundry_lite.application.ports import ObjectRecordRow, ObjectTypeRow, PropertyTypeRow
from foundry_lite.application.services.object_store.cdc_indexing import (
    cdc_cursor,
    cdc_event_should_skip,
    cdc_property_versions,
    cdc_source_dataset,
    cdc_source_dataset_version_id,
    cdc_source_hash,
    parse_object_cdc_event,
)
from foundry_lite.application.services.object_store.indexing_types import ObjectCdcEvent
from foundry_lite.domain.errors import ValidationFailed


def test_cdc_event_parser_supports_json_envelope_fields() -> None:
    raw = {
        "eventId": "topic:0:4",
        "op": "c",
        "pk_json": json.dumps({"order_id": "O-1001"}),
        "after_json": json.dumps({"order_id": "O-1001", "status": "APPROVED", "ignored": "x"}),
        "ordering_json": json.dumps({"lsn": 4, "source_ts_ms": 1700000000004, "table": "orders"}),
    }

    event = parse_object_cdc_event(raw, _object_type(), _properties())

    assert event == ObjectCdcEvent(
        event_id="topic:0:4",
        op="c",
        object_id="O-1001",
        base_patch={"orderId": "O-1001", "status": "APPROVED"},
        ordering={"lsn": 4, "source_ts_ms": 1700000000004, "table": "orders"},
    )
    assert cdc_source_dataset(_object_type()) == "raw_cdc.erp_orders"
    assert cdc_source_dataset_version_id(event) == "cdc:topic:0:4"
    assert cdc_source_hash(event, event.base_patch)
    assert cdc_cursor([], skipped=2) == {"last_event_id": None, "events_skipped": 2}


def test_cdc_delete_event_allows_null_before_payload() -> None:
    event = parse_object_cdc_event(
        {
            "event_id": "topic:0:5",
            "op": "d",
            "pk": {"order_id": "O-1001"},
            "before_json": "null",
            "ordering": {"lsn": 5, "source_ts_ms": 1700000000005, "table": "orders"},
        },
        _object_type(),
        _properties(),
    )

    assert event.base_patch == {}
    assert event.object_id == "O-1001"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            {
                "event_id": "",
                "op": "u",
                "pk": {"order_id": "O-1001"},
                "after": {"order_id": "O-1001"},
                "ordering": {"lsn": 1},
            },
            "CDC event id is required",
        ),
        ({"event_id": "topic:0:1", "op": "x"}, "CDC event operation is invalid"),
        (
            {"event_id": "topic:0:1", "op": "u", "pk": {"order_id": "O-1001"}, "ordering": {"lsn": 1}},
            "CDC upsert event after payload is required",
        ),
        (
            {
                "event_id": "topic:0:1",
                "op": "u",
                "pk": {},
                "after": {"order_id": "O-1001"},
                "ordering": {"lsn": 1},
            },
            "CDC primary key value is missing",
        ),
        (
            {
                "event_id": "topic:0:1",
                "op": "u",
                "pk": {"order_id": "O-1001"},
                "after": {"order_id": "O-1001"},
            },
            "CDC event mapping field is required",
        ),
        (
            {
                "event_id": "topic:0:1",
                "op": "u",
                "pk": {"order_id": "O-1001"},
                "after": {"order_id": "O-1001"},
                "ordering_json": "[1]",
            },
            "CDC JSON field must decode to an object",
        ),
        (
            {
                "event_id": "topic:0:1",
                "op": "u",
                "pk": {"order_id": "O-1001"},
                "after": {"order_id": "O-1001"},
                "ordering": {"source_ts_ms": 1700000000001},
            },
            "CDC ordering field must be an integer",
        ),
        (
            {
                "event_id": "event-without-offset",
                "op": "u",
                "pk": {"order_id": "O-1001"},
                "after": {"order_id": "O-1001"},
                "ordering": {"lsn": 1},
            },
            "CDC ordering requires a stream offset tie-breaker",
        ),
    ],
)
def test_cdc_event_parser_rejects_malformed_events(raw: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationFailed, match=message):
        parse_object_cdc_event(raw, _object_type(), _properties())


def test_cdc_source_mapping_requires_a_dataset() -> None:
    with pytest.raises(ValidationFailed, match="object type has no CDC source mapping"):
        cdc_source_dataset(_object_type(backing={"dataset": "clean.orders"}))

    with pytest.raises(ValidationFailed, match="CDC source mapping dataset is required"):
        cdc_source_dataset(_object_type(backing={"dataset": "clean.orders", "cdc": {"dataset": ""}}))


def test_cdc_primary_key_falls_back_to_property_column() -> None:
    object_type = _object_type(backing={"dataset": "clean.orders", "cdc": {"dataset": "raw_cdc.erp_orders"}})
    event = parse_object_cdc_event(
        {
            "event_id": "topic:0:6",
            "op": "r",
            "pk": {"order_id": "O-1001"},
            "after": {"order_id": "O-1001", "status": "APPROVED"},
            "ordering": {"lsn": 6},
        },
        object_type,
        _properties(),
    )

    assert event.object_id == "O-1001"


def test_cdc_composite_primary_key_uses_canonical_tuple_identity() -> None:
    object_type = _object_type(
        backing={
            "dataset": "clean.orders",
            "cdc": {
                "dataset": "raw_cdc.erp_order_lines",
                "primaryKeyColumns": ["region", "order_id"],
            },
        }
    )
    ambiguous_left = parse_object_cdc_event(
        {
            "event_id": "topic:0:10",
            "op": "u",
            "pk": {"region": "A|B", "order_id": "C"},
            "after": {"region": "A|B", "order_id": "C", "status": "APPROVED"},
            "ordering": {"lsn": 10},
        },
        object_type,
        _properties(),
    )
    ambiguous_right = parse_object_cdc_event(
        {
            "event_id": "topic:0:11",
            "op": "u",
            "pk": {"region": "A", "order_id": "B|C"},
            "after": {"region": "A", "order_id": "B|C", "status": "APPROVED"},
            "ordering": {"lsn": 11},
        },
        object_type,
        _properties(),
    )

    assert ambiguous_left.object_id.startswith("cdc_pk:v1:")
    assert ambiguous_right.object_id.startswith("cdc_pk:v1:")
    assert ambiguous_left.object_id != ambiguous_right.object_id


def test_cdc_primary_key_fallback_requires_a_column() -> None:
    object_type = _object_type(backing={"dataset": "clean.orders", "cdc": {"dataset": "raw_cdc.erp_orders"}})
    properties = [_property("orderId", column_name=None), _property("status", column_name="status")]

    with pytest.raises(ValidationFailed, match="object primary key column missing"):
        parse_object_cdc_event(
            {
                "event_id": "topic:0:7",
                "op": "u",
                "pk": {"order_id": "O-1001"},
                "after": {"order_id": "O-1001"},
                "ordering": {"lsn": 7},
            },
            object_type,
            properties,
        )


def test_cdc_pk_update_policy() -> None:
    with pytest.raises(ValidationFailed, match="primary key update is not supported") as exc_info:
        parse_object_cdc_event(
            {
                "event_id": "topic:0:9",
                "op": "u",
                "pk": {"order_id": "O-1001"},
                "before": {"order_id": "O-1001", "status": "PENDING"},
                "after": {"order_id": "O-2002", "status": "APPROVED"},
                "ordering": {"lsn": 9},
            },
            _object_type(),
            _properties(),
        )

    assert exc_info.value.details == {"before_object_id": "O-1001", "after_object_id": "O-2002"}


def test_cdc_ordering_and_property_versions_are_monotonic() -> None:
    event = ObjectCdcEvent(
        event_id="topic:0:8",
        op="u",
        object_id="O-1001",
        base_patch={"status": "APPROVED", "amount": 725},
        ordering={"lsn": 8, "source_ts_ms": 1700000000008, "table": "orders"},
    )
    older = _record({"_cdc": {"eventId": "topic:0:7", "ordering": {"lsn": 7}}})
    same = _record({"_cdc": {"eventId": "topic:0:8", "ordering": event.ordering}})
    no_cdc = _record({})
    no_ordering = _record({"_cdc": {"eventId": "topic:0:7"}})

    assert cdc_event_should_skip(older, event) is False
    assert cdc_event_should_skip(same, event) is True
    assert cdc_event_should_skip(no_cdc, event) is False
    assert cdc_event_should_skip(no_ordering, event) is False

    versions = cdc_property_versions(
        _record({"status": 2, "amount": "bad"}),
        event,
        {"status": "APPROVED", "amount": 725},
    )

    assert versions["status"] == 3
    assert versions["amount"] == 1
    assert versions["_cdc"] == {"eventId": "topic:0:8", "ordering": event.ordering}


def _object_type(*, backing: dict[str, object] | None = None) -> ObjectTypeRow:
    return cast(
        ObjectTypeRow,
        {
            "id": "otype_order",
            "tenant_id": "tenant_demo",
            "ontology_version_id": "ont_v1",
            "api_name": "Order",
            "display_name": "Order",
            "description": None,
            "primary_key_property": "orderId",
            "backing": backing
            or {
                "dataset": "clean.orders",
                "primaryKeyColumns": ["order_id"],
                "cdc": {"dataset": "raw_cdc.erp_orders", "primaryKeyColumns": ["order_id"]},
            },
            "config": {},
        },
    )


def _properties() -> list[PropertyTypeRow]:
    return [
        _property("orderId", column_name="order_id"),
        _property("status", column_name="status"),
        _property("manualNote", source="edit", column_name="manual_note"),
    ]


def _property(
    api_name: str,
    *,
    source: str = "dataset",
    column_name: str | None,
) -> PropertyTypeRow:
    return cast(
        PropertyTypeRow,
        {
            "id": f"prop_{api_name}",
            "tenant_id": "tenant_demo",
            "object_type_id": "otype_order",
            "api_name": api_name,
            "display_name": api_name,
            "data_type": "string",
            "nullable": True,
            "indexed": True,
            "searchable": False,
            "editable": source != "dataset",
            "classification": None,
            "source": source,
            "column_name": column_name,
            "edit_policy": "source_wins",
            "derivation": None,
        },
    )


def _record(property_versions: dict[str, object]) -> ObjectRecordRow:
    return cast(
        ObjectRecordRow,
        {
            "id": "obj_1",
            "tenant_id": "tenant_demo",
            "object_type_id": "otype_order",
            "object_type_api_name": "Order",
            "object_id": "O-1001",
            "index_version": "active",
            "is_active": True,
            "properties": {},
            "base_properties": {},
            "edit_properties": {},
            "property_versions": property_versions,
            "source_dataset_version_id": None,
            "source_hash": None,
            "object_version": 1,
            "deleted": False,
            "deletion_reason": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
