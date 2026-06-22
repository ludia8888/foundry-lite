from __future__ import annotations

import pytest
from foundry_lite.application.ports import RuntimeRunSnapshot
from foundry_lite.application.services import runtime_run_queries as queries
from foundry_lite.domain.errors import ValidationFailed


def test_runtime_run_query_helpers_filter_and_join_related_rows() -> None:
    snapshot = _snapshot()

    filtered = queries.filtered_snapshot(
        snapshot,
        run_type=queries.required_run_type("action"),
        status="succeeded",
        since="2026-06-10T00:00:00Z",
        until="2026-06-11T00:00:00Z",
    )
    row = filtered["actionRuns"][0]

    assert queries.optional_run_type("") is None
    assert filtered["transformRuns"] == []
    assert filtered["actionRuns"] == [snapshot["actionRuns"][0]]
    assert filtered["objectEdits"] == []
    assert queries.row_status(row) == "succeeded"
    assert queries.correlation_id(row) == "action_run_1"
    assert queries.row_references(row)["target_object_id"] == "O-1001"
    assert queries.resource_ids_for_lineage(
        {"output_version_id": "version_clean", "input_versions": {"raw": "version_raw", "bad": 1}}
    ) == {"version_clean", "version_raw"}


def test_relation_ids_exclude_tenant_scope() -> None:
    # Regression: ``tenant_id`` scopes the snapshot, it is not a correlation key.
    # Including it matched every same-tenant evidence row as "related", so the
    # correlation ids that drive related-evidence push-down must omit it.
    row = {"id": "run-1", "tenant_id": "t-1", "correlation_id": "corr-A", "transform_id": "tx-9", "status": "ok"}

    ids = queries.relation_ids(row)

    assert "t-1" not in ids
    assert set(ids) == {"run-1", "corr-A", "tx-9"}
    # A row carrying only the tenant scope and non-relation fields has no correlation ids.
    assert queries.relation_ids({"tenant_id": "t-1", "status": "ok"}) == []


def test_runtime_run_query_helpers_cover_error_and_empty_branches() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationFailed):
        queries.required_run_type("unknown")

    assert queries.required_run_type("dead-letter") == "dead_letter"
    assert queries.row_status({}, "dead_letter") == "dead_lettered"
    assert queries.row_status({}) == "unknown"
    assert queries.error_message({"type": "RuntimeError"}) == "{'type': 'RuntimeError'}"
    assert queries.error_message("boom") == "boom"
    assert queries.error_message(None) is None

    before_window = queries.filtered_snapshot(
        snapshot,
        run_type=None,
        status=None,
        since="2026-06-11T00:00:00Z",
        until=None,
    )
    after_window = queries.filtered_snapshot(
        snapshot,
        run_type=None,
        status=None,
        since=None,
        until="2026-06-09T00:00:00Z",
    )
    no_timestamp_filtered = queries.filtered_snapshot(
        _snapshot_without_timestamp(),
        run_type=None,
        status=None,
        since="2026-06-10T00:00:00Z",
        until=None,
    )

    assert before_window["actionRuns"] == []
    assert after_window["actionRuns"] == []
    assert no_timestamp_filtered["actionRuns"] == []


def test_run_investigation_summarizes_failed_run_for_operator() -> None:
    investigation = queries.run_investigation(
        {
            "id": "transform_failed",
            "status": "FAILED",
            "error": {"message": "bad transform SQL"},
            "input_versions": {"raw": "version_raw"},
        },
        "transform",
        related_outbox_count=0,
        related_audit_count=1,
        related_object_edit_count=0,
        related_action_writeback_count=0,
        lineage_edge_count=0,
    )

    assert investigation["summary"] == "transform run transform_failed is FAILED: bad transform SQL"
    assert investigation["errorMessage"] == "bad transform SQL"
    assert investigation["references"]["input_versions"] == {"raw": "version_raw"}
    assert investigation["relatedCounts"]["auditEvents"] == 1
    assert "flite transform retry transform_failed" in investigation["suggestedActions"]


@pytest.mark.parametrize(
    ("run_type", "status", "expected_action"),
    [
        ("index", "failed", "flite index replay-run run_1"),
        ("dead_letter", "dead_lettered", "flite outbox retry run_1"),
        ("sync", "FAILED", "fix the source input, then rerun the dataset upload"),
        ("materialization", "failed", "inspect errorMessage, references, and related audit/outbox evidence"),
    ],
)
def test_run_investigation_suggests_operator_actions(run_type, status, expected_action) -> None:
    investigation = queries.run_investigation(
        {"id": "run_1", "status": status},
        run_type,
        related_outbox_count=0,
        related_audit_count=0,
        related_object_edit_count=0,
        related_action_writeback_count=0,
        lineage_edge_count=0,
    )

    assert investigation["summary"] == f"{run_type} run run_1 is {status}"
    assert expected_action in investigation["suggestedActions"]


