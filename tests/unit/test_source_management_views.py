from foundry_lite.application.services.source_management_views import sync_run_view


def _sync_run_row(
    *,
    result_summary: dict[str, object],
    operations_path: str | None,
) -> dict[str, object]:
    return {
        "id": "source_sync_run_1",
        "sync_name": "orders_sync",
        "source_name": "orders",
        "source_type": "rest_api",
        "capability": "batch",
        "workflow_run_id": "workflow-1",
        "dataset_version_id": "dsv_1",
        "status": "succeeded",
        "trigger_type": "manual",
        "batch_limit": None,
        "checkpoint_start": {},
        "checkpoint_end": {},
        "result_summary": result_summary,
        "error": None,
        "operations_path": operations_path,
        "started_at": "2026-07-07T00:00:00Z",
        "completed_at": "2026-07-07T00:00:01Z",
        "created_at": "2026-07-07T00:00:00Z",
    }


def test_sync_run_view_prefers_workflow_operations_path() -> None:
    row = _sync_run_row(
        result_summary={
            "workflowRun": {
                "operationPath": "/api/operations/runs/workflow/workflow-1",
            },
        },
        operations_path="/operations/source-sync-runs/source_sync_run_1",
    )

    assert sync_run_view(row)["operationsPath"] == "/api/operations/runs/workflow/workflow-1"


def test_sync_run_view_hides_unroutable_source_sync_path() -> None:
    row = _sync_run_row(
        result_summary={},
        operations_path="/operations/source-sync-runs/source_sync_run_1",
    )

    assert sync_run_view(row)["operationsPath"] is None
