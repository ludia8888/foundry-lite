from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, TypedDict

AdminCapabilityStatus = Literal["api_available", "worker_entrypoint", "cli_only", "runbook_only", "future"]
AdminExecutionSurface = Literal["browser_sdk", "worker_entrypoint", "cli", "runbook", "future"]
AdminTaskArea = Literal["overview", "event_plane", "worker", "migration", "bootstrap", "workspace"]


class AdminOperationCapability(TypedDict):
    id: str
    title: str
    status: AdminCapabilityStatus
    summary: str
    sdkSurface: str | None
    apiRoute: str | None
    command: str | None
    evidencePath: str | None
    safetyBoundary: str
    nextSurface: str | None
    executionSurface: AdminExecutionSurface
    canStartFromBrowser: bool
    requiresApproval: bool
    operatorChecklist: list[str]
    blockingReason: str | None


class AdminReadinessSummary(TypedDict):
    apiAvailable: int
    workerEntrypoints: int
    cliOnly: int
    runbookOnly: int
    future: int


class AdminReadinessOverview(TypedDict):
    generatedAt: str
    summary: AdminReadinessSummary
    capabilities: list[AdminOperationCapability]


class AdminOperationTask(TypedDict):
    id: str
    capabilityId: str
    title: str
    area: AdminTaskArea
    status: AdminCapabilityStatus
    executionSurface: AdminExecutionSurface
    canStartFromBrowser: bool
    requiresApproval: bool
    primarySurface: str | None
    sdkSurface: str | None
    apiRoute: str | None
    command: str | None
    evidencePath: str | None
    operatorChecklist: list[str]
    requiredBackendSurface: str | None
    blockingReason: str | None


class AdminTaskPlanSummary(TypedDict):
    browserRunnable: int
    operatorRequired: int
    futureBackendSurfaces: int


class AdminTaskPlan(TypedDict):
    generatedAt: str
    summary: AdminTaskPlanSummary
    tasks: list[AdminOperationTask]


