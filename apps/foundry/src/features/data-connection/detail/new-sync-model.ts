import type {
  ConnectorResource,
  SourceConnection,
  SourceManagedSyncCreateRequest,
} from "@foundry-lite/sdk";

import {
  isValidDatasetRef,
  isValidIdentifier,
  sanitizeIdentifier,
} from "../source-model";

export type ScheduleMode = "manual" | "interval" | "cron";
export type KafkaPartitionMode = "all" | "single";
export type KafkaUpstreamMode = "external" | "kraken";

export interface NewSyncDraft {
  displayName: string;
  datasetRef: string;
  isDatasetRefTouched: boolean;
  mode: string;
  scheduleMode: ScheduleMode;
  everySecondsText: string;
  cronText: string;
  tableName: string;
  secretRef: string;
  connectorName: string;
  resourceName: string;
  isIncremental: boolean;
  incrementalColumn: string;
  kafkaTopic: string;
  kafkaConsumerGroup: string;
  kafkaStreamName: string;
  kafkaPartitionText: string;
  kafkaPartitionMode: KafkaPartitionMode;
  kafkaBatchLimitText: string;
  checkpointLivenessText: string;
  maxCheckpointDurationText: string;
  maxBrokerLagText: string;
  kafkaUpstreamMode: KafkaUpstreamMode;
  krakenSymbol: string;
}

export type NewSyncDraftUpdater = <Key extends keyof NewSyncDraft>(
  field: Key,
  value: NewSyncDraft[Key],
) => void;

export interface NewSyncSourceKinds {
  isKafka: boolean;
  isPostgres: boolean;
  isRest: boolean;
  isRunnable: boolean;
}

export interface NewSyncValidation {
  isNameValid: boolean;
  isDatasetValid: boolean;
  isScheduleValid: boolean;
  isSourceConfigValid: boolean;
  canCreate: boolean;
}

export interface NewSyncValidationContext {
  hasRestResourceMismatch: boolean;
  hasRestConnectorError: boolean;
}

const RUNNABLE_SYNC_TYPES = new Set([
  "postgres_jdbc",
  "rest",
  "rest_api",
  "sap_odata",
  "kafka",
]);

export function readSourceConfigText(
  source: SourceConnection,
  key: string,
): string {
  const value = source.configSummary?.[key];
  return typeof value === "string" ? value : "";
}

function readSourceConfigNumber(
  source: SourceConnection,
  key: string,
  fallback: number,
): number {
  const value = source.configSummary?.[key];
  return typeof value === "number" ? value : fallback;
}

function readInitialRestResourceName(source: SourceConnection): string {
  const direct = readSourceConfigText(source, "resourceName");
  if (direct) return direct;
  const resources = source.configSummary?.resources;
  if (!Array.isArray(resources)) return "";
  const first = resources[0];
  if (!first || typeof first !== "object") return "";
  const resourceName = (first as Record<string, unknown>)["resourceName"];
  return typeof resourceName === "string" ? resourceName : "";
}

export function sourceKinds(source: SourceConnection): NewSyncSourceKinds {
  return {
    isKafka: source.kind === "kafka",
    isPostgres: source.kind === "postgres_jdbc",
    isRest:
      source.kind === "rest_api" ||
      source.kind === "rest" ||
      source.kind === "sap_odata",
    isRunnable: RUNNABLE_SYNC_TYPES.has(source.kind),
  };
}

