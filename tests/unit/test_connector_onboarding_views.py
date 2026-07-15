from __future__ import annotations

from typing import cast

from foundry_lite.application.ports import (
    ConnectorConnectionRow,
    ConnectorResourceRow,
    RestSourceConfig,
)
from foundry_lite.application.services.connector_onboarding_config import ConnectorBundle
from foundry_lite.application.services.connector_onboarding_views import (
    CONNECTOR_PREVIEW_ROW_LIMIT,
    _test_result,
)


def test_connector_resource_preview_returns_palantir_sized_sample() -> None:
    bundle = ConnectorBundle(
        connection=cast(ConnectorConnectionRow, {"connector_name": "github"}),
        resource=cast(
            ConnectorResourceRow,
            {"resource_name": "repositories", "dataset_ref": "demo.repositories"},
        ),
        config_fingerprint="sha256:preview",
        rest=cast(RestSourceConfig, {}),
    )
    rows = [{"id": index} for index in range(CONNECTOR_PREVIEW_ROW_LIMIT + 5)]

    result = _test_result(bundle, status="succeeded", rows=rows)

    assert result["rowCount"] == CONNECTOR_PREVIEW_ROW_LIMIT + 5
    sample_rows = cast(list[dict[str, object]], result["sampleRows"])
    assert len(sample_rows) == CONNECTOR_PREVIEW_ROW_LIMIT
    assert sample_rows[-1]["id"] == CONNECTOR_PREVIEW_ROW_LIMIT - 1
