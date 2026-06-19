from __future__ import annotations

from pathlib import Path


def test_operations_ui_record_dlq_retry_shows_result() -> None:
    html = Path("apps/web/index.html").read_text(encoding="utf-8")

    expected_fragments = [
        'id="recordDlqStatus"',
        'id="recordDlqId"',
        'id="loadRecordDlqBtn"',
        'id="recordDlqDetailBtn"',
        'id="replayRecordDlqBtn"',
        'id="bulkReplayRecordDlqBtn"',
        'id="discardRecordDlqBtn"',
        'id="recordDlqResult"',
        'id="requestIdText"',
        'id="errorKindText"',
        'id="retryableText"',
        "createRequestId",
        "normalizeFoundryLiteError",
        "isRetryableFoundryLiteError",
        "requestIdFactory",
        "onResponse: updateRequestTelemetry",
        'tenantId: "tenant-demo"',
        'userId: "web-demo-operator"',
        'roles: ["ops_manager", "data_engineer", "finance"]',
        "sdkClient().request(path, options)",
        'id="metricLateData"',
        'id="metricImpactRuns"',
        'id="metricImpactRoots"',
        "sdkClient().operations.deadLetterRecords.list",
        "sdkClient().operations.deadLetterRecords.get",
        "sdkClient().operations.deadLetterRecords.retry",
        "sdkClient().operations.deadLetterRecords.bulkRetry",
        "sdkClient().operations.deadLetterRecords.discard",
        "lastRecordReplay",
        "lastRecordDiscard",
        "impactPreview",
        "lateDataBadge",
        "downstreamImpact",
    ]

    for fragment in expected_fragments:
        assert fragment in html
