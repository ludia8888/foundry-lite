import type {
  SourceConnection,
  SourceConnectionTestResult,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  KeyRound,
  Network,
  Play,
  Server,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useCallback, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { readTextField } from "../source-model";
import {
  useNetworkPolicies,
  useSourceAgents,
  useSourceConnectionTests,
  useSourceEgressAttempts,
} from "../use-source-queries";
import { SourceConnectionTestHistory } from "./SourceConnectionTestHistory";
import { SourceEgressAttemptHistory } from "./SourceEgressAttemptHistory";
import { SettingsCard, SettingsRow } from "./SourceSettingsUi";
import {
  type ReadinessState,
  agentDetail,
  agentReadiness,
  diagnosticDetail,
  diagnosticState,
  networkPolicyState,
  policyDetail,
  policyHostSummary,
  previewDetail,
  resolveAgent,
  resolvePolicy,
  sourceDestination,
  sourceEndpoint,
} from "./source-network-readiness-model";

interface SourceTestPayload {
  sourceName: string;
  configFingerprint: string;
}

interface SourceNetworkReadinessProps {
  source: SourceConnection;
  baseUrl: string | null;
  isPrivateNetworkAllowed: boolean;
}

/** Source → policy → agent → preview 순서로 실제 연결 준비 상태를 설명한다. */
export function SourceNetworkReadiness({
  source,
  baseUrl,
  isPrivateNetworkAllowed,
}: SourceNetworkReadinessProps) {
  const client = useFoundryLiteClient();
  const policiesQuery = useNetworkPolicies();
  const agentsQuery = useSourceAgents();
  const historyQuery = useSourceConnectionTests(source.sourceName);
  const egressQuery = useSourceEgressAttempts(source.sourceName);
  const [testResult, setTestResult] =
    useState<SourceConnectionTestResult | null>(null);
  const activeTest = testResult ?? historyQuery.data?.[0] ?? null;
  const config = source.configSummary;
  const sourceAddress =
    baseUrl ?? readTextField(config, "bootstrapServers");
  const host = sourceDestination(sourceAddress);
  const endpoint = sourceEndpoint(source, baseUrl);
  const explicitMode = readTextField(config, "connectionMode");
  const fallbackMode = isPrivateNetworkAllowed ? "agent_proxy" : "direct";
  const routeMode = explicitMode ?? fallbackMode;
  const policy = resolvePolicy(
    policiesQuery.data ?? [],
    readTextField(config, "networkPolicyName"),
    routeMode,
    host,
  );
  const agent = resolveAgent(
    agentsQuery.data ?? [],
    readTextField(config, "agentId") ?? policy?.agentId ?? null,
  );
  const policyState = networkPolicyState(policy, host, routeMode);
  const agentState = agentReadiness(routeMode, agent);
  const hasConfigurationConcern =
    !endpoint ||
    agentState !== "ready" ||
    (routeMode === "agent_proxy"
      ? policyState !== "ready"
      : policyState === "blocked");
  const isSupported = [
    "rest",
    "rest_api",
    "postgres_jdbc",
    "sap_odata",
    "kafka",
  ].includes(source.kind);

  const testConnection = useFoundryLiteMutation(
    useCallback(
      (payload: SourceTestPayload) =>
        client.sources.testConnection(
          payload.sourceName,
          { expectedConfigFingerprint: payload.configFingerprint },
          {
            idempotencyKey: idempotencyKey(
              "source-connection-test",
              crypto.randomUUID(),
            ),
          },
        ),
      [client],
    ),
    {
      lockKey: (payload) =>
        `sources:connection-test:${payload.sourceName}`,
    },
  );

  const handleTest = async () => {
    if (!isSupported) return;
    const result = await testConnection.execute({
      sourceName: source.sourceName,
      configFingerprint: source.configFingerprint,
    });
    if (!result) return;
    setTestResult(result);
    await Promise.all([historyQuery.reload(), egressQuery.reload()]);
  };

  if (
    (policiesQuery.isLoading && !policiesQuery.data) ||
    (agentsQuery.isLoading && !agentsQuery.data) ||
    (historyQuery.isLoading && !historyQuery.data) ||
    (egressQuery.isLoading && !egressQuery.data)
  ) {
    return <LoadingState rowCount={5} />;
  }

  const testState: ReadinessState = activeTest
    ? activeTest.status.toLowerCase() === "succeeded"
      ? "ready"
      : "blocked"
    : "pending";
  const overallIntent =
    testState === "ready" && !hasConfigurationConcern
      ? "success"
      : testState === "blocked"
        ? "danger"
        : "warning";
  const overallLabel =
    testState === "ready" && !hasConfigurationConcern
      ? "연결 준비됨"
      : testState === "blocked"
        ? "연결 실패"
        : "확인 필요";

  return (
    <div className="space-y-4">
      <SettingsCard
        title="네트워크 egress"
        description="Source 요청이 외부 endpoint까지 도달하는 경로를 순서대로 확인합니다."
        actions={
          <span aria-live="polite">
            <StatusPill intent={overallIntent}>{overallLabel}</StatusPill>
          </span>
        }
      >
        <div className="p-4">
          <div className="relative space-y-0">
            <div className="absolute top-5 bottom-5 left-[15px] w-px bg-border" />
            <ReadinessStep
              icon={Network}
              state={diagnosticState(activeTest, "source_config", endpoint ? "ready" : "blocked")}
              title="Source endpoint"
              detail={diagnosticDetail(activeTest, "source_config", endpoint ?? "endpoint 구성이 없습니다.")}
            />
            <ReadinessStep
              icon={ShieldCheck}
              state={diagnosticState(activeTest, "network_route", policyState)}
              title="Egress policy"
              detail={diagnosticDetail(
                activeTest,
                "network_route",
                policyDetail(policy, host, routeMode),
              )}
            />
            <ReadinessStep
              icon={Server}
              state={diagnosticState(activeTest, "worker_runtime", agentState)}
              title="Foundry worker"
              detail={diagnosticDetail(activeTest, "worker_runtime", agentDetail(routeMode, agent))}
            />
            <ReadinessStep
              icon={KeyRound}
              state={diagnosticState(activeTest, "credential", "pending")}
              title="Credential"
              detail={diagnosticDetail(activeTest, "credential", "실제 요청 전에는 인증 결과를 확정하지 않습니다.")}
            />
            <ReadinessStep
              icon={Play}
              state={testState}
              title="실제 Source preview"
              detail={previewDetail(activeTest)}
              isLast
            />
          </div>
        </div>
      </SettingsCard>

      <SettingsCard
        title="경로 구성"
        description="현재 Source가 참조하는 네트워크 객체입니다."
        actions={
          <Button
            type="button"
            size="sm"
            disabled={
              !isSupported || testConnection.isRunning
            }
            title={
              isSupported
                ? undefined
                : "현재 live 진단은 REST, database, Kafka Source를 지원합니다."
            }
            onClick={() => void handleTest()}
          >
            <Play className="size-3.5" />
            {testConnection.isRunning ? "진단 중..." : "연결 진단 실행"}
          </Button>
        }
      >
        <dl className="divide-y divide-border/60">
          <SettingsRow
            label="실행 경로"
            value={
              routeMode === "agent_proxy"
                ? "Agent proxy 경유"
                : "Foundry worker 직접 연결"
            }
          />
          <SettingsRow
            label="네트워크 정책"
            value={policy?.displayName ?? "연결된 정책 없음"}
            isCode={!policy}
          />
          <SettingsRow
            label="허용 호스트"
            value={host ?? policyHostSummary(policy) ?? "확인할 수 없음"}
            isCode
          />
          <SettingsRow
            label="Agent"
            value={
              routeMode === "agent_proxy"
                ? agent?.displayName ?? "연결된 Agent 없음"
                : "필요하지 않음"
            }
          />
          <SettingsRow
            label="Agent proxy URL"
            value={
              routeMode === "agent_proxy"
                ? readTextField(agent?.networkSummary, "proxyUrl") ??
                  "등록된 proxy URL 없음"
                : "필요하지 않음"
            }
            isCode={routeMode === "agent_proxy"}
          />
          <SettingsRow
            label="사설 주소"
            value={isPrivateNetworkAllowed ? "허용" : "차단"}
          />
        </dl>
      </SettingsCard>

      <SourceConnectionTestHistory
        tests={historyQuery.data ?? []}
        currentConfigFingerprint={source.configFingerprint}
        selectedTestId={activeTest?.connectionTestId ?? null}
        onSelect={setTestResult}
      />

      <SourceEgressAttemptHistory attempts={egressQuery.data ?? []} />

      {policiesQuery.error ? (
        <ErrorState
          error={policiesQuery.error}
          onRetry={() => void policiesQuery.reload()}
        />
      ) : null}
      {agentsQuery.error ? (
        <ErrorState
          error={agentsQuery.error}
          onRetry={() => void agentsQuery.reload()}
        />
      ) : null}
      {historyQuery.error ? (
        <ErrorState
          error={historyQuery.error}
          onRetry={() => void historyQuery.reload()}
        />
      ) : null}
      {egressQuery.error ? (
        <ErrorState
          error={egressQuery.error}
          onRetry={() => void egressQuery.reload()}
        />
      ) : null}
      {testConnection.error ? (
        <ErrorState
          error={testConnection.error}
          onRetry={() => void handleTest()}
        />
      ) : null}
    </div>
  );
}

function ReadinessStep({
  icon: Icon,
  state,
  title,
  detail,
  isLast = false,
}: {
  icon: typeof Network;
  state: ReadinessState;
  title: string;
  detail: string;
  isLast?: boolean;
}) {
  const StateIcon = readinessIcon(state);
  return (
    <div className={cn("relative flex gap-3", !isLast && "pb-5")}>
      <span
        className={cn(
          "relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full border bg-card",
          state === "ready" && "border-success/40 text-success",
          state === "warning" && "border-warning/40 text-warning",
          state === "blocked" && "border-destructive/40 text-destructive",
          state === "pending" && "text-muted-foreground",
        )}
      >
        <Icon aria-hidden className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="flex items-center gap-1.5 text-xs font-semibold">
          {title}
          <StateIcon aria-hidden className="size-3.5" />
        </div>
        <p className="mt-0.5 break-words font-mono text-[10px] text-muted-foreground">
          {detail}
        </p>
      </div>
    </div>
  );
}

function readinessIcon(state: ReadinessState) {
  if (state === "ready") return CheckCircle2;
  if (state === "warning") return AlertTriangle;
  if (state === "blocked") return XCircle;
  return CircleDashed;
}
