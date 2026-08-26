"""Immutable 24-hour phase and event plan for the Mac mini enterprise campaign."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

CAMPAIGN_DURATION_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class CampaignPhase:
    phase_id: str
    start_second: int
    end_second: int
    purpose: str


@dataclass(frozen=True, slots=True)
class CampaignEvent:
    event_id: str
    phase_id: str
    offset_second: int
    kind: str
    value: str
    fault_window_seconds: int = 0
    injection_seconds: int = 30


PHASES = (
    CampaignPhase("baseline", 0, 2 * 3600, "Closed-loop latency and resource baseline"),
    CampaignPhase("compute", 2 * 3600, 5 * 3600, "Pod, process, signal, and OOM recovery"),
    CampaignPhase(
        "dependencies",
        5 * 3600,
        8 * 3600,
        "Stateful and runtime dependency fail-closed recovery",
    ),
    CampaignPhase(
        "network",
        8 * 3600,
        11 * 3600,
        "Latency, packet loss, reset, DNS, and partition",
    ),
    CampaignPhase("multi-tenant", 11 * 3600, 14 * 3600, "Tenant fairness and cross-tenant denial"),
    CampaignPhase("security-time", 14 * 3600, 17 * 3600, "Identity rotation and expiry boundaries"),
    CampaignPhase(
        "release",
        17 * 3600,
        20 * 3600,
        "Rollout, rejection, rollback, and migration safety",
    ),
    CampaignPhase("dr", 20 * 3600, 22 * 3600, "Encrypted backup and non-destructive recovery"),
    CampaignPhase("quiet", 22 * 3600, 24 * 3600, "Backlog drain and final invariant scan"),
)

EVENTS = (
    CampaignEvent("api-pod-delete", "compute", 2 * 3600 + 600, "fault", "api-pod", 240),
    CampaignEvent("worker-pod-delete", "compute", 2 * 3600 + 1800, "fault", "worker-pod", 240),
    CampaignEvent(
        "controller-pod-delete",
        "compute",
        2 * 3600 + 3000,
        "fault",
        "controller-pod",
        240,
    ),
    CampaignEvent("api-sigterm", "compute", 3 * 3600 + 600, "fault", "api-sigterm", 240),
    CampaignEvent("worker-sigkill", "compute", 3 * 3600 + 2100, "fault", "worker-sigkill", 240),
    CampaignEvent("controller-sigkill", "compute", 4 * 3600, "fault", "controller-sigkill", 240),
    CampaignEvent("worker-oom", "compute", 4 * 3600 + 1500, "fault", "worker-oom", 360, 45),
    CampaignEvent(
        "postgresql-stop",
        "dependencies",
        5 * 3600 + 600,
        "fault",
        "dependency-postgresql",
        480,
    ),
    CampaignEvent("minio-stop", "dependencies", 5 * 3600 + 2100, "fault", "dependency-minio", 480),
    CampaignEvent("temporal-stop", "dependencies", 6 * 3600, "fault", "dependency-temporal", 480),
    CampaignEvent(
        "redpanda-stop",
        "dependencies",
        6 * 3600 + 1500,
        "fault",
        "dependency-redpanda",
        480,
    ),
    CampaignEvent(
        "elasticsearch-stop",
        "dependencies",
        6 * 3600 + 3000,
        "fault",
        "dependency-elasticsearch",
        480,
    ),
    CampaignEvent("clamav-stop", "dependencies", 7 * 3600 + 900, "fault", "dependency-clamav", 480),
    CampaignEvent("network-latency", "network", 8 * 3600 + 600, "fault", "network-latency", 360),
    CampaignEvent(
        "network-packet-loss",
        "network",
        8 * 3600 + 2100,
        "fault",
        "network-packet-loss",
        360,
    ),
    CampaignEvent(
        "network-connection-reset",
        "network",
        9 * 3600,
        "fault",
        "network-connection-reset",
        360,
    ),
    CampaignEvent(
        "network-dns-failure",
        "network",
        9 * 3600 + 1500,
        "fault",
        "network-dns-failure",
        360,
    ),
    CampaignEvent(
        "network-full-partition",
        "network",
        9 * 3600 + 3000,
        "fault",
        "network-full-partition",
        360,
    ),
    CampaignEvent(
        "network-api-worker-isolation",
        "network",
        10 * 3600 + 1200,
        "fault",
        "network-api-worker",
        360,
    ),
    CampaignEvent(
        "tenant-a-flood-tenant-b-business",
        "multi-tenant",
        11 * 3600 + 600,
        "tenant-stress",
        "live",
        0,
        600,
    ),
    CampaignEvent("postgres-jsonb-index-rls", "multi-tenant", 13 * 3600, "postgres-rls", "live"),
    CampaignEvent(
        "mcp-tenant-quota-fairness",
        "multi-tenant",
        13 * 3600 + 1800,
        "mcp-quota",
        "deployed-durable-rate-limiter",
    ),
    CampaignEvent(
        "security-time-runtime",
        "security-time",
        14 * 3600 + 600,
        "security-time",
        "deployed-image",
    ),
    CampaignEvent(
        "external-oidc-network-path",
        "security-time",
        16 * 3600 + 1800,
        "external-oidc-fault",
        "dependency-keycloak",
        480,
        45,
    ),
    CampaignEvent(
        "same-digest-rolling-restart",
        "release",
        17 * 3600 + 600,
        "fault",
        "rolling-restart",
        420,
    ),
    CampaignEvent("bad-image", "release", 17 * 3600 + 2700, "fault", "invalid-image", 420),
    CampaignEvent("bad-config", "release", 18 * 3600 + 1800, "fault", "bad-config", 420),
    CampaignEvent("migration-failure", "release", 19 * 3600, "fault", "migration-failure", 600),
    CampaignEvent(
        "verified-digest-rollback",
        "release",
        19 * 3600 + 1200,
        "fault",
        "verified-digest-rollback",
        2400,
    ),
    CampaignEvent(
        "backup-restore-recovery",
        "dr",
        20 * 3600 + 300,
        "dr",
        "encrypted-nondestructive",
        6600,
    ),
)


def build_campaign_plan(started_at: datetime) -> dict[str, object]:
    _validate_plan()
    phases = [
        {
            **asdict(phase),
            "startedAt": (started_at + timedelta(seconds=phase.start_second)).isoformat(),
            "endedAt": (started_at + timedelta(seconds=phase.end_second)).isoformat(),
        }
        for phase in PHASES
    ]
    events = [
        {
            **asdict(event),
            "scheduledAt": (started_at + timedelta(seconds=event.offset_second)).isoformat(),
        }
        for event in EVENTS
    ]
    return {
        "schemaVersion": 1,
        "durationSeconds": CAMPAIGN_DURATION_SECONDS,
        "startedAt": started_at.isoformat(),
        "endedAt": (started_at + timedelta(seconds=CAMPAIGN_DURATION_SECONDS)).isoformat(),
        "phases": phases,
        "events": events,
        "quietSoakSeconds": 2 * 3600,
    }


def fault_windows(started_at: datetime) -> list[dict[str, object]]:
    return [
        {
            "eventId": event.event_id,
            "startedAt": (started_at + timedelta(seconds=event.offset_second)).isoformat(),
            "endedAt": (started_at + timedelta(seconds=event.offset_second + event.fault_window_seconds)).isoformat(),
        }
        for event in EVENTS
        if event.fault_window_seconds > 0
    ]


def _validate_plan() -> None:
    _validate_phase_boundaries()
    phases = _validated_phase_index()
    for event in EVENTS:
        _validate_event(event, phases)


def _validate_phase_boundaries() -> None:
    if PHASES[0].start_second != 0 or PHASES[-1].end_second != CAMPAIGN_DURATION_SECONDS:
        raise RuntimeError("macmini_campaign_phase_boundary_invalid")
    if any(left.end_second != right.start_second for left, right in zip(PHASES, PHASES[1:], strict=False)):
        raise RuntimeError("macmini_campaign_phase_gap")


def _validated_phase_index() -> dict[str, CampaignPhase]:
    phases = {phase.phase_id: phase for phase in PHASES}
    event_ids = {event.event_id for event in EVENTS}
    if len(phases) != len(PHASES) or len(event_ids) != len(EVENTS):
        raise RuntimeError("macmini_campaign_identifier_duplicate")
    return phases


def _validate_event(event: CampaignEvent, phases: dict[str, CampaignPhase]) -> None:
    phase = phases.get(event.phase_id)
    if phase is None or not phase.start_second <= event.offset_second < phase.end_second:
        raise RuntimeError("macmini_campaign_event_outside_phase")
    if event.fault_window_seconds and event.offset_second + event.fault_window_seconds > phase.end_second:
        raise RuntimeError("macmini_campaign_fault_window_outside_phase")
