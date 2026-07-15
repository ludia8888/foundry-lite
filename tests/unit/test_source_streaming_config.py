from __future__ import annotations

import pytest
from foundry_lite.application.services.source_streaming_config import validate_streaming_sync_config
from foundry_lite.domain.errors import ValidationFailed


def test_kafka_streaming_config_accepts_all_partitions_and_monitor_thresholds() -> None:
    validate_streaming_sync_config(
        "kafka",
        "streaming",
        {
            "bootstrapServers": "redpanda:9092",
            "topic": "crypto.trades",
            "streamName": "crypto-trades",
            "consumerGroup": "foundry-crypto",
            "partitionMode": "all",
            "deliveryGuarantee": "AT_LEAST_ONCE",
            "monitoring": {
                "checkpointLivenessSeconds": 60,
                "maxCheckpointDurationMs": 30_000,
                "maxBrokerLag": 10_000,
            },
        },
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"partitions": [0, 0]}, "must not contain duplicates"),
        ({"partitionMode": "selected"}, "requires partitions"),
        ({"partition": -1}, "non-negative integer"),
        ({"deliveryGuarantee": "EXACTLY_ONCE"}, "currently support AT_LEAST_ONCE"),
        ({"monitoring": {"checkpointLivenessSeconds": 0}}, "threshold is invalid"),
    ],
)
def test_kafka_streaming_config_rejects_unsafe_contracts(override: dict[str, object], message: str) -> None:
    config: dict[str, object] = {
        "bootstrapServers": "redpanda:9092",
        "topic": "crypto.trades",
        "streamName": "crypto-trades",
        "consumerGroup": "foundry-crypto",
        "partitionMode": "single",
        "partition": 0,
    }
    config.update(override)

    with pytest.raises(ValidationFailed, match=message):
        validate_streaming_sync_config("kafka", "streaming", config)
