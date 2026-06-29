from foundry_lite.application.admin_overview import build_admin_readiness_overview, build_admin_task_plan


def test_admin_readiness_overview_separates_current_and_future_admin_surfaces() -> None:
    overview = build_admin_readiness_overview(generated_at="2026-06-28T00:00:00Z")
    capabilities = {row["id"]: row for row in overview["capabilities"]}

    assert overview["generatedAt"] == "2026-06-28T00:00:00Z"
    assert overview["summary"] == {
        "apiAvailable": 2,
        "workerEntrypoints": 2,
        "cliOnly": 1,
        "runbookOnly": 1,
        "future": 1,
    }
    assert capabilities["operations-admin-overview"]["sdkSurface"] == "operations.admin.overview"
    assert capabilities["operations-admin-overview"]["executionSurface"] == "browser_sdk"
    assert capabilities["operations-admin-overview"]["canStartFromBrowser"] is True
    assert capabilities["bounded-outbox-publish"]["apiRoute"] == "POST /api/operations/outbox/publish"
    assert capabilities["bounded-outbox-publish"]["operatorChecklist"]
    assert capabilities["schema-migration-runner"]["command"] == "pnpm db:migrate"
    assert capabilities["schema-migration-runner"]["status"] == "cli_only"
    assert capabilities["schema-migration-runner"]["requiresApproval"] is True
    assert capabilities["schema-migration-runner"]["canStartFromBrowser"] is False
    assert "CLI-only" in str(capabilities["schema-migration-runner"]["blockingReason"])
    assert capabilities["full-visual-operations-workspace"]["status"] == "future"


def test_admin_task_plan_maps_admin_surfaces_to_screen_tasks() -> None:
    plan = build_admin_task_plan(generated_at="2026-06-28T00:00:00Z")
    tasks = {row["capabilityId"]: row for row in plan["tasks"]}

    assert plan["generatedAt"] == "2026-06-28T00:00:00Z"
    assert plan["summary"] == {
        "browserRunnable": 2,
        "operatorRequired": 5,
        "futureBackendSurfaces": 4,
    }
    assert tasks["bounded-outbox-publish"]["area"] == "event_plane"
    assert tasks["bounded-outbox-publish"]["canStartFromBrowser"] is True
    assert tasks["bounded-outbox-publish"]["primarySurface"] == "operations.outbox.publishPending"
    assert tasks["outbox-publisher-worker"]["area"] == "worker"
    assert tasks["outbox-publisher-worker"]["requiresApproval"] is True
    assert tasks["schema-migration-runner"]["area"] == "migration"
    assert tasks["schema-migration-runner"]["requiredBackendSurface"] == "operations.admin.migrations"
    assert tasks["infra-bootstrap"]["area"] == "bootstrap"
    assert tasks["full-visual-operations-workspace"]["area"] == "workspace"
