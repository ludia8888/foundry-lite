from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    IndexRunCursor,
    ObjectPropertyMap,
    ObjectRecordRow,
    ObjectTypeRow,
    PropertyTypeRow,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.object_store.indexing_types import ObjectCdcEvent
from foundry_lite.domain.errors import ValidationFailed

CDC_VERSION_KEY = "_cdc"
CDC_INDEX_OPS = frozenset({"c", "u", "r", "d"})


def cdc_source_dataset(object_type: ObjectTypeRow) -> str:
    cdc = object_type["backing"].get("cdc")
    if not isinstance(cdc, Mapping):
        raise ValidationFailed("object type has no CDC source mapping", details={"objectType": object_type["api_name"]})
    dataset = cdc.get("dataset")
    if not isinstance(dataset, str) or not dataset:
        raise ValidationFailed(
            "CDC source mapping dataset is required", details={"objectType": object_type["api_name"]}
        )
    return dataset


def parse_object_cdc_event(
    raw: Mapping[str, object],
    object_type: ObjectTypeRow,
    properties: Sequence[PropertyTypeRow],
) -> ObjectCdcEvent:
    op = _event_op(raw)
    pk = _json_mapping(raw, "pk", "pk_json")
    primary_key_columns = _primary_key_columns(object_type, properties)
    object_id = _object_id(pk, primary_key_columns)
    data = _event_data(raw, op)
    _require_primary_key_stable(raw, op, primary_key_columns, object_id)
    return ObjectCdcEvent(
        event_id=_event_id(raw),
        op=op,
        object_id=object_id,
        base_patch=_base_patch(data, properties),
        ordering=_json_mapping(raw, "ordering", "ordering_json"),
    )


def cdc_event_should_skip(existing: ObjectRecordRow, event: ObjectCdcEvent) -> bool:
    previous = existing["property_versions"].get(CDC_VERSION_KEY)
    if not isinstance(previous, Mapping):
        return False
    previous_ordering = previous.get("ordering")
    if not isinstance(previous_ordering, Mapping):
        return False
    return _ordering_key(event.ordering) <= _ordering_key(previous_ordering)


def cdc_property_versions(
    existing: ObjectRecordRow | None,
    event: ObjectCdcEvent,
    properties: ObjectPropertyMap,
) -> dict[str, object]:
    versions = dict(existing["property_versions"]) if existing is not None else {}
    for name in properties:
        versions[name] = _next_property_version(versions.get(name))
    versions[CDC_VERSION_KEY] = {"eventId": event.event_id, "ordering": dict(event.ordering)}
    return versions


def cdc_source_dataset_version_id(event: ObjectCdcEvent) -> str:
    return f"cdc:{event.event_id}"


def cdc_source_hash(event: ObjectCdcEvent, base_patch: ObjectPropertyMap) -> str:
    return _json_hash({"base": dict(base_patch), "op": event.op, "ordering": dict(event.ordering)})


def cdc_cursor(events: Sequence[ObjectCdcEvent], skipped: int) -> IndexRunCursor:
    if not events:
        return {"last_event_id": None, "events_skipped": skipped}
    return {"last_event_id": events[-1].event_id, "last_ordering": dict(events[-1].ordering), "events_skipped": skipped}


def _event_id(raw: Mapping[str, object]) -> str:
    value = raw.get("event_id", raw.get("eventId"))
    if not isinstance(value, str) or not value:
        raise ValidationFailed("CDC event id is required")
    return value


def _event_op(raw: Mapping[str, object]) -> str:
    value = raw.get("op")
    if not isinstance(value, str) or value not in CDC_INDEX_OPS:
        raise ValidationFailed("CDC event operation is invalid", details={"op": str(value)})
    return value


def _event_data(raw: Mapping[str, object], op: str) -> Mapping[str, object]:
    key = "before" if op == "d" else "after"
    json_key = "before_json" if op == "d" else "after_json"
    data = _nullable_json_mapping(raw, key, json_key)
    if data is None and op != "d":
        raise ValidationFailed("CDC upsert event after payload is required", details={"op": op})
    return data or {}


