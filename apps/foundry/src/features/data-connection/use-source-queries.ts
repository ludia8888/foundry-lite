import type {
  SourceConnection,
  SourceDebeziumOperationPlan,
  SourceManagedSync,
  SourceManagedStreamingSyncStatus,
  SourceSchedulerDecision,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { useCallback, useEffect } from "react";

/**
 * managed sync만 있고 소스 행이 없는 postgres/rest 소스를 가상 소스로 만든다.
 * (백엔드는 업로드/웹훅/CDC 계열만 소스 행을 생성하고, 관리형 소스는
 * credential + managed sync 조합으로 존재한다.)
 */
function configString(
  config: Record<string, unknown>,
  key: string,
): string | null {
  const value = config[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function toVirtualSource(
  sync: SourceManagedSync,
  registeredSource: SourceConnection | null,
  connectorConnection: SourceConnection | null,
): SourceConnection {
  if (registeredSource) {
    return {
      ...registeredSource,
      targetDatasetRef:
        registeredSource.targetDatasetRef ?? sync.targetDatasetRef,
      targetMediaSetId:
        registeredSource.targetMediaSetId ?? sync.targetMediaSetId,
      configSummary: {
        ...connectorConnection?.configSummary,
        ...sync.configSummary,
        ...registeredSource.configSummary,
        syncName: sync.syncName,
        connectionConfigFingerprint:
          connectorConnection?.configFingerprint ??
          registeredSource.configFingerprint,
      },
      lastRunId: registeredSource.lastRunId ?? sync.lastRunId,
      lastWorkflowRunId:
        registeredSource.lastWorkflowRunId ?? sync.lastWorkflowRunId,
      updatedAt:
        registeredSource.updatedAt > sync.updatedAt
          ? registeredSource.updatedAt
          : sync.updatedAt,
    };
  }
  return {
    sourceName: sync.sourceName,
    displayName: sync.displayName,
    kind: sync.sourceType,
    targetDatasetRef: sync.targetDatasetRef,
    targetMediaSetId: sync.targetMediaSetId,
    status: sync.status,
    configSummary: {
      ...sync.configSummary,
      ...connectorConnection?.configSummary,
      syncName: sync.syncName,
      connectionConfigFingerprint:
        connectorConnection?.configFingerprint ?? null,
    },
    configFingerprint: sync.configFingerprint,
    lastRunId: sync.lastRunId,
    lastWorkflowRunId: sync.lastWorkflowRunId,
    lastCommitRef: {},
    operationsPath: null,
    createdAt: sync.createdAt,
    updatedAt: sync.updatedAt,
  };
}

/**
 * 백엔드 실행용 connector connection과 사용자용 managed source를 연결 키로 합친다.
 * 내부 connector row를 별도 Source처럼 노출하지 않고, 연결 상세는 Source에 보존한다.
 */
export function mergeSourceConnections(
  sources: readonly SourceConnection[],
  syncs: readonly SourceManagedSync[],
): SourceConnection[] {
  const latestSyncBySourceName = new Map<string, SourceManagedSync>();
  for (const sync of syncs) {
    const existing = latestSyncBySourceName.get(sync.sourceName);
    if (!existing || sync.updatedAt > existing.updatedAt) {
      latestSyncBySourceName.set(sync.sourceName, sync);
    }
  }

  const sourceByName = new Map(
    sources.map((source) => [source.sourceName, source] as const),
  );
  const linkedConnectorNames = new Set<string>();
  const managedSources: SourceConnection[] = [];
  for (const sync of latestSyncBySourceName.values()) {
    const connectorName = configString(sync.configSummary, "connectorName");
    if (connectorName) linkedConnectorNames.add(connectorName);
    const registeredSource = sourceByName.get(sync.sourceName) ?? null;
    const connectorConnection = connectorName
      ? sourceByName.get(connectorName) ?? null
      : null;
    managedSources.push(
      toVirtualSource(sync, registeredSource, connectorConnection),
    );
  }

  const standaloneSources = sources.filter(
    (source) =>
      !latestSyncBySourceName.has(source.sourceName) &&
      !linkedConnectorNames.has(source.sourceName),
  );
  return [...standaloneSources, ...managedSources];
}

/** sources.list + managed sync의 product-level Source 투영 목록. */
export function useSourceConnections() {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    const [sources, syncs] = await Promise.all([
      client.sources.list(),
      client.sources.managedSyncs.list(),
    ]);
    return mergeSourceConnections(sources, syncs);
  }, [client]);
  return useFoundryLiteQuery(["data-connection", "sources"], load);
}

/** sources.cdc.debezium.operationPlan — CDC 운영 준비 상태와 worker 명령. */
export function useDebeziumOperationPlan(
  sourceName: string | null,
  objectTypeApiName = "Order",
) {
  const client = useFoundryLiteClient();
  const load = useCallback(
    (): Promise<SourceDebeziumOperationPlan | null> =>
      sourceName
        ? client.sources.cdc.debezium.operationPlan(sourceName, {
            objectTypeApiName,
          })
        : Promise.resolve(null),
    [client, objectTypeApiName, sourceName],
  );
  return useFoundryLiteQuery(
    ["data-connection", "cdc-operation-plan", sourceName, objectTypeApiName],
    load,
    { enabled: sourceName !== null },
  );
}

/** sources.templates.list — 새 소스 위저드의 템플릿 카탈로그. */
export function useSourceTemplates() {
  const client = useFoundryLiteClient();
  const load = useCallback(() => client.sources.templates.list(), [client]);
  return useFoundryLiteQuery(["data-connection", "templates"], load);
}

/** sources.managedSyncs.list — 관리형 동기화 전체 목록. */
export function useManagedSyncs() {
  const client = useFoundryLiteClient();
  const load = useCallback(() => client.sources.managedSyncs.list(), [client]);
  return useFoundryLiteQuery(["data-connection", "managed-syncs"], load);
}

/** sources.managedSyncs.listRuns — 선택된 sync의 run 증거 목록. */
export function useSyncRuns(syncName: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(
    () =>
      syncName
        ? client.sources.managedSyncs.listRuns(syncName)
        : Promise.resolve([]),
    [client, syncName],
  );
  return useFoundryLiteQuery(["data-connection", "sync-runs", syncName], load, {
    enabled: syncName !== null,
  });
}

/** Durable streaming lifecycle + heartbeat/checkpoint telemetry, polled while the detail is open. */
export function useStreamingSyncStatus(
  syncName: string | null,
  pollIntervalMs = 2_000,
) {
  const client = useFoundryLiteClient();
  const load = useCallback(
    (): Promise<SourceManagedStreamingSyncStatus | null> =>
      syncName
        ? client.sources.managedSyncs.streamStatus(syncName)
        : Promise.resolve(null),
    [client, syncName],
  );
  const query = useFoundryLiteQuery(
    ["data-connection", "streaming-sync-status", syncName],
    load,
    { enabled: syncName !== null },
  );
  const reload = query.reload;

  useEffect(() => {
    if (syncName === null || pollIntervalMs <= 0) return undefined;
    const timer = window.setInterval(() => void reload(), pollIntervalMs);
    return () => window.clearInterval(timer);
  }, [pollIntervalMs, reload, syncName]);

  return query;
}

/** scheduler.previewDue — 실행하지 않고 한 sync의 현재 스케줄 판단을 읽는다. */
export function useSourceSchedulerStatus(syncName: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(async (): Promise<SourceSchedulerDecision | null> => {
    if (!syncName) return null;
    const result = await client.sources.scheduler.previewDue({ maxRuns: 500 });
    return result.decisions.find((decision) => decision.syncName === syncName) ?? null;
  }, [client, syncName]);
  return useFoundryLiteQuery(["data-connection", "sync-schedule", syncName], load, {
    enabled: syncName !== null,
  });
}

/** sources.agents.list — 에이전트 상태 패널. */
export function useSourceAgents() {
  const client = useFoundryLiteClient();
  const load = useCallback(() => client.sources.agents.list(), [client]);
  return useFoundryLiteQuery(["data-connection", "agents"], load);
}

/** sources.credentials.list — Source 설정의 인증정보 선택 목록. */
export function useSourceCredentials() {
  const client = useFoundryLiteClient();
  const load = useCallback(() => client.sources.credentials.list(), [client]);
  return useFoundryLiteQuery(["data-connection", "credentials"], load);
}

/** sources.networkPolicies.list — 네트워크 정책 목록. */
export function useNetworkPolicies() {
  const client = useFoundryLiteClient();
  const load = useCallback(
    () => client.sources.networkPolicies.list(),
    [client],
  );
  return useFoundryLiteQuery(["data-connection", "network-policies"], load);
}

/** Source-level live connection diagnostics — persisted test history. */
export function useSourceConnectionTests(sourceName: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(
    () =>
      sourceName
        ? client.sources.listConnectionTests(sourceName, { limit: 20 })
        : Promise.resolve([]),
    [client, sourceName],
  );
  return useFoundryLiteQuery(
    ["data-connection", "source-connection-tests", sourceName],
    load,
    { enabled: sourceName !== null },
  );
}

/** Source connection tests에서 파생된 durable egress attempt log. */
export function useSourceEgressAttempts(sourceName: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(
    () =>
      sourceName
        ? client.sources.listEgressAttempts(sourceName, { limit: 20 })
        : Promise.resolve([]),
    [client, sourceName],
  );
  return useFoundryLiteQuery(
    ["data-connection", "source-egress-attempts", sourceName],
    load,
    { enabled: sourceName !== null },
  );
}
