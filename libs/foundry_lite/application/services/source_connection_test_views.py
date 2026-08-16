"""Views and five-stage status semantics for Source connection diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from foundry_lite.application.ports import ConnectorNetworkRoute, SourceConnectionRow
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.errors import FoundryLiteError


@dataclass(frozen=True)
class SourceProbeOutcome:
    details: Mapping[str, object]
    credential_status: str
    credential_detail: str


def stream_network_evidence(
    route: ConnectorNetworkRoute,
    bootstrap_servers: str,
    connection_id: str,
    duration_ms: int,
) -> dict[str, object]:
    destination_host, destination_port = _bootstrap_destination(bootstrap_servers)
    return {
        "connectionId": f"{connection_id}:kafka",
        "egressSucceeded": True,
        "responseFlags": "NONE",
        "bytesSent": 0,
        "bytesReceived": 0,
        "durationMs": duration_ms,
        "destinationHost": destination_host,
        "destinationPort": destination_port,
        "origin": "connectivity-sidecar",
        "networkType": route.mode,
        "networkResources": {"networkPolicy": route.policy_name, "agentId": route.agent_id},
    }


def _bootstrap_destination(bootstrap_servers: str) -> tuple[str, int]:
    first_server = bootstrap_servers.split(",", 1)[0].strip()
    split = urlsplit(first_server if "://" in first_server else f"tcp://{first_server}")
    return split.hostname or first_server, split.port or 9092


def connection_checks(
    source: SourceConnectionRow,
    outcome: SourceProbeOutcome | None,
    error: FoundryLiteError | None,
    *,
    request_id: str,
) -> dict[str, object]:
    evidence = network_evidence(outcome, error)
    items = [
        _check("source_config", "Source endpoint", "succeeded", f"{source['kind']} · 설정 지문 일치"),
        _check("network_route", "Network route", _network_status(evidence), _network_detail(evidence, error)),
        _check("worker_runtime", "Foundry worker", "succeeded", _worker_detail(evidence)),
        _check("credential", "Credential", _credential_status(outcome, error), _credential_detail(outcome, error)),
        _check("source_preview", "Source preview", _preview_status(outcome), _preview_detail(outcome, error)),
    ]
    probe = dict(outcome.details) if outcome is not None else {"networkEvidence": evidence}
    return {
        "requestId": request_id,
        "summary": {"passed": sum(item["status"] == "succeeded" for item in items), "total": len(items)},
        "items": items,
        "probe": probe,
    }


def network_evidence(
    outcome: SourceProbeOutcome | None,
    error: FoundryLiteError | None,
) -> dict[str, object]:
    if outcome is not None:
        value = outcome.details.get("networkEvidence")
        return dict(value) if isinstance(value, Mapping) else {}
    if error is None:
        return {}
    value = error.details.get("networkEvidence")
    return dict(value) if isinstance(value, Mapping) else {}


def egress_attempt_view(row: Mapping[str, object]) -> dict[str, object]:
    checks = _mapping(row.get("checks"))
    probe = _mapping(checks.get("probe"))
    evidence = _mapping(probe.get("networkEvidence"))
    if not evidence:
        evidence = _mapping(_mapping(_mapping(row.get("error")).get("details")).get("networkEvidence"))
    return {
        "connectionId": evidence.get("connectionId") or f"{row['id']}:network",
        "connectionTestId": row["id"],
        "sourceName": row["source_name"],
        "status": "succeeded" if evidence.get("egressSucceeded") is True else "failed",
        "responseFlags": evidence.get("responseFlags") or "NO_EGRESS_EVIDENCE",
        "bytesSent": _integer(evidence.get("bytesSent")),
        "bytesReceived": _integer(evidence.get("bytesReceived")),
        "durationMs": _integer(evidence.get("durationMs")),
        "destinationPort": _integer(evidence.get("destinationPort")),
        "origin": evidence.get("origin") or "connectivity-sidecar",
        "networkType": evidence.get("networkType") or "direct",
        "networkResources": dict(_mapping(evidence.get("networkResources"))),
        "requestId": checks.get("requestId"),
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


def _network_status(evidence: Mapping[str, object]) -> str:
    return "succeeded" if evidence.get("egressSucceeded") is True else "failed"


def _network_detail(evidence: Mapping[str, object], error: FoundryLiteError | None) -> str:
    if evidence.get("egressSucceeded") is True:
        if evidence.get("networkType") == "agent_proxy":
            return "Foundry worker가 Agent CONNECT 터널을 통해 destination TCP endpoint까지 도달했습니다."
        return "Foundry worker가 destination TCP endpoint까지 직접 도달했습니다."
    flag = evidence.get("responseFlags")
    return (
        f"네트워크 경로 실패 · {flag}" if flag else (scrub_error_text(error.message) if error else "네트워크 경로 실패")
    )


def _worker_detail(evidence: Mapping[str, object]) -> str:
    if evidence.get("networkType") == "agent_proxy":
        return "Foundry worker가 프로토콜과 TLS를 실행하고 Agent는 투명 TCP 터널만 제공했습니다."
    return "Foundry worker가 실제 연결 probe를 실행했습니다."


def _credential_status(outcome: SourceProbeOutcome | None, error: FoundryLiteError | None) -> str:
    if outcome is not None:
        return outcome.credential_status
    if error is not None and error.details.get("networkStage") == "http":
        return "failed"
    return "not_verified"


def _credential_detail(outcome: SourceProbeOutcome | None, error: FoundryLiteError | None) -> str:
    if outcome is not None:
        return outcome.credential_detail
    if error is not None and error.details.get("networkStage") == "http":
        return "endpoint가 HTTP 응답을 반환했지만 요청 검증 또는 인증이 실패했습니다."
    return "네트워크 또는 TLS 단계 때문에 인증 결과를 확정하지 못했습니다."


def _preview_status(outcome: SourceProbeOutcome | None) -> str:
    return "succeeded" if outcome is not None else "failed"


def _preview_detail(outcome: SourceProbeOutcome | None, error: FoundryLiteError | None) -> str:
    if outcome is not None:
        rows = outcome.details.get("rowCount")
        resources = outcome.details.get("visibleResourceCount")
        if isinstance(rows, int):
            return f"{rows}개 row를 읽었고 Dataset commit은 만들지 않았습니다."
        if isinstance(resources, int):
            return f"{resources}개 resource를 볼 수 있고 Dataset commit은 만들지 않았습니다."
    return scrub_error_text(error.message) if error is not None else "Source preview를 완료하지 못했습니다."


def _check(key: str, label: str, status: str, detail: str) -> dict[str, object]:
    return {"key": key, "label": label, "status": status, "detail": detail}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: object) -> int:
    return value if isinstance(value, int) else 0
