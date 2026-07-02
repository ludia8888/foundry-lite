"""Pure CDC object-indexing rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.domain.errors import ValidationFailed


def cdc_deletion_reason(is_deleted: bool) -> str | None:
    return "source_deleted" if is_deleted else None


def should_skip_cdc_event(
    previous_ordering: Mapping[str, object],
    previous_event_id: str,
    event_ordering: Mapping[str, object],
    event_id: str,
) -> bool:
    previous_key = cdc_ordering_key(previous_ordering, previous_event_id)
    event_key = cdc_ordering_key(event_ordering, event_id)
    return event_key <= previous_key


def cdc_ordering_key(ordering: Mapping[str, object], event_id: str) -> tuple[int, int, int, int, str, int, int, str]:
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


def require_cdc_ordering(ordering: Mapping[str, object], event_id: str) -> None:
    _required_int(ordering, "lsn")
    _event_stream_position(ordering, event_id)


def _event_stream_position(ordering: Mapping[str, object], event_id: str) -> tuple[int, int]:
    partition = _optional_int_value(ordering, ("partition", "stream_partition", "kafka_partition"))
    offset = _optional_int_or_none(ordering, ("offset", "stream_offset", "kafka_offset"))
    parsed = _event_id_partition_offset(event_id)
    if parsed is not None:
        parsed_partition, parsed_offset = parsed
        offset = parsed_offset if offset is None else offset
        partition = parsed_partition if partition == 0 else partition
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
    raise ValidationFailed("CDC ordering field must be an integer", details={"field": name, "value": str(value)})


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