_ADMIN_CAPABILITY_ROWS: tuple[AdminOperationCapability, ...] = (
    {
        "id": "operations-admin-overview",
        "title": "Operations admin overview",
        "status": "api_available",
        "summary": "Frontend admin screens can read current operations capabilities through the named SDK.",
        "sdkSurface": "operations.admin.overview",
        "apiRoute": "GET /api/operations/admin/overview",
        "command": None,
        "evidencePath": "tests/sdk/request_contract.mjs",
        "safetyBoundary": "Read-only; no infrastructure state is changed.",
        "nextSurface": None,
        "executionSurface": "browser_sdk",
        "canStartFromBrowser": True,
        "requiresApproval": False,
        "operatorChecklist": [
            "Render as a read-only admin inventory.",
            "Do not treat the overview itself as proof that blocked operations can run from the browser.",
        ],
        "blockingReason": None,
    },
    {
        "id": "bounded-outbox-publish",
        "title": "Bounded outbox publish batch",
        "status": "api_available",
        "summary": "Operations can publish one bounded outbox batch and inspect durable event or DLQ evidence.",
        "sdkSurface": "operations.outbox.publishPending",
        "apiRoute": "POST /api/operations/outbox/publish",
        "command": "pnpm worker:outbox-publisher",
        "evidencePath": "artifacts/operations/outbox_publisher_summary.json",
        "safetyBoundary": "Bounded batch only; always-on daemon scheduling remains an operator deployment concern.",
        "nextSurface": None,
        "executionSurface": "browser_sdk",
        "canStartFromBrowser": True,
        "requiresApproval": False,
        "operatorChecklist": [
            "Show the bounded batch limit before publishing.",
            "Display published, failed, skipped, and DLQ evidence after the call.",
        ],
        "blockingReason": None,
    },
    {
        "id": "outbox-publisher-worker",
        "title": "Outbox publisher worker",
        "status": "worker_entrypoint",
        "summary": "A worker entrypoint can drain pending outbox rows through the same CAS-backed service path.",
        "sdkSurface": None,
        "apiRoute": None,
        "command": "pnpm worker:outbox-publisher",
        "evidencePath": "artifacts/operations/outbox_publisher_summary.json",
        "safetyBoundary": (
            "Bounded worker proof exists; always-on daemon lifecycle and pause/resume automation are future work."
        ),
        "nextSurface": "operations.admin.workers",
        "executionSurface": "worker_entrypoint",
        "canStartFromBrowser": False,
        "requiresApproval": True,
        "operatorChecklist": [
            "Show the worker command and evidence output path.",
            "Require an operator deployment surface before exposing start, stop, or pause controls.",
        ],
        "blockingReason": "Worker daemon lifecycle is not browser-safe until a privileged worker-control API exists.",
    },
    {
        "id": "stream-archive-worker",
        "title": "Stream archive worker",
        "status": "worker_entrypoint",
        "summary": (
            "A worker entrypoint archives stream batches through the dataset transaction and checkpoint boundary."
        ),
        "sdkSurface": None,
        "apiRoute": None,
        "command": "pnpm worker:stream-archive",
        "evidencePath": "artifacts/operations/stream_archive_worker_summary.json",
        "safetyBoundary": (
            "Bounded loop proof exists; CDC object-indexer daemon mode and broker fencing remain future work."
        ),
        "nextSurface": "operations.admin.workers",
        "executionSurface": "worker_entrypoint",
        "canStartFromBrowser": False,
        "requiresApproval": True,
        "operatorChecklist": [
            "Show stream name, tenant context, and checkpoint evidence before running.",
            "Keep CDC daemon management out of browser UI until lease and fencing controls exist.",
        ],
        "blockingReason": (
            "Stream worker lifecycle requires deployment-owned daemon control, not a direct browser button."
        ),
    },
    {
        "id": "schema-migration-runner",
        "title": "Schema migration runner",
        "status": "cli_only",
        "summary": (
            "Schema migrations run through the dedicated singleton-lock runner, not API startup or browser calls."
        ),
        "sdkSurface": None,
        "apiRoute": None,
        "command": "pnpm db:migrate",
        "evidencePath": "artifacts/operations/migration_run.json",
        "safetyBoundary": "CLI-only until a privileged migration API has audit, approval, rollback, and lock evidence.",
        "nextSurface": "operations.admin.migrations",
        "executionSurface": "cli",
        "canStartFromBrowser": False,
        "requiresApproval": True,
        "operatorChecklist": [
            "Show the singleton-lock evidence file after the run.",
            "Require explicit approval and rollback/runbook context before any future browser migration control.",
        ],
        "blockingReason": (
            "Migration execution can change schema and remains CLI-only until privileged approval exists."
        ),
    },
    {
        "id": "infra-bootstrap",
        "title": "Infrastructure bootstrap",
        "status": "runbook_only",
        "summary": "Local runtime bootstrap is still runbook/package-script driven rather than an admin API.",
        "sdkSurface": None,
        "apiRoute": None,
        "command": "pnpm dev",
        "evidencePath": None,
        "safetyBoundary": (
            "Bootstrap can create or connect infrastructure and must stay outside unreviewed browser control."
        ),
        "nextSurface": "operations.admin.bootstrap",
        "executionSurface": "runbook",
        "canStartFromBrowser": False,
        "requiresApproval": True,
        "operatorChecklist": [
            "Show this as a runbook step rather than an executable browser action.",
            "Keep credentials, container startup, and infrastructure connection state outside frontend code.",
        ],
        "blockingReason": "Infra bootstrap is a runbook operation until a hardened bootstrap admin service exists.",
    },
    {
        "id": "full-visual-operations-workspace",
        "title": "Full visual operations workspace",
        "status": "future",
        "summary": (
            "The backend and SDK have building blocks, but the complete visual control room is not current proof."
        ),
        "sdkSurface": None,
        "apiRoute": None,
        "command": None,
        "evidencePath": None,
        "safetyBoundary": "Requires screen-level authorization, irreversible-action UX, and operator evidence design.",
        "nextSurface": "operations.workspace",
        "executionSurface": "future",
        "canStartFromBrowser": False,
        "requiresApproval": True,
        "operatorChecklist": [
            "Keep the full workspace visible as roadmap work.",
            "Do not label partial admin cards as a finished visual operations workspace.",
        ],
        "blockingReason": "The full visual operations workspace is not implemented yet.",
    },
)