def _require_primary_key_stable(
    raw: Mapping[str, object],
    op: str,
    columns: Sequence[str],
    event_object_id: str,
) -> None:
    before_id = _payload_object_id(_nullable_json_mapping(raw, "before", "before_json"), columns)
    after_id = _payload_object_id(_nullable_json_mapping(raw, "after", "after_json"), columns)
    if before_id is not None and after_id is not None and before_id != after_id:
        raise ValidationFailed(
            "CDC primary key update is not supported",
            details={"before_object_id": before_id, "after_object_id": after_id},
        )
    payload_id = before_id if op == "d" else after_id
    if payload_id is not None and payload_id != event_object_id:
        raise ValidationFailed(
            "CDC primary key payload does not match event key",
            details={"event_object_id": event_object_id, "payload_object_id": payload_id},
        )


def _payload_object_id(payload: Mapping[str, object] | None, columns: Sequence[str]) -> str | None:
    if payload is None or any(column not in payload for column in columns):
        return None
    return _object_id(payload, columns)


def _base_patch(raw: Mapping[str, object], properties: Sequence[PropertyTypeRow]) -> ObjectPropertyMap:
    patch: dict[str, object] = {}
    for prop in properties:
        if prop["source"] != "dataset":
            continue
        column_name = prop["column_name"]
        if column_name is not None and column_name in raw:
            patch[prop["api_name"]] = raw[column_name]
    return patch


def _primary_key_columns(object_type: ObjectTypeRow, properties: Sequence[PropertyTypeRow]) -> Sequence[str]:
    cdc = object_type["backing"].get("cdc")
    columns = cdc.get("primaryKeyColumns") if isinstance(cdc, Mapping) else None
    if isinstance(columns, Sequence) and not isinstance(columns, str | bytes):
        return [str(column) for column in columns if isinstance(column, str)]
    return object_type["backing"].get("primaryKeyColumns") or [_primary_key_column(object_type, properties)]


def _primary_key_column(object_type: ObjectTypeRow, properties: Sequence[PropertyTypeRow]) -> str:
    prop = next(item for item in properties if item["api_name"] == object_type["primary_key_property"])
    if prop["column_name"] is None:
        raise ValidationFailed("object primary key column missing")
    return prop["column_name"]


def _object_id(pk: Mapping[str, object], columns: Sequence[str]) -> str:
    values = [pk.get(column) for column in columns]
    if not values or any(value in {None, ""} for value in values):
        raise ValidationFailed("CDC primary key value is missing", details={"primaryKeyColumns": list(columns)})
    return "|".join(str(value) for value in values)


def _json_mapping(raw: Mapping[str, object], key: str, json_key: str) -> Mapping[str, object]:
    value = _nullable_json_mapping(raw, key, json_key)
    if value is None:
        raise ValidationFailed("CDC event mapping field is required", details={"field": key})
    return value


def _nullable_json_mapping(raw: Mapping[str, object], key: str, json_key: str) -> Mapping[str, object] | None:
    value = raw.get(key)
    if isinstance(value, Mapping):
        return dict(value)
    encoded = raw.get(json_key)
    if not isinstance(encoded, str):
        return None
    return _decode_json_mapping(encoded, key)


def _decode_json_mapping(encoded: str, key: str) -> Mapping[str, object] | None:
    if encoded == "null":
        return None
    parsed = json.loads(encoded)
    if not isinstance(parsed, Mapping):
        raise ValidationFailed("CDC JSON field must decode to an object", details={"field": key})
    return dict(parsed)


def _ordering_key(ordering: Mapping[str, object]) -> tuple[int, int, str]:
    return (_int_value(ordering.get("lsn")), _int_value(ordering.get("source_ts_ms")), str(ordering.get("table", "")))


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _next_property_version(value: object) -> int:
    return value + 1 if isinstance(value, int) and not isinstance(value, bool) else 1
