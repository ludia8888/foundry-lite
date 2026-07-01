"""Application service helpers for cdc indexing workflows."""

from __future__ import annotations

import base64
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
    event_id = _event_id(raw)
    pk = _json_mapping(raw, "pk", "pk_json")
    primary_key_columns = _primary_key_columns(object_type, properties)
    object_id = _object_id(pk, primary_key_columns)
    data = _event_data(raw, op)
    ordering = _json_mapping(raw, "ordering", "ordering_json")
    _validate_ordering(ordering, event_id)
    _require_primary_key_stable(raw, op, primary_key_columns, object_id)
    return ObjectCdcEvent(
        event_id=event_id,
        op=op,
        object_id=object_id,
        base_patch=_base_patch(data, properties),
        ordering=ordering,
        late_data_status=_optional_string(raw.get("late_data_status")),
        event_time_lag_seconds=_optional_int(raw.get("event_time_lag_seconds")),
    )


def cdc_event_should_skip(existing: ObjectRecordRow, event: ObjectCdcEvent) -> bool:
    previous = existing["property_versions"].get(CDC_VERSION_KEY)
    if not isinstance(previous, Mapping):
        return False
    previous_ordering = previous.get("ordering")
    if not isinstance(previous_ordering, Mapping):
        return False
    previous_event_id = previous.get("eventId")
    if not isinstance(previous_event_id, str):
        return False
    try:
        previous_key = _ordering_key(previous_ordering, previous_event_id)
    except ValidationFailed:
        return False
    return _ordering_key(event.ordering, event.event_id) <= previous_key


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
    cursor: IndexRunCursor = {
        "last_event_id": events[-1].event_id,
        "last_ordering": dict(events[-1].ordering),
        "events_skipped": skipped,
    }
    cursor.update(_late_data_cursor(events))
    return cursor


def _late_data_cursor(events: Sequence[ObjectCdcEvent]) -> IndexRunCursor:
    counts: dict[str, int] = {}
    late_event_ids: list[str] = []
    max_lag: int | None = None
    for event in events:
        if event.late_data_status is None:
            continue
        counts[event.late_data_status] = counts.get(event.late_data_status, 0) + 1
        if event.late_data_status == "LATE_REQUIRES_REPROCESS":
            late_event_ids.append(event.event_id)
        if event.event_time_lag_seconds is not None:
            max_lag = max(event.event_time_lag_seconds, max_lag or event.event_time_lag_seconds)
    if not counts:
        return {}
    return {
        "lateDataStatusCounts": counts,
        "lateEventIds": late_event_ids,
        "maxEventTimeLagSeconds": max_lag,
    }


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
    values = [_required_pk_component(pk, column) for column in columns]
    if not values:
        raise ValidationFailed("CDC primary key value is missing", details={"primaryKeyColumns": list(columns)})
    if len(values) == 1:
        return str(values[0])
    payload = [
        {"column": column, "value": _composite_pk_json_value(value)}
        for column, value in zip(columns, values, strict=True)
    ]
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).decode("ascii")
    return "cdc_pk:v1:" + encoded.rstrip("=")


def _required_pk_component(pk: Mapping[str, object], column: str) -> object:
    value = pk.get(column)
    if value is None or value == "":
        raise ValidationFailed("CDC primary key value is missing", details={"primaryKeyColumns": [column]})
    return value


def _composite_pk_json_value(value: object) -> object:
    if isinstance(value, str | int | float | bool):
        return value
    raise ValidationFailed(
        "CDC composite primary key value must be a JSON scalar",
        details={"valueType": type(value).__name__},
    )


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


def _validate_ordering(ordering: Mapping[str, object], event_id: str) -> None:
    _required_int(ordering, "lsn")
    _event_stream_position(ordering, event_id)


def _ordering_key(ordering: Mapping[str, object], event_id: str) -> tuple[int, int, int, int, str, int, int, str]:
    partition, offset = _event_stream_position(ordering, event_id)
    return (
        _required_int(ordering, "lsn"),
        _optional_int_value(ordering, ("transaction_order", "transactionOrder", "tx_order", "txOrder")),
        _optional_int_value(ordering, ("transaction_id", "transactionId", "tx_id", "txId")),
        _optional_int_value(ordering, ("source_ts_ms",)),
        str(ordering.get("table", "")),
        partition,
        offset,
        event_id,
    )


def _event_stream_position(ordering: Mapping[str, object], event_id: str) -> tuple[int, int]:
    partition = _optional_int_value(ordering, ("partition", "stream_partition", "kafka_partition"))
    offset = _optional_int_or_none(ordering, ("offset", "stream_offset", "kafka_offset"))
    parsed = _event_id_partition_offset(event_id)
    if parsed is not None:
        parsed_partition, parsed_offset = parsed
        if offset is None:
            offset = parsed_offset
        if partition == 0:
            partition = parsed_partition
    if offset is None:
        raise ValidationFailed(
            "CDC ordering requires a stream offset tie-breaker",
            details={"field": "ordering", "event_id": event_id},
        )
    return partition, offset


def _event_id_partition_offset(event_id: str) -> tuple[int, int] | None:
    parts = event_id.rsplit(":", 2)
    if len(parts) != 3:
        return None
    partition = _int_from_text(parts[1])
    offset = _int_from_text(parts[2])
    if partition is None or offset is None:
        return None
    return partition, offset


def _required_int(ordering: Mapping[str, object], name: str) -> int:
    value = ordering.get(name)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValidationFailed(
        "CDC ordering field must be an integer",
        details={"field": name, "value": str(value)},
    )


def _optional_int_value(ordering: Mapping[str, object], names: Sequence[str]) -> int:
    value = _optional_int_or_none(ordering, names)
    return 0 if value is None else value


def _optional_int_or_none(ordering: Mapping[str, object], names: Sequence[str]) -> int | None:
    for name in names:
        value = ordering.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _int_from_text(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _next_property_version(value: object) -> int:
    return value + 1 if isinstance(value, int) and not isinstance(value, bool) else 1
