"""Resolve Source control-plane records into fail-closed worker network routes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from foundry_lite.application.ports import (
    ConnectorNetworkRoute,
    SourceConnectionRow,
    SourceManagementRepository,
    TransactionManager,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

AGENT_HEARTBEAT_MAX_AGE_SECONDS = 90


def resolve_source_network_route(
    engine: TransactionManager,
    repository: SourceManagementRepository,
    ctx: RequestContext,
    source: SourceConnectionRow,
) -> ConnectorNetworkRoute:
    config = source["config_summary"]
    mode = _connection_mode(config)
    policy_name = _optional_text(config.get("networkPolicyName"))
    if policy_name is None:
        return _route_without_policy(mode, config)
    with engine.begin() as conn:
        policy = repository.network_policy_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            policy_name=policy_name,
        )
        if policy is None:
            raise _routing_error(mode, policy_name, None, "NETWORK_POLICY_NOT_FOUND")
        if policy["status"] != "active" or policy["mode"] != mode:
            raise _routing_error(mode, policy_name, policy["agent_id"], "NETWORK_POLICY_INACTIVE")
        if mode == "direct":
            return _direct_route(policy_name, policy["allowed_hosts"])
        agent = repository.agent_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            agent_id=_required_agent_id(config, policy["agent_id"]),
        )
    return _agent_route(policy_name, policy, agent)


def _connection_mode(config: Mapping[str, object]) -> str:
    value = config.get("connectionMode")
    if value in {"direct", "agent_proxy"}:
        return str(value)
    return "agent_proxy" if _optional_text(config.get("agentId")) else "direct"


def _route_without_policy(mode: str, config: Mapping[str, object]) -> ConnectorNetworkRoute:
    if mode == "direct":
        return ConnectorNetworkRoute(mode="direct")
    agent_id = _optional_text(config.get("agentId"))
    raise _routing_error(mode, None, agent_id, "NETWORK_POLICY_REQUIRED")


def _direct_route(policy_name: str, allowed_hosts: Mapping[str, object]) -> ConnectorNetworkRoute:
    return ConnectorNetworkRoute(
        mode="direct",
        policy_name=policy_name,
        allowed_destinations=_allowed_destinations(allowed_hosts),
    )


def _required_agent_id(config: Mapping[str, object], policy_agent_id: str | None) -> str:
    configured = _optional_text(config.get("agentId"))
    if configured is None or configured != policy_agent_id:
        raise _routing_error(
            "agent_proxy", _optional_text(config.get("networkPolicyName")), configured, "AGENT_MISMATCH"
        )
    return configured


def _agent_route(
    policy_name: str,
    policy: Mapping[str, object],
    agent: Mapping[str, object] | None,
) -> ConnectorNetworkRoute:
    agent_id = _optional_text(policy.get("agent_id"))
    if agent is None or not _is_agent_fresh(agent):
        raise _routing_error("agent_proxy", policy_name, agent_id, "AGENT_OFFLINE")
    network_summary = _mapping(agent.get("network_summary"))
    proxy_url = _optional_text(network_summary.get("proxyUrl"))
    if proxy_url is None:
        raise _routing_error("agent_proxy", policy_name, agent_id, "AGENT_PROXY_URL_MISSING")
    return ConnectorNetworkRoute(
        mode="agent_proxy",
        policy_name=policy_name,
        agent_id=agent_id,
        proxy_url=proxy_url,
        allowed_destinations=_allowed_destinations(_mapping(policy.get("allowed_hosts"))),
    )


def _is_agent_fresh(agent: Mapping[str, object]) -> bool:
    if agent.get("status") != "online":
        return False
    heartbeat = _optional_text(agent.get("last_heartbeat_at"))
    if heartbeat is None:
        return False
    try:
        timestamp = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds() <= AGENT_HEARTBEAT_MAX_AGE_SECONDS


def _allowed_destinations(value: Mapping[str, object]) -> tuple[str, ...]:
    hosts = value.get("hosts")
    if not isinstance(hosts, list):
        return ()
    return tuple(item.strip() for item in hosts if isinstance(item, str) and item.strip())


def _routing_error(mode: str, policy_name: str | None, agent_id: str | None, flag: str) -> ValidationFailed:
    is_agent = mode == "agent_proxy"
    evidence = {
        "egressSucceeded": False,
        "responseFlags": flag,
        "bytesSent": 0,
        "bytesReceived": 0,
        "durationMs": 0,
        "destinationPort": 0,
        "origin": "agent-proxy" if is_agent else "connectivity-sidecar",
        "networkType": mode,
        "networkResources": {"networkPolicy": policy_name, "agentId": agent_id},
    }
    return ValidationFailed(
        "Source network route is not ready",
        details={"networkStage": "route_resolution", "networkEvidence": evidence},
    )


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}