export function createInitialNewSyncDraft(
  source: SourceConnection,
  initialTableName?: string,
  initialResourceName?: string,
): NewSyncDraft {
  const { isKafka } = sourceKinds(source);
  const kafkaTopic =
    initialResourceName ?? readSourceConfigText(source, "topic");
  const displayName =
    initialTableName ?? initialResourceName ?? (isKafka ? kafkaTopic : "");
  return {
    displayName,
    datasetRef: initialTableName
      ? `sync.${sanitizeIdentifier(initialTableName)}`
      : (source.targetDatasetRef ??
        (isKafka
          ? `live.${sanitizeIdentifier(source.sourceName)}_events`
          : "")),
    isDatasetRefTouched: false,
    mode: isKafka ? "APPEND" : "SNAPSHOT",
    scheduleMode: "manual",
    everySecondsText: "3600",
    cronText: "0 * * * *",
    tableName: initialTableName ?? "",
    secretRef: readSourceConfigText(source, "databaseUrlSecretRef"),
    connectorName:
      readSourceConfigText(source, "connectorName") || source.sourceName,
    resourceName:
      initialResourceName ?? readInitialRestResourceName(source),
    isIncremental: false,
    incrementalColumn: "",
    kafkaTopic,
    kafkaConsumerGroup:
      readSourceConfigText(source, "consumerGroup") ||
      "foundry-lite-archive",
    kafkaStreamName:
      readSourceConfigText(source, "streamName") ||
      kafkaTopic.replace(/[^a-zA-Z0-9_-]/g, "-"),
    kafkaPartitionText: String(source.configSummary?.partition ?? 0),
    kafkaPartitionMode:
      readSourceConfigText(source, "partitionMode") === "single" ||
      typeof source.configSummary?.partition === "number"
        ? "single"
        : "all",
    kafkaBatchLimitText: String(source.configSummary?.batchLimit ?? 100),
    checkpointLivenessText: String(
      readSourceConfigNumber(source, "checkpointLivenessSeconds", 60),
    ),
    maxCheckpointDurationText: String(
      readSourceConfigNumber(source, "maxCheckpointDurationMs", 30_000),
    ),
    maxBrokerLagText: String(
      readSourceConfigNumber(source, "maxBrokerLag", 10_000),
    ),
    kafkaUpstreamMode:
      readSourceConfigText(source, "upstreamProvider") ===
      "kraken_websocket_v2"
        ? "kraken"
        : "external",
    krakenSymbol:
      readSourceConfigText(source, "upstreamSymbol") || "BTC/USD",
  };
}

export function findRestResource(
  resources: readonly ConnectorResource[],
  resourceName: string,
): ConnectorResource | null {
  const trimmed = resourceName.trim();
  return (
    resources.find((resource) => resource.resourceName === trimmed) ?? null
  );
}

function buildSchedule(draft: NewSyncDraft): Record<string, unknown> {
  if (draft.scheduleMode === "interval") {
    return {
      mode: "interval",
      everySeconds: Number.parseInt(draft.everySecondsText, 10),
    };
  }
  if (draft.scheduleMode === "cron") {
    return { mode: "cron", cron: draft.cronText.trim() };
  }
  return { mode: "manual" };
}

function buildKafkaConfig(
  source: SourceConnection,
  draft: NewSyncDraft,
): Record<string, unknown> {
  const config: Record<string, unknown> = {
    bootstrapServers: readSourceConfigText(source, "bootstrapServers"),
    connectionMode:
      readSourceConfigText(source, "connectionMode") || "direct",
    topic: draft.kafkaTopic.trim(),
    consumerGroup: draft.kafkaConsumerGroup.trim(),
    streamName: draft.kafkaStreamName.trim(),
    partitionMode: draft.kafkaPartitionMode,
    deliveryGuarantee: "AT_LEAST_ONCE",
    batchLimit: Number.parseInt(draft.kafkaBatchLimitText, 10),
    monitoring: {
      checkpointLivenessSeconds: Number.parseInt(
        draft.checkpointLivenessText,
        10,
      ),
      maxCheckpointDurationMs: Number.parseInt(
        draft.maxCheckpointDurationText,
        10,
      ),
      maxBrokerLag: Number.parseInt(draft.maxBrokerLagText, 10),
    },
  };
  if (draft.kafkaPartitionMode === "single") {
    config.partition = Number.parseInt(draft.kafkaPartitionText, 10);
  }
  if (draft.kafkaUpstreamMode === "kraken") {
    config.upstreamProvider = "kraken_websocket_v2";
    config.upstreamWebsocketUrl = "wss://ws.kraken.com/v2";
    config.upstreamSymbol = draft.krakenSymbol.trim();
  }
  return config;
}

