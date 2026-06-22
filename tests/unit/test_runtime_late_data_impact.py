from __future__ import annotations

from collections.abc import Callable, Sequence

from foundry_lite.application.ports import LineageEdgeRow, RuntimeRow, RuntimeRunSnapshot, RuntimeRunType
from foundry_lite.application.services.runtime_late_data_impact import (
    downstream_impact_graph,
    materialization_late_data_badge,
    object_late_data_badge,
)


def test_object_late_data_badge_requires_cdc_source_and_late_index_run() -> None:
    snapshot = _snapshot(
        index_runs=[
            _run(
                "index-old",
                object_type_api_name="Order",
                completed_at="2026-06-18T01:00:00Z",
                cursor={
                    "lateDataStatusCounts": {"LATE_REQUIRES_REPROCESS": 1},
                    "lateEventIds": ["topic:0:40"],
                },
            ),
            _run(
                "index-new",
                object_type_api_name="Order",
                completed_at="2026-06-18T02:00:00Z",
                cursor={
                    "lateDataStatusCounts": {"LATE_REQUIRES_REPROCESS": 1},
                    "lateEventIds": ["topic:0:40", 41],
                    "maxEventTimeLagSeconds": 900,
                },
            ),
        ]
    )

    assert (
        object_late_data_badge(snapshot, source_dataset_version_id="dataset-v1", object_type_api_name="Order") is None
    )
    assert (
        object_late_data_badge(snapshot, source_dataset_version_id="cdc:missing", object_type_api_name="Order") is None
    )

    badge = object_late_data_badge(snapshot, source_dataset_version_id="cdc:topic:0:40", object_type_api_name="Order")

    assert badge is not None
    assert badge["sourceEventId"] == "topic:0:40"
    assert badge["lateEventIds"] == ["topic:0:40"]
    assert badge["maxEventTimeLagSeconds"] == 900
    assert badge["sourceRun"]["runId"] == "index-new"


def test_materialization_late_data_badge_requires_reopen_metadata() -> None:
    assert materialization_late_data_badge({"reopen": {"isReopened": False}}) is None

    badge = materialization_late_data_badge(
        {
            "apiName": "order_current",
            "reopen": {
                "isReopened": True,
                "sourceIndexRunId": "index-late",
                "lateEventIds": ["topic:0:40", 41],
                "maxEventTimeLagSeconds": 120,
            },
        }
    )

    assert badge is not None
    assert badge["apiName"] == "order_current"
    assert badge["sourceIndexRunId"] == "index-late"
    assert badge["lateEventIds"] == ["topic:0:40"]


def test_downstream_impact_graph_returns_none_without_late_roots_or_badges() -> None:
    impact = downstream_impact_graph(
        _run("mat-run", api_name="order_current"),
        None,
        _run_resolver(_snapshot()),
        lambda _resource_id: [],
    )

    assert impact is None


def test_downstream_impact_graph_walks_lineage_and_dedupes_run_links() -> None:
    materialization_run = _run("mat-run", api_name="order_current", status="SUCCEEDED")
    dataset_transaction = _run(
        "tx-late",
        metadata={
            "lateDataReprocessingPlan": {
                "affectedClosedOutputs": [
                    {"resourceType": "dataset_version", "resourceId": "orders-v1"},
                    {"resourceType": "dataset_version", "resourceId": "orders-v1"},
                    {"resourceType": "dataset_version"},
                ],
                "previousCommittedDatasetVersionId": "orders-prev",
            },
            "materializationDetail": {
                "apiName": "order_current",
                "reopen": {
                    "isReopened": True,
                    "previousDatasetVersionId": "orders-v1",
                    "sourceIndexRunId": "index-late",
                    "lateEventIds": ["topic:0:40"],
                },
            },
        },
    )
    snapshot = _snapshot(
        sync_runs=[_run("sync-run")],
        transform_runs=[_run("transform-run")],
        index_runs=[_run("index-late")],
        materialization_runs=[materialization_run],
    )

    impact = downstream_impact_graph(
        materialization_run,
        dataset_transaction,
        _run_resolver(snapshot),
        _lineage_lookup(
            [
                _edge("edge-wrong-source", "other-v1", "ignored-v1", "sync-run"),
                _edge("edge-sync", "orders-v1", "orders-staged-v1", "sync-run", relation="synced_to"),
                _edge("edge-transform", "orders-v1", "orders-clean-v1", "transform-run"),
                _edge("edge-transform-duplicate", "orders-v1", "orders-clean-v2", "transform-run"),
                _edge("edge-index", "orders-clean-v1", "order-object-index", "index-late", to_type="object_index"),
                _edge("edge-materialize", "orders-clean-v1", "insight-v1", "mat-run"),
                _edge("edge-missing-run", "orders-clean-v2", "orphan-v1", "missing-run"),
            ]
        ),
    )

    assert impact is not None
    root_ids = {(root["resourceType"], root["resourceId"]) for root in impact["rootResources"]}
    run_links = {(link["runType"], link["runId"], link["relation"]) for link in impact["affectedRuns"]}

    assert impact["status"] == "IMPACTED"
    assert ("dataset_version", "orders-v1") in root_ids
    assert ("dataset_version", "orders-prev") in root_ids
    assert ("index_run", "index-late") in root_ids
    assert ("sync", "sync-run", "synced_to") in run_links
    assert ("transform", "transform-run", "derived_from") in run_links
    assert ("index", "index-late", "derived_from") in run_links
    assert ("materialization", "mat-run", "late_data_reprocessed") in run_links
    assert sum(1 for link in impact["affectedRuns"] if link["runId"] == "transform-run") == 1
    assert impact["lateDataBadges"][0]["runId"] == "mat-run"