def test_source_run_chain_links_object_source_to_operation_detail() -> None:
    snapshot = _snapshot()
    snapshot["syncRuns"] = [{"id": "sync_1", "status": "succeeded", "committed_version_id": "version_raw"}]
    snapshot["transformRuns"] = [{"id": "transform_1", "status": "SUCCESS", "output_version_id": "version_clean"}]
    snapshot["indexRuns"] = [
        {
            "id": "index_1",
            "status": "succeeded",
            "object_type_api_name": "Order",
            "source_ref": {"dataset_version_id": "version_clean"},
        }
    ]

    chain = queries.source_run_chain(
        snapshot,
        source_dataset_version_id="version_clean",
        object_type_api_name="Order",
        lineage_rows=[_lineage_row()],
    )

    assert [link["runType"] for link in chain] == ["index", "transform", "sync"]
    assert chain[0]["operationPath"] == "/api/operations/runs/index/index_1"
    assert chain[1]["relation"] == "source_producer"
    assert chain[2]["relation"] == "upstream_producer"


def test_source_run_chain_handles_downstream_and_unknown_lineage_runs() -> None:
    snapshot = _snapshot()
    snapshot["syncRuns"] = [{"id": "sync_1", "status": "succeeded", "committed_version_id": "version_raw"}]
    snapshot["indexRuns"] = [
        {
            "id": "index_1",
            "status": "succeeded",
            "object_type_api_name": "Order",
            "source_ref": {"dataset_version_id": "version_clean"},
        }
    ]
    snapshot["materializationRuns"] = [
        {"id": "materialization_1", "status": "succeeded", "target_dataset_version_id": "version_consumer"}
    ]

    chain = queries.source_run_chain(
        snapshot,
        source_dataset_version_id="version_clean",
        object_type_api_name="Order",
        lineage_rows=[
            _lineage_row(created_by_run_id="sync_1"),
            _lineage_row(
                edge_id="lineage_2",
                from_resource_id="version_clean",
                to_resource_id="version_consumer",
                created_by_run_id="materialization_1",
            ),
            _lineage_row(
                edge_id="lineage_3",
                from_resource_id="version_clean",
                to_resource_id="version_unknown",
                created_by_run_id="missing_run",
            ),
            _lineage_row(
                edge_id="lineage_4",
                from_resource_id="version_clean",
                to_resource_id="version_indexed",
                created_by_run_id="index_1",
            ),
        ],
    )

    assert ("materialization", "materialization_1") in {(link["runType"], link["runId"]) for link in chain}
    assert ("sync", "sync_1") in {(link["runType"], link["runId"]) for link in chain}
    assert ("index", "index_1") in {(link["runType"], link["runId"]) for link in chain}


def _snapshot() -> RuntimeRunSnapshot:
    action_row = {
        "id": "action_run_1",
        "status": "succeeded",
        "target_object_id": "O-1001",
        "created_at": "2026-06-10T00:00:00Z",
    }
    return {
        "syncRuns": [],
        "transformRuns": [{"id": "transform_1", "status": "SUCCESS", "created_at": "2026-06-10T00:00:00Z"}],
        "indexRuns": [],
        "actionRuns": [action_row],
        "actionWritebacks": [],
        "materializationRuns": [],
        "outboxEvents": [{"id": "outbox_1", "event_type": "action.run.committed", "correlation_id": "action_run_1"}],
        "deadLetterEvents": [],
        "workflowRuns": [],
        "auditEvents": [{"id": "audit_1", "event_type": "action.run.committed", "resource_id": "action_run_1"}],
        "objectEdits": [{"id": "edit_1", "action_run_id": "action_run_1"}],
    }


def _lineage_row(
    *,
    edge_id: str = "lineage_1",
    from_resource_id: str = "version_raw",
    to_resource_id: str = "version_clean",
    created_by_run_id: str = "transform_1",
) -> dict[str, str]:
    return {
        "id": edge_id,
        "tenant_id": "tenant-demo",
        "from_resource_type": "dataset_version",
        "from_resource_id": from_resource_id,
        "to_resource_type": "dataset_version",
        "to_resource_id": to_resource_id,
        "relation": "input_to",
        "created_by_run_id": created_by_run_id,
        "created_at": "2026-06-10T00:00:00Z",
    }


def _snapshot_without_timestamp() -> RuntimeRunSnapshot:
    snapshot = _snapshot()
    snapshot["actionRuns"] = [{"id": "action_run_1", "status": "succeeded"}]
    return snapshot