function buildConfigSummary(
  source: SourceConnection,
  draft: NewSyncDraft,
  kinds: NewSyncSourceKinds,
): Record<string, unknown> {
  if (kinds.isPostgres) {
    return {
      tableName: draft.tableName.trim(),
      databaseUrlSecretRef: draft.secretRef.trim(),
      ...(draft.isIncremental && draft.incrementalColumn.trim()
        ? { checkpointColumn: draft.incrementalColumn.trim() }
        : {}),
    };
  }
  if (kinds.isRest) {
    return {
      connectorName: draft.connectorName.trim(),
      resourceName: draft.resourceName.trim(),
    };
  }
  return kinds.isKafka ? buildKafkaConfig(source, draft) : {};
}

export function buildManagedSyncCreateRequest(
  source: SourceConnection,
  draft: NewSyncDraft,
  syncName: string,
): SourceManagedSyncCreateRequest {
  const kinds = sourceKinds(source);
  return {
    syncName,
    sourceName: source.sourceName,
    displayName: draft.displayName.trim(),
    sourceType: source.kind === "rest" ? "rest_api" : source.kind,
    capability: kinds.isKafka ? "streaming" : "batch",
    targetDatasetRef: draft.datasetRef.trim(),
    mode: draft.mode,
    schedule: buildSchedule(draft),
    configSummary: buildConfigSummary(source, draft, kinds),
  };
}

export function evaluateNewSyncDraft(
  draft: NewSyncDraft,
  kinds: NewSyncSourceKinds,
  syncName: string,
  context: NewSyncValidationContext,
): NewSyncValidation {
  const isNameValid =
    draft.displayName.trim().length > 0 && isValidIdentifier(syncName);
  const isDatasetValid = isValidDatasetRef(draft.datasetRef.trim());
  const isScheduleValid =
    draft.scheduleMode === "manual" ||
    (draft.scheduleMode === "interval" &&
      Number.parseInt(draft.everySecondsText, 10) > 0) ||
    (draft.scheduleMode === "cron" && draft.cronText.trim().length > 0);
  const isSourceConfigValid = evaluateSourceConfiguration(
    draft,
    kinds,
    context,
  );
  return {
    isNameValid,
    isDatasetValid,
    isScheduleValid,
    isSourceConfigValid,
    canCreate:
      isNameValid &&
      isDatasetValid &&
      isScheduleValid &&
      isSourceConfigValid,
  };
}

function evaluateSourceConfiguration(
  draft: NewSyncDraft,
  kinds: NewSyncSourceKinds,
  context: NewSyncValidationContext,
): boolean {
  if (kinds.isPostgres) {
    return (
      draft.tableName.trim().length > 0 && draft.secretRef.trim().length > 0
    );
  }
  if (kinds.isRest) {
    return (
      draft.connectorName.trim().length > 0 &&
      draft.resourceName.trim().length > 0 &&
      !context.hasRestResourceMismatch &&
      !context.hasRestConnectorError
    );
  }
  return kinds.isKafka ? isKafkaConfigurationValid(draft) : false;
}

function isKafkaConfigurationValid(draft: NewSyncDraft): boolean {
  return (
    draft.kafkaTopic.trim().length > 0 &&
    draft.kafkaConsumerGroup.trim().length > 0 &&
    draft.kafkaStreamName.trim().length > 0 &&
    (draft.kafkaPartitionMode === "all" ||
      Number.parseInt(draft.kafkaPartitionText, 10) >= 0) &&
    Number.parseInt(draft.kafkaBatchLimitText, 10) > 0 &&
    Number.parseInt(draft.checkpointLivenessText, 10) > 0 &&
    Number.parseInt(draft.maxCheckpointDurationText, 10) > 0 &&
    Number.parseInt(draft.maxBrokerLagText, 10) >= 0 &&
    (draft.kafkaUpstreamMode !== "kraken" ||
      draft.krakenSymbol.trim().length > 0)
  );
}
