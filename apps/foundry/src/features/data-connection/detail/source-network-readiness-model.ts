import type {
  SourceAgent,
  SourceConnection,
  SourceConnectionTestCheck,
  SourceConnectionTestResult,
  SourceNetworkPolicy,
} from "@foundry-lite/sdk";

import { formatTimestamp, readTextField } from "../source-model";

export type ReadinessState = "ready" | "warning" | "blocked" | "pending";

export function sourceDestination(baseUrl: string | null): string | null {
  if (!baseUrl) return null;
  try {
    return new URL(baseUrl.includes("://") ? baseUrl : `http://${baseUrl}`).host;
  } catch {
    return null;
  }
}

export function sourceEndpoint(
  source: SourceConnection,
  baseUrl: string | null,
): string | null {
  if (baseUrl) return baseUrl;
  const bootstrapServers = readTextField(
    source.configSummary,
    "bootstrapServers",
  );
  if (bootstrapServers) return `Kafka · ${bootstrapServers}`;
  const secretRef = readTextField(
    source.configSummary,
    "databaseUrlSecretRef",
  );
  return secretRef ? `database URL · secretRef ${secretRef}` : null;
}

export function diagnosticState(
  result: SourceConnectionTestResult | null,
  key: string,
  fallback: ReadinessState,
): ReadinessState {
  const status = testCheck(result, key)?.status.toLowerCase();
  if (status === "succeeded") return "ready";
  if (status === "failed") return "blocked";
  if (status === "not_verified") return "warning";
  return fallback;
}

export function diagnosticDetail(
  result: SourceConnectionTestResult | null,
  key: string,
  fallback: string,
): string {
  return testCheck(result, key)?.detail ?? fallback;
}

export function resolvePolicy(
  policies: readonly SourceNetworkPolicy[],
  policyName: string | null,
  mode: string,
  destination: string | null,
): SourceNetworkPolicy | null {
  const namedPolicy = policies.find((policy) => policy.policyName === policyName);
  if (namedPolicy) return namedPolicy;
  return (
    policies.find(
      (policy) =>
        policy.mode === mode &&
        destination !== null &&
        policyAllowsDestination(policy, destination),
    ) ?? null
  );
}

export function resolveAgent(
  agents: readonly SourceAgent[],
  agentId: string | null,
): SourceAgent | null {
  return agents.find((agent) => agent.agentId === agentId) ?? null;
}

export function networkPolicyState(
  policy: SourceNetworkPolicy | null,
  destination: string | null,
  routeMode: string,
): ReadinessState {
  if (!policy) return routeMode === "agent_proxy" ? "blocked" : "warning";
  if (policy.status.toLowerCase() !== "active") return "blocked";
  if (!destination) return "warning";
  const hosts = policyHosts(policy);
  if (hosts.length === 0) {
    return routeMode === "agent_proxy" ? "blocked" : "ready";
  }
  return policyAllowsDestination(policy, destination) ? "ready" : "blocked";
}

export function policyDetail(
  policy: SourceNetworkPolicy | null,
  destination: string | null,
  routeMode: string,
): string {
  if (!policy) {
    return routeMode === "agent_proxy"
      ? "Agent 경로에 필요한 egress policy를 찾지 못했습니다."
      : "명시적 정책 없이 direct egress를 사용합니다.";
  }
  if (!destination) {
    const hosts = policyHosts(policy);
    return `${policy.policyName} · ${hosts.length > 0 ? `allowlist ${hosts.join(", ")}` : "endpoint host 확인 불가"}`;
  }
  if (!policyAllowsDestination(policy, destination)) {
    return `${policy.policyName} · ${destination}이 allowlist에 없습니다.`;
  }
  const hosts = policyHosts(policy);
  return `${policy.policyName} · ${hosts.length > 0 ? hosts.join(", ") : "direct unrestricted"}`;
}

export function policyHostSummary(
  policy: SourceNetworkPolicy | null,
): string | null {
  if (!policy) return null;
  const hosts = policyHosts(policy);
  return hosts.length > 0 ? hosts.join(", ") : null;
}

export function agentReadiness(
  routeMode: string,
  agent: SourceAgent | null,
): ReadinessState {
  if (routeMode !== "agent_proxy") return "ready";
  if (!agent || agent.status.toLowerCase() !== "online") return "blocked";
  return hasRecentHeartbeat(agent.lastHeartbeatAt) ? "ready" : "blocked";
}

export function agentDetail(
  routeMode: string,
  agent: SourceAgent | null,
): string {
  if (routeMode !== "agent_proxy") {
    return "Foundry worker가 프로토콜과 TLS를 직접 실행합니다.";
  }
  if (!agent) return "선택된 Agent를 찾지 못했습니다.";
  if (!agent.lastHeartbeatAt) {
    return `${agent.agentId} · daemon heartbeat가 없습니다.`;
  }
  return `${agent.agentId} · ${agent.status} · ${formatTimestamp(agent.lastHeartbeatAt)}`;
}

export function previewDetail(
  result: SourceConnectionTestResult | null,
): string {
  if (!result) return "아직 실행하지 않았습니다. 연결 진단을 실행하세요.";
  const preview = testCheck(result, "source_preview");
  if (preview) return preview.detail;
  return (
    readTextField(result.error, "message") ??
    readTextField(result.error, "detail") ??
    "외부 endpoint 요청에 실패했습니다."
  );
}

function testCheck(
  result: SourceConnectionTestResult | null,
  key: string,
): SourceConnectionTestCheck | null {
  return result?.checks.items.find((item) => item.key === key) ?? null;
}

function policyHosts(policy: SourceNetworkPolicy): string[] {
  const hosts = policy.allowedHosts["hosts"] ?? policy.allowedHosts;
  if (Array.isArray(hosts)) return hosts.map(String);
  return Object.values(policy.allowedHosts).flatMap((value) =>
    Array.isArray(value) ? value.map(String) : [String(value)],
  );
}

function policyAllowsDestination(
  policy: SourceNetworkPolicy,
  destination: string,
): boolean {
  return policyHosts(policy).some((candidate) =>
    destinationMatches(candidate, destination),
  );
}

function destinationMatches(candidate: string, destination: string): boolean {
  try {
    const expected = new URL(`http://${candidate}`);
    const actual = new URL(`http://${destination}`);
    const portMatches = expected.port.length === 0 || expected.port === actual.port;
    return expected.hostname === actual.hostname && portMatches;
  } catch {
    return false;
  }
}

function hasRecentHeartbeat(value: string | null): boolean {
  if (!value) return false;
  const timestamp = Date.parse(value);
  const age = Date.now() - timestamp;
  return Number.isFinite(timestamp) && age >= 0 && age <= 90_000;
}
