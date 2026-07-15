"""Validation for Kafka managed streaming Sync configuration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.domain.errors import ValidationFailed

_REQUIRED_TEXT_FIELDS = ("bootstrapServers", "topic", "streamName", "consumerGroup")
_MONITOR_MINIMUMS = {
    "checkpointLivenessSeconds": 0,
    "maxCheckpointDurationMs": 0,
    "maxBrokerLag": -1,
    "minOutputRatePerSecond": -1,
}


def validate_streaming_sync_config(
    source_type: str,
    capability: str,
    config_summary: Mapping[str, object],
) -> None:
    if source_type != "kafka" or capability != "streaming":
        return
    _require_text_fields(config_summary)
    _validate_partition_selection(config_summary)
    _validate_monitoring(config_summary.get("monitoring"))
    guarantee = config_summary.get("deliveryGuarantee", "AT_LEAST_ONCE")
    if guarantee != "AT_LEAST_ONCE":
        raise ValidationFailed(
            "Kafka Source streaming extracts currently support AT_LEAST_ONCE delivery",
            details={"deliveryGuarantee": guarantee},
        )


def _require_text_fields(config: Mapping[str, object]) -> None:
    missing = [field for field in _REQUIRED_TEXT_FIELDS if not _is_nonempty_text(config.get(field))]
    if missing:
        raise ValidationFailed("Kafka streaming Sync configuration is incomplete", details={"missingFields": missing})


def _validate_partition_selection(config: Mapping[str, object]) -> None:
    mode = config.get("partitionMode", "single")
    if mode not in {"all", "single", "selected"}:
        raise ValidationFailed("Kafka partitionMode is invalid", details={"partitionMode": mode})
    selected = config.get("partitions")
    if selected is not None:
        _validate_selected_partitions(selected)
        return
    if mode == "selected":
        raise ValidationFailed("selected Kafka partitionMode requires partitions")
    if mode == "single" and not _is_nonnegative_int(config.get("partition", 0)):
        raise ValidationFailed("Kafka partition must be a non-negative integer")


def _validate_selected_partitions(value: object) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValidationFailed("Kafka partitions must be a non-empty integer list")
    partitions = list(value)
    if any(not _is_nonnegative_int(partition) for partition in partitions):
        raise ValidationFailed("Kafka partitions must contain non-negative integers")
    if len(set(partitions)) != len(partitions):
        raise ValidationFailed("Kafka partitions must not contain duplicates")


def _validate_monitoring(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise ValidationFailed("Kafka streaming monitoring configuration must be an object")
    for field, minimum in _MONITOR_MINIMUMS.items():
        observed = value.get(field)
        if observed is None:
            continue
        if not isinstance(observed, (int, float)) or isinstance(observed, bool) or observed <= minimum:
            raise ValidationFailed(
                "Kafka streaming monitor threshold is invalid",
                details={"field": field, "value": observed, "minimumExclusive": minimum},
            )


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
