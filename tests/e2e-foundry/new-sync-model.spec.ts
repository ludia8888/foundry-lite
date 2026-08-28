import { expect, test } from "@playwright/test";
import type { SourceConnection } from "@foundry-lite/sdk";

import {
  buildManagedSyncCreateRequest,
  createInitialNewSyncDraft,
  evaluateNewSyncDraft,
  sourceKinds,
} from "../../apps/foundry/src/features/data-connection/detail/new-sync-model";

function sourceConnection(
  overrides: Partial<SourceConnection>,
): SourceConnection {
  return {
    sourceName: "orders_source",
    displayName: "Orders source",
    kind: "postgres_jdbc",
    targetDatasetRef: null,
    targetMediaSetId: null,
    status: "active",
    configSummary: {},
    configFingerprint: "fingerprint-1",
    lastRunId: null,
    lastWorkflowRunId: null,
    lastCommitRef: {},
    operationsPath: null,
    createdAt: "2026-08-28T00:00:00Z",
    updatedAt: "2026-08-28T00:00:00Z",
    ...overrides,
  };
}

test("Postgres sync draft produces the batch request and incremental checkpoint", () => {
  const source = sourceConnection({
    configSummary: { databaseUrlSecretRef: "vault/postgres/orders" },
  });
  const initial = createInitialNewSyncDraft(source, "public_orders");
  const draft = {
    ...initial,
    displayName: "Orders hourly",
    isIncremental: true,
    incrementalColumn: "updated_at",
    scheduleMode: "interval" as const,
    everySecondsText: "900",
  };
  const syncName = "orders_hourly";

  expect(initial.datasetRef).toBe("sync.public_orders");
  expect(initial.secretRef).toBe("vault/postgres/orders");
  expect(
    evaluateNewSyncDraft(draft, sourceKinds(source), syncName, {
      hasRestResourceMismatch: false,
      hasRestConnectorError: false,
    }),
  ).toEqual({
    isNameValid: true,
    isDatasetValid: true,
    isScheduleValid: true,
    isSourceConfigValid: true,
    canCreate: true,
  });
  expect(buildManagedSyncCreateRequest(source, draft, syncName)).toEqual({
    syncName,
    sourceName: "orders_source",
    displayName: "Orders hourly",
    sourceType: "postgres_jdbc",
    capability: "batch",
    targetDatasetRef: "sync.public_orders",
    mode: "SNAPSHOT",
    schedule: { mode: "interval", everySeconds: 900 },
    configSummary: {
      tableName: "public_orders",
      databaseUrlSecretRef: "vault/postgres/orders",
      checkpointColumn: "updated_at",
    },
  });
});

test("REST sync rejects a resource mismatch and normalizes rest to rest_api", () => {
  const source = sourceConnection({
    kind: "rest",
    sourceName: "erp_rest",
    targetDatasetRef: "raw.orders",
    configSummary: {
      connectorName: "erp_connector",
      resources: [{ resourceName: "orders" }],
    },
  });
  const draft = {
    ...createInitialNewSyncDraft(source),
    displayName: "ERP orders",
  };
  const kinds = sourceKinds(source);

  expect(draft.connectorName).toBe("erp_connector");
  expect(draft.resourceName).toBe("orders");
  expect(
    evaluateNewSyncDraft(draft, kinds, "erp_orders", {
      hasRestResourceMismatch: true,
      hasRestConnectorError: false,
    }).canCreate,
  ).toBe(false);
  expect(
    evaluateNewSyncDraft(draft, kinds, "erp_orders", {
      hasRestResourceMismatch: false,
      hasRestConnectorError: false,
    }).canCreate,
  ).toBe(true);
  expect(buildManagedSyncCreateRequest(source, draft, "erp_orders")).toEqual({
    syncName: "erp_orders",
    sourceName: "erp_rest",
    displayName: "ERP orders",
    sourceType: "rest_api",
    capability: "batch",
    targetDatasetRef: "raw.orders",
    mode: "SNAPSHOT",
    schedule: { mode: "manual" },
    configSummary: {
      connectorName: "erp_connector",
      resourceName: "orders",
    },
  });
});

test("Kafka sync preserves partition, monitoring, and managed upstream settings", () => {
  const source = sourceConnection({
    kind: "kafka",
    sourceName: "market_events",
    configSummary: {
      bootstrapServers: "broker.internal:9092",
      connectionMode: "agent_proxy",
      topic: "trades",
      consumerGroup: "archive-v2",
      streamName: "trades-stream",
      batchLimit: 250,
    },
  });
  const initial = createInitialNewSyncDraft(source);
  const draft = {
    ...initial,
    displayName: "Managed BTC trades",
    kafkaPartitionMode: "single" as const,
    kafkaPartitionText: "2",
    kafkaUpstreamMode: "kraken" as const,
    krakenSymbol: "BTC/USD",
  };

  expect(initial.datasetRef).toBe("live.market_events_events");
  expect(initial.mode).toBe("APPEND");
  expect(
    evaluateNewSyncDraft(draft, sourceKinds(source), "managed_btc_trades", {
      hasRestResourceMismatch: false,
      hasRestConnectorError: false,
    }).canCreate,
  ).toBe(true);
  expect(
    buildManagedSyncCreateRequest(source, draft, "managed_btc_trades"),
  ).toEqual({
    syncName: "managed_btc_trades",
    sourceName: "market_events",
    displayName: "Managed BTC trades",
    sourceType: "kafka",
    capability: "streaming",
    targetDatasetRef: "live.market_events_events",
    mode: "APPEND",
    schedule: { mode: "manual" },
    configSummary: {
      bootstrapServers: "broker.internal:9092",
      connectionMode: "agent_proxy",
      topic: "trades",
      consumerGroup: "archive-v2",
      streamName: "trades-stream",
      partitionMode: "single",
      partition: 2,
      deliveryGuarantee: "AT_LEAST_ONCE",
      batchLimit: 250,
      monitoring: {
        checkpointLivenessSeconds: 60,
        maxCheckpointDurationMs: 30_000,
        maxBrokerLag: 10_000,
      },
      upstreamProvider: "kraken_websocket_v2",
      upstreamWebsocketUrl: "wss://ws.kraken.com/v2",
      upstreamSymbol: "BTC/USD",
    },
  });
});