def build_admin_readiness_overview(*, generated_at: str | None = None) -> AdminReadinessOverview:
    capabilities = _admin_capabilities()
    return {
        "generatedAt": generated_at or _utc_now(),
        "summary": _summary(capabilities),
        "capabilities": capabilities,
    }


def build_admin_task_plan(*, generated_at: str | None = None) -> AdminTaskPlan:
    tasks = [_admin_task(capability) for capability in _admin_capabilities()]
    return {
        "generatedAt": generated_at or _utc_now(),
        "summary": _task_summary(tasks),
        "tasks": tasks,
    }


def _admin_capabilities() -> list[AdminOperationCapability]:
    return [
        {**capability, "operatorChecklist": list(capability["operatorChecklist"])}
        for capability in _ADMIN_CAPABILITY_ROWS
    ]


def _admin_task(capability: AdminOperationCapability) -> AdminOperationTask:
    return {
        "id": f"{capability['id']}-task",
        "capabilityId": capability["id"],
        "title": capability["title"],
        "area": _task_area(capability["id"]),
        "status": capability["status"],
        "executionSurface": capability["executionSurface"],
        "canStartFromBrowser": capability["canStartFromBrowser"],
        "requiresApproval": capability["requiresApproval"],
        "primarySurface": _primary_surface(capability),
        "sdkSurface": capability["sdkSurface"],
        "apiRoute": capability["apiRoute"],
        "command": capability["command"],
        "evidencePath": capability["evidencePath"],
        "operatorChecklist": list(capability["operatorChecklist"]),
        "requiredBackendSurface": capability["nextSurface"],
        "blockingReason": capability["blockingReason"],
    }


def _primary_surface(capability: AdminOperationCapability) -> str | None:
    return capability["sdkSurface"] or capability["command"] or capability["apiRoute"]


def _task_area(capability_id: str) -> AdminTaskArea:
    if capability_id == "bounded-outbox-publish":
        return "event_plane"
    if capability_id.endswith("-worker"):
        return "worker"
    if capability_id == "schema-migration-runner":
        return "migration"
    if capability_id == "infra-bootstrap":
        return "bootstrap"
    if capability_id == "full-visual-operations-workspace":
        return "workspace"
    return "overview"


def _task_summary(tasks: list[AdminOperationTask]) -> AdminTaskPlanSummary:
    required_surfaces = {task["requiredBackendSurface"] for task in tasks if task["requiredBackendSurface"]}
    return {
        "browserRunnable": sum(1 for task in tasks if task["canStartFromBrowser"]),
        "operatorRequired": sum(1 for task in tasks if task["requiresApproval"]),
        "futureBackendSurfaces": len(required_surfaces),
    }


def _summary(capabilities: list[AdminOperationCapability]) -> AdminReadinessSummary:
    return {
        "apiAvailable": _count(capabilities, "api_available"),
        "workerEntrypoints": _count(capabilities, "worker_entrypoint"),
        "cliOnly": _count(capabilities, "cli_only"),
        "runbookOnly": _count(capabilities, "runbook_only"),
        "future": _count(capabilities, "future"),
    }


def _count(capabilities: list[AdminOperationCapability], status: AdminCapabilityStatus) -> int:
    return sum(1 for capability in capabilities if capability["status"] == status)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