def test_downstream_impact_graph_handles_cycles_and_reopen_without_sources() -> None:
    run = _run("mat-run", api_name="order_current")
    dataset_transaction = _run(
        "tx-cycle",
        metadata={
            "lateDataReprocessingPlan": {
                "affectedClosedOutputs": [{"resourceType": "dataset_version", "resourceId": "ds-a"}],
            },
            # Reopen with no previous/source ids exercises the skip branches.
            "materializationDetail": {"apiName": "order_current", "reopen": {"isReopened": True}},
        },
    )

    impact = downstream_impact_graph(
        run,
        dataset_transaction,
        _run_resolver(_snapshot()),
        _lineage_lookup(
            [
                _edge("e-ab", "ds-a", "ds-b", "sync-run"),
                _edge("e-ba", "ds-b", "ds-a", "sync-run"),  # cycles back to a visited resource
            ]
        ),
    )

    assert impact is not None
    node_ids = {(node["resourceType"], node["resourceId"]) for node in impact["nodes"]}
    assert ("dataset_version", "ds-a") in node_ids
    assert ("dataset_version", "ds-b") in node_ids


def _run_resolver(
    snapshot: RuntimeRunSnapshot,
) -> Callable[[str], tuple[RuntimeRunType, RuntimeRow] | None]:
    groups: tuple[tuple[RuntimeRunType, str], ...] = (
        ("sync", "syncRuns"),
        ("transform", "transformRuns"),
        ("index", "indexRuns"),
        ("materialization", "materializationRuns"),
    )

    def resolve(run_id: str) -> tuple[RuntimeRunType, RuntimeRow] | None:
        for run_type, key in groups:
            for row in snapshot[key]:  # type: ignore[literal-required]
                if row.get("id") == run_id:
                    return run_type, row
        return None

    return resolve


def _snapshot(
    *,
    sync_runs: Sequence[RuntimeRow] = (),
    transform_runs: Sequence[RuntimeRow] = (),
    index_runs: Sequence[RuntimeRow] = (),
    materialization_runs: Sequence[RuntimeRow] = (),
) -> RuntimeRunSnapshot:
    return {
        "syncRuns": list(sync_runs),
        "transformRuns": list(transform_runs),
        "indexRuns": list(index_runs),
        "actionRuns": [],
        "actionWritebacks": [],
        "materializationRuns": list(materialization_runs),
        "outboxEvents": [],
        "deadLetterEvents": [],
        "workflowRuns": [],
        "auditEvents": [],
        "objectEdits": [],
    }


def _run(run_id: str, **extra: object) -> RuntimeRow:
    return {"id": run_id, "status": "SUCCEEDED", **extra}


def _edge(
    edge_id: str,
    from_id: str,
    to_id: str,
    run_id: str,
    *,
    relation: str = "derived_from",
    to_type: str = "dataset_version",
) -> LineageEdgeRow:
    return {
        "id": edge_id,
        "tenant_id": "tenant-demo",
        "from_resource_type": "dataset_version",
        "from_resource_id": from_id,
        "to_resource_type": to_type,
        "to_resource_id": to_id,
        "relation": relation,
        "created_by_run_id": run_id,
        "created_at": "2026-06-18T00:00:00Z",
    }


def _lineage_lookup(edges: Sequence[LineageEdgeRow]):
    def lookup(resource_id: str) -> list[LineageEdgeRow]:
        return [edge for edge in edges if edge["from_resource_id"] in {resource_id, "other-v1"}]

    return lookup
