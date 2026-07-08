import type {
  SourceConnection,
  SourceDebeziumOperationPlan,
  SourceManagedSync,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { useCallback } from "react";

/**
 * managed sync만 있고 소스 행이 없는 postgres/rest 소스를 가상 소스로 만든다.
 * (백엔드는 업로드/웹훅/CDC 계열만 소스 행을 생성하고, 관리형 소스는
 * credential + managed sync 조합으로 존재한다.)
 */
function toVirtualSource(sync: SourceManagedSync): SourceConnection {
  return {
    sourceName: sync.sourceName,
    displayName: sync.sourceName,
    kind: sync.sourceType,
    targetDatasetRef: sync.targetDatasetRef,
    targetMediaSetId: sync.targetMediaSetId,
    status: sync.status,
    configSummary: { ...sync.configSummary },
    configFingerprint: sync.configFingerprint,
    lastRunId: sync.lastRunId,
    lastWorkflowRunId: sync.lastWorkflowRunId,
    lastCommitRef: {},
    operationsPath: null,
    createdAt: sync.createdAt,
    updatedAt: sync.updatedAt,
  };
}

/** sources.list + managed sync 파생 가상 소스 병합 목록. */
export function useSourceConnections() {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    const [sources, syncs] = await Promise.all([
      client.sources.list(),
      client.sources.managedSyncs.list(),
    ]);
    const knownNames = new Set(sources.map((source) => source.sourceName));
    const virtualBySourceName = new Map<string, SourceConnection>();
    for (const sync of syncs) {
      if (knownNames.has(sync.sourceName)) continue;
      const existing = virtualBySourceName.get(sync.sourceName);
      if (!existing || sync.updatedAt > existing.updatedAt) {
        virtualBySourceName.set(sync.sourceName, toVirtualSource(sync));
      }
    }
    return [...sources, ...virtualBySourceName.values()];
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

/** sources.agents.list — 에이전트 상태 패널. */
export function useSourceAgents() {
  const client = useFoundryLiteClient();
  const load = useCallback(() => client.sources.agents.list(), [client]);
  return useFoundryLiteQuery(["data-connection", "agents"], load);
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
