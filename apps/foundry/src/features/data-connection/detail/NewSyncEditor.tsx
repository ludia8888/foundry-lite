import type {
  ConnectorResource,
  SourceConnection,
  SourceManagedSync,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { Activity, CalendarClock, Database, FileSearch, Radio, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

import {
  isValidDatasetRef,
  isValidIdentifier,
  sanitizeIdentifier,
} from "../source-model";
import { TRANSACTION_MODES } from "./sync-config";

type ScheduleMode = "manual" | "interval" | "cron";

/** 백엔드 managed sync 실행 데이터플레인이 있는 소스 타입. */
const RUNNABLE_SYNC_TYPES = new Set([
  "postgres_jdbc",
  "rest",
  "rest_api",
  "sap_odata",
  "kafka",
]);

interface NewSyncEditorProps {
  source: SourceConnection;
  onCreated: (sync: SourceManagedSync) => void;
  onCancel: () => void;
  /** 탐색 탭에서 테이블 선택 후 진입한 경우의 초기값. */
  initialTableName?: string;
  /** REST Source 탐색 탭에서 리소스 선택 후 진입한 경우의 초기값. */
  initialResourceName?: string;
}

function readSourceSecretRef(source: SourceConnection): string {
  const value = source.configSummary?.databaseUrlSecretRef;
  return typeof value === "string" ? value : "";
}

function readSourceConfigText(source: SourceConnection, key: string): string {
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

function findRestResource(
  resources: readonly ConnectorResource[],
  resourceName: string,
): ConnectorResource | null {
  const trimmed = resourceName.trim();
  return resources.find((resource) => resource.resourceName === trimmed) ?? null;
}

/**
 * 새 동기화 편집기 (Palantir 동기화 구성 페이지):
 * 핵심 구성(목적지/트랜잭션 유형/일정) + 소스별 구성(SQL 쿼리/Incremental).
 */
export function NewSyncEditor({
  source,
  onCreated,
  onCancel,
  initialTableName,
  initialResourceName,
}: NewSyncEditorProps) {
  const client = useFoundryLiteClient();
  const isKafka = source.kind === "kafka";
  const initialKafkaTopic =
    initialResourceName ?? readSourceConfigText(source, "topic");
  const initialDisplayName =
    initialTableName ?? initialResourceName ?? (isKafka ? initialKafkaTopic : "");
  const [displayName, setDisplayName] = useState(initialDisplayName);
  const [datasetRef, setDatasetRef] = useState(
    initialTableName
      ? `sync.${sanitizeIdentifier(initialTableName)}`
      : (source.targetDatasetRef ??
        (isKafka ? `live.${sanitizeIdentifier(source.sourceName)}_events` : "")),
  );
  const [isDatasetRefTouched, setIsDatasetRefTouched] = useState(false);
  const [mode, setMode] = useState<string>(isKafka ? "APPEND" : "SNAPSHOT");
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>("manual");
  const [everySecondsText, setEverySecondsText] = useState("3600");
  const [cronText, setCronText] = useState("0 * * * *");
  const [tableName, setTableName] = useState(initialTableName ?? "");
  const [secretRef, setSecretRef] = useState(() => readSourceSecretRef(source));
  const [connectorName, setConnectorName] = useState(
    () => readSourceConfigText(source, "connectorName") || source.sourceName,
  );
  const [resourceName, setResourceName] = useState(
    () => initialResourceName ?? readInitialRestResourceName(source),
  );
  const [isIncremental, setIsIncremental] = useState(false);
  const [incrementalColumn, setIncrementalColumn] = useState("");
  const [kafkaTopic, setKafkaTopic] = useState(initialKafkaTopic);
  const [kafkaConsumerGroup, setKafkaConsumerGroup] = useState(
    () => readSourceConfigText(source, "consumerGroup") || "foundry-lite-archive",
  );
  const [kafkaStreamName, setKafkaStreamName] = useState(
    () => readSourceConfigText(source, "streamName") || initialKafkaTopic.replace(/[^a-zA-Z0-9_-]/g, "-"),
  );
  const [kafkaPartitionText, setKafkaPartitionText] = useState(() =>
    String(source.configSummary?.partition ?? 0),
  );
  const [kafkaPartitionMode, setKafkaPartitionMode] = useState<"all" | "single">(
    () =>
      readSourceConfigText(source, "partitionMode") === "single" ||
      typeof source.configSummary?.partition === "number"
        ? "single"
        : "all",
  );
  const [kafkaBatchLimitText, setKafkaBatchLimitText] = useState(() =>
    String(source.configSummary?.batchLimit ?? 100),
  );
  const [checkpointLivenessText, setCheckpointLivenessText] = useState(() =>
    String(readSourceConfigNumber(source, "checkpointLivenessSeconds", 60)),
  );
  const [maxCheckpointDurationText, setMaxCheckpointDurationText] = useState(() =>
    String(readSourceConfigNumber(source, "maxCheckpointDurationMs", 30_000)),
  );
  const [maxBrokerLagText, setMaxBrokerLagText] = useState(() =>
    String(readSourceConfigNumber(source, "maxBrokerLag", 10_000)),
  );
  const [kafkaUpstreamMode, setKafkaUpstreamMode] = useState<"external" | "kraken">(
    () =>
      readSourceConfigText(source, "upstreamProvider") ===
      "kraken_websocket_v2"
        ? "kraken"
        : "external",
  );
  const [krakenSymbol, setKrakenSymbol] = useState(
    () => readSourceConfigText(source, "upstreamSymbol") || "BTC/USD",
  );
  const kafkaBootstrapServers = readSourceConfigText(
    source,
    "bootstrapServers",
  );
  const kafkaConnectionMode =
    readSourceConfigText(source, "connectionMode") || "direct";

  const isPostgres = source.kind === "postgres_jdbc";
  const isRest =
    source.kind === "rest_api" ||
    source.kind === "rest" ||
    source.kind === "sap_odata";
  const isRunnableType = RUNNABLE_SYNC_TYPES.has(source.kind);
  const sourceType = source.kind === "rest" ? "rest_api" : source.kind;
  const restConnectorQuery = useFoundryLiteQuery(
    ["data-connection", "rest-connector", connectorName.trim()],
    () =>
      isRest && connectorName.trim()
        ? client.connectors.connections.get(connectorName.trim())
        : Promise.resolve(null),
    { enabled: isRest && connectorName.trim().length > 0 },
  );
  const restResources = restConnectorQuery.data?.resources ?? [];
  const selectedRestResource = findRestResource(restResources, resourceName);
  const hasRestResourceMismatch =
    selectedRestResource !== null &&
    datasetRef.trim().length > 0 &&
    datasetRef.trim() !== selectedRestResource.datasetRef;

  useEffect(() => {
    if (!isRest || selectedRestResource === null) return;
    if (!isDatasetRefTouched || hasRestResourceMismatch) {
      setDatasetRef(selectedRestResource.datasetRef);
      setIsDatasetRefTouched(false);
    }
  }, [
    hasRestResourceMismatch,
    isDatasetRefTouched,
    isRest,
    selectedRestResource,
  ]);

  const syncName = useMemo(
    () => sanitizeIdentifier(displayName),
    [displayName],
  );
  const idempotencyRef = useMemo(
    () => idempotencyKey("managed-sync", crypto.randomUUID()),
    [],
  );

  const createSync = useFoundryLiteMutation(
    useCallback(async () => {
      const schedule: Record<string, unknown> =
        scheduleMode === "interval"
          ? {
              mode: "interval",
              everySeconds: Number.parseInt(everySecondsText, 10),
            }
          : scheduleMode === "cron"
            ? { mode: "cron", cron: cronText.trim() }
            : { mode: "manual" };
      // 백엔드 실행 계약: postgres는 tableName+databaseUrlSecretRef(+checkpointColumn),
      // rest는 connectorName+resourceName을 configSummary에서 읽는다.
      const configSummary: Record<string, unknown> = {};
      if (isPostgres) {
        configSummary.tableName = tableName.trim();
        configSummary.databaseUrlSecretRef = secretRef.trim();
        if (isIncremental && incrementalColumn.trim()) {
          configSummary.checkpointColumn = incrementalColumn.trim();
        }
      }
      if (isRest) {
        configSummary.connectorName = connectorName.trim();
        configSummary.resourceName = resourceName.trim();
      }
      if (isKafka) {
        configSummary.bootstrapServers = kafkaBootstrapServers;
        configSummary.connectionMode = kafkaConnectionMode;
        configSummary.topic = kafkaTopic.trim();
        configSummary.consumerGroup = kafkaConsumerGroup.trim();
        configSummary.streamName = kafkaStreamName.trim();
        configSummary.partitionMode = kafkaPartitionMode;
        configSummary.deliveryGuarantee = "AT_LEAST_ONCE";
        if (kafkaPartitionMode === "single") {
          configSummary.partition = Number.parseInt(kafkaPartitionText, 10);
        }
        configSummary.batchLimit = Number.parseInt(kafkaBatchLimitText, 10);
        configSummary.monitoring = {
          checkpointLivenessSeconds: Number.parseInt(checkpointLivenessText, 10),
          maxCheckpointDurationMs: Number.parseInt(maxCheckpointDurationText, 10),
          maxBrokerLag: Number.parseInt(maxBrokerLagText, 10),
        };
        if (kafkaUpstreamMode === "kraken") {
          configSummary.upstreamProvider = "kraken_websocket_v2";
          configSummary.upstreamWebsocketUrl = "wss://ws.kraken.com/v2";
          configSummary.upstreamSymbol = krakenSymbol.trim();
        }
      }
      return client.sources.managedSyncs.create(
        {
          syncName,
          sourceName: source.sourceName,
          displayName: displayName.trim(),
          sourceType,
          capability: isKafka ? "streaming" : "batch",
          targetDatasetRef: datasetRef.trim(),
          mode,
          schedule,
          configSummary,
        },
        { idempotencyKey: idempotencyRef },
      );
    }, [
      client,
      connectorName,
      cronText,
      datasetRef,
      displayName,
      everySecondsText,
      idempotencyRef,
      incrementalColumn,
      isIncremental,
      isKafka,
      isPostgres,
      isRest,
      mode,
      kafkaBatchLimitText,
      kafkaBootstrapServers,
      kafkaConnectionMode,
      kafkaConsumerGroup,
      kafkaPartitionMode,
      kafkaPartitionText,
      kafkaStreamName,
      kafkaTopic,
      kafkaUpstreamMode,
      krakenSymbol,
      checkpointLivenessText,
      maxBrokerLagText,
      maxCheckpointDurationText,
      resourceName,
      scheduleMode,
      secretRef,
      source.kind,
      source.sourceName,
      sourceType,
      syncName,
      tableName,
    ]),
    { onSuccess: (sync) => onCreated(sync) },
  );

  const isNameValid =
    displayName.trim().length > 0 && isValidIdentifier(syncName);
  const isDatasetValid = isValidDatasetRef(datasetRef.trim());
  const isScheduleValid =
    scheduleMode === "manual" ||
    (scheduleMode === "interval" &&
      Number.parseInt(everySecondsText, 10) > 0) ||
    (scheduleMode === "cron" && cronText.trim().length > 0);
  const isSourceConfigValid = isPostgres
    ? tableName.trim().length > 0 && secretRef.trim().length > 0
    : isRest
      ? connectorName.trim().length > 0 &&
        resourceName.trim().length > 0 &&
        !hasRestResourceMismatch &&
        !restConnectorQuery.error
      : isKafka
        ? kafkaTopic.trim().length > 0 &&
          kafkaConsumerGroup.trim().length > 0 &&
          kafkaStreamName.trim().length > 0 &&
          (kafkaPartitionMode === "all" || Number.parseInt(kafkaPartitionText, 10) >= 0) &&
          Number.parseInt(kafkaBatchLimitText, 10) > 0 &&
          Number.parseInt(checkpointLivenessText, 10) > 0 &&
          Number.parseInt(maxCheckpointDurationText, 10) > 0 &&
          Number.parseInt(maxBrokerLagText, 10) >= 0 &&
          (kafkaUpstreamMode !== "kraken" || krakenSymbol.trim().length > 0)
      : false;
  const canCreate =
    isNameValid && isDatasetValid && isScheduleValid && isSourceConfigValid;

  if (!isRunnableType) {
    return (
      <div className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="text-[15px] font-semibold">새 동기화</div>
          <Button variant="ghost" size="sm" onClick={onCancel}>
            <X className="size-3.5" /> 취소
          </Button>
        </div>
        <div className="rounded border bg-muted/40 p-3 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{source.kind}</span>{" "}
          타입은 반복 동기화 데이터플레인을 지원하지 않습니다. CSV·배치·미디어
          업로드는 업로드 시점에 즉시 커밋되고, 웹훅·CDC는 수신 시점에
          처리됩니다. <StatusPill intent="neutral">future</StatusPill>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[15px] font-semibold">새 동기화</div>
          <div className="text-[11px] text-muted-foreground">
            소스에서 데이터를 읽어 Foundry 데이터셋으로 가져오는 작업을
            정의합니다.
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          aria-label="동기화 편집기 닫기"
          onClick={onCancel}
        >
          <X className="size-3.5" /> 취소
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <div className="space-y-3">
          <EditorCard icon={Database} title="핵심 구성">
            <div className="space-y-3">
              <div className="space-y-1">
                <Label className="text-[11px]">동기화 이름</Label>
                <Input
                  data-testid="new-sync-display-name"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="예: 주문 테이블 동기화"
                  className="h-7 text-xs"
                />
                {displayName ? (
                  <div className="font-mono text-[10px] text-muted-foreground">
                    sync_name={syncName || "(영문/숫자/_ 필요)"}
                  </div>
                ) : null}
              </div>
              <div className="space-y-1">
                <Label className="text-[11px]">목적지 데이터셋</Label>
                <Input
                  data-testid="new-sync-dataset-ref"
                  value={datasetRef}
                  onChange={(event) => {
                    setIsDatasetRefTouched(true);
                    setDatasetRef(event.target.value);
                  }}
                  placeholder="namespace.name"
                  className={cn(
                    "h-7 font-mono text-xs",
                    datasetRef && !isDatasetValid && "border-destructive",
                  )}
                />
                <div className="text-[10px] text-muted-foreground">
                  동기화가 생성할 Foundry 데이터셋 위치입니다.
                </div>
                {selectedRestResource ? (
                  <div className="text-[10px] text-muted-foreground">
                    REST 리소스{" "}
                    <span className="font-mono">
                      {selectedRestResource.resourceName}
                    </span>
                    의 datasetRef와 자동으로 맞춥니다.
                  </div>
                ) : null}
              </div>
              <div className="space-y-1">
                <Label className="text-[11px]">트랜잭션 유형</Label>
                <div className="space-y-1.5">
                  {TRANSACTION_MODES.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setMode(option.value)}
                      className={cn(
                        "flex w-full items-start gap-2 rounded border p-2 text-left",
                        mode === option.value
                          ? "border-primary bg-accent"
                          : "hover:bg-muted/60",
                      )}
                    >
                      <span
                        className={cn(
                          "mt-0.5 size-3 shrink-0 rounded-full border",
                          mode === option.value
                            ? "border-4 border-primary"
                            : "border-muted-foreground/40",
                        )}
                      />
                      <span className="text-[11px] font-medium">
                        {option.label}
                        <span className="block text-[10px] font-normal text-muted-foreground">
                          {option.description}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </EditorCard>
          <EditorCard icon={CalendarClock} title="일정">
            <div className="space-y-2">
              <Select
                value={scheduleMode}
                onValueChange={(value) =>
                  setScheduleMode(value as ScheduleMode)
                }
              >
                <SelectTrigger size="sm" className="w-full text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="manual">수동 실행</SelectItem>
                  <SelectItem value="interval">간격 (interval)</SelectItem>
                  <SelectItem value="cron">cron</SelectItem>
                </SelectContent>
              </Select>
              {scheduleMode === "interval" ? (
                <div className="flex items-center gap-2">
                  <Input
                    value={everySecondsText}
                    onChange={(event) =>
                      setEverySecondsText(event.target.value)
                    }
                    className="h-7 w-24 font-mono text-xs"
                    inputMode="numeric"
                  />
                  <span className="text-[11px] text-muted-foreground">
                    초마다 실행
                  </span>
                </div>
              ) : null}
              {scheduleMode === "cron" ? (
                <Input
                  value={cronText}
                  onChange={(event) => setCronText(event.target.value)}
                  className="h-7 font-mono text-xs"
                  placeholder="분 시 일 월 요일"
                />
              ) : null}
              <div className="text-[10px] text-muted-foreground">
                새로 생성된 동기화에 일정을 설정하는 것이 좋습니다.
              </div>
            </div>
          </EditorCard>
        </div>

        <div className="space-y-3">
          {isPostgres ? (
            <EditorCard icon={Database} title="소스별 구성 — 테이블">
              <div className="space-y-3">
                <div className="space-y-1">
                  <Label className="text-[11px]">테이블 이름</Label>
                  <Input
                    value={tableName}
                    onChange={(event) => setTableName(event.target.value)}
                    placeholder="예: erp_orders"
                    className="h-7 font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">자격 증명 (secret ref)</Label>
                  <Input
                    value={secretRef}
                    onChange={(event) => setSecretRef(event.target.value)}
                    placeholder="데이터베이스 URL secret ref"
                    className="h-7 font-mono text-xs"
                  />
                  <div className="text-[10px] text-muted-foreground">
                    소스에 저장된 자격 증명 ref가 자동으로 채워집니다.
                  </div>
                </div>
              </div>
            </EditorCard>
          ) : null}
          {isRest ? (
            <EditorCard icon={Database} title="소스별 구성 — REST 리소스">
              <div className="space-y-3">
                <div className="space-y-1">
                  <Label className="text-[11px]">커넥터 이름</Label>
                  <Input
                    value={connectorName}
                    onChange={(event) => {
                      setConnectorName(event.target.value);
                      setResourceName("");
                    }}
                    className="h-7 font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">리소스 이름</Label>
                  {restResources.length > 0 ? (
                    <Select
                      value={resourceName}
                      onValueChange={(value) => {
                        setResourceName(value);
                        const resource = findRestResource(
                          restResources,
                          value,
                        );
                        if (resource) {
                          setDatasetRef(resource.datasetRef);
                          setIsDatasetRefTouched(false);
                        }
                      }}
                    >
                      <SelectTrigger
                        size="sm"
                        data-testid="new-sync-rest-resource"
                        className="w-full font-mono text-xs"
                      >
                        <SelectValue placeholder="리소스를 선택하세요" />
                      </SelectTrigger>
                      <SelectContent>
                        {restResources.map((resource) => (
                          <SelectItem
                            key={resource.resourceName}
                            value={resource.resourceName}
                          >
                            {resource.resourceName} · {resource.datasetRef}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input
                      value={resourceName}
                      onChange={(event) => setResourceName(event.target.value)}
                      placeholder="예: orders"
                      className="h-7 font-mono text-xs"
                    />
                  )}
                  {selectedRestResource ? (
                    <div className="font-mono text-[10px] text-muted-foreground">
                      datasetRef={selectedRestResource.datasetRef}
                    </div>
                  ) : null}
                  {hasRestResourceMismatch ? (
                    <StatusPill intent="warning">
                      리소스 datasetRef와 목적지 데이터셋이 달라 생성할 수 없습니다
                    </StatusPill>
                  ) : null}
                  {restConnectorQuery.error ? (
                    <ErrorState
                      error={restConnectorQuery.error}
                      onRetry={() => void restConnectorQuery.reload()}
                    />
                  ) : null}
                </div>
              </div>
            </EditorCard>
          ) : null}
          {isKafka ? (
            <EditorCard icon={Radio} title="소스별 구성 — Kafka topic">
              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-1 md:col-span-2">
                  <Label className="text-[11px]">Topic</Label>
                  <Input
                    value={kafkaTopic}
                    onChange={(event) => {
                      const topic = event.target.value;
                      setKafkaTopic(topic);
                      if (!kafkaStreamName) {
                        setKafkaStreamName(topic.replace(/[^a-zA-Z0-9_-]/g, "-"));
                      }
                    }}
                    placeholder="crypto.trades"
                    className="h-7 font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">Consumer group</Label>
                  <Input
                    value={kafkaConsumerGroup}
                    onChange={(event) => setKafkaConsumerGroup(event.target.value)}
                    className="h-7 font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">Stream name</Label>
                  <Input
                    value={kafkaStreamName}
                    onChange={(event) => setKafkaStreamName(event.target.value)}
                    className="h-7 font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">Partition strategy</Label>
                  <Select
                    value={kafkaPartitionMode}
                    onValueChange={(value) =>
                      setKafkaPartitionMode(value as "all" | "single")
                    }
                  >
                    <SelectTrigger size="sm" className="w-full text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All partitions · 자동 발견</SelectItem>
                      <SelectItem value="single">Single partition</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">Micro-batch records</Label>
                  <Input
                    value={kafkaBatchLimitText}
                    onChange={(event) => setKafkaBatchLimitText(event.target.value)}
                    inputMode="numeric"
                    className="h-7 font-mono text-xs"
                  />
                </div>
                {kafkaPartitionMode === "single" ? (
                  <div className="space-y-1 md:col-span-2">
                    <Label className="text-[11px]">Partition number</Label>
                    <Input
                      value={kafkaPartitionText}
                      onChange={(event) => setKafkaPartitionText(event.target.value)}
                      inputMode="numeric"
                      className="h-7 font-mono text-xs"
                    />
                  </div>
                ) : (
                  <div className="rounded border border-primary/20 bg-primary/5 px-2.5 py-2 text-[10px] text-muted-foreground md:col-span-2">
                    실행할 때 topic metadata를 다시 읽고 모든 partition을 독립 offset으로
                    체크포인트합니다. topic partition이 늘어나도 다음 micro-batch부터 자동 반영됩니다.
                  </div>
                )}
                <div className="space-y-1 md:col-span-2">
                  <Label className="text-[11px]">Upstream producer</Label>
                  <Select
                    value={kafkaUpstreamMode}
                    onValueChange={(value) =>
                      setKafkaUpstreamMode(value as "external" | "kraken")
                    }
                  >
                    <SelectTrigger size="sm" className="w-full text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="external">외부 Kafka producer</SelectItem>
                      <SelectItem value="kraken">Kraken WebSocket v2 · live trades</SelectItem>
                    </SelectContent>
                  </Select>
                  <div className="text-[10px] text-muted-foreground">
                    Kraken을 선택하면 상시 worker가 공개 trade feed를 이 topic에 publish한 뒤 같은 checkpoint 경로로 Dataset에 보관합니다.
                  </div>
                </div>
                {kafkaUpstreamMode === "kraken" ? (
                  <div className="space-y-1 md:col-span-2">
                    <Label className="text-[11px]">Kraken symbol</Label>
                    <Input
                      value={krakenSymbol}
                      onChange={(event) => setKrakenSymbol(event.target.value)}
                      placeholder="BTC/USD"
                      className="h-7 font-mono text-xs"
                    />
                    <div className="font-mono text-[10px] text-muted-foreground">
                      wss://ws.kraken.com/v2 · channel=trade · snapshot=false
                    </div>
                  </div>
                ) : null}
              </div>
            </EditorCard>
          ) : null}
          {isKafka ? (
            <EditorCard icon={Activity} title="Production monitoring">
              <div className="grid gap-3 md:grid-cols-3">
                <div className="space-y-1">
                  <Label className="text-[11px]">Checkpoint liveness · sec</Label>
                  <Input
                    value={checkpointLivenessText}
                    onChange={(event) => setCheckpointLivenessText(event.target.value)}
                    inputMode="numeric"
                    className="h-7 font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">Max checkpoint · ms</Label>
                  <Input
                    value={maxCheckpointDurationText}
                    onChange={(event) => setMaxCheckpointDurationText(event.target.value)}
                    inputMode="numeric"
                    className="h-7 font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">Max total lag · records</Label>
                  <Input
                    value={maxBrokerLagText}
                    onChange={(event) => setMaxBrokerLagText(event.target.value)}
                    inputMode="numeric"
                    className="h-7 font-mono text-xs"
                  />
                </div>
              </div>
              <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
                Worker heartbeat, checkpoint liveness, duration, total lag, throughput를 매 상태 조회마다 판정합니다.
              </p>
            </EditorCard>
          ) : null}
          {isPostgres ? (
            <EditorCard icon={FileSearch} title="SQL 쿼리">
              <pre className="overflow-x-auto rounded bg-muted/60 p-2.5 font-mono text-[11px] leading-5">
                {`SELECT * FROM ${tableName.trim() || "<테이블>"}`}
                {isIncremental && incrementalColumn.trim()
                  ? `\nWHERE ${incrementalColumn.trim()} > :lastValue`
                  : ""}
              </pre>
              <div className="mt-1 text-[10px] text-muted-foreground">
                동기화에 포함된 테이블의 기본 쿼리는 SELECT * 입니다. 실행
                쿼리는 위 구성에서 파생됩니다.
              </div>
            </EditorCard>
          ) : null}
          {isKafka ? (
            <EditorCard icon={Activity} title="Streaming checkpoint">
              <p className="text-[11px] leading-5 text-muted-foreground">
                topic·partition·consumer group별 마지막 offset은 데이터셋 버전
                커밋이 성공한 뒤에만 전진합니다. 재시작은 커밋된 offset 다음
                레코드부터 이어집니다.
              </p>
            </EditorCard>
          ) : null}
          <EditorCard
            icon={Database}
            title="Incremental"
            meta={
              <Switch
                checked={isIncremental}
                onCheckedChange={setIsIncremental}
                disabled={!isPostgres}
                aria-label="Incremental 활성화"
              />
            }
          >
            {isIncremental && isPostgres ? (
              <div className="space-y-2">
                <p className="text-[11px] text-muted-foreground">
                  타임스탬프나 id 같은 단조 증가 컬럼을 지정하면, 이미 가져온
                  마지막 값(체크포인트) 이후의 행만 가져옵니다. 첫 실행은 전체를
                  가져옵니다.
                </p>
                <Input
                  value={incrementalColumn}
                  onChange={(event) => setIncrementalColumn(event.target.value)}
                  placeholder="체크포인트 컬럼 (예: updated_at)"
                  className="h-7 font-mono text-xs"
                />
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground">
                {isPostgres
                  ? "비활성 — 매 실행마다 전체 데이터를 가져옵니다 (트랜잭션 유형에 따라 덮어쓰기/추가)."
                  : isKafka
                    ? "Kafka 동기화는 topic partition offset 체크포인트를 자동으로 사용합니다."
                    : "REST 동기화는 커넥터의 커서 설정을 따릅니다."}
              </p>
            )}
          </EditorCard>

          {createSync.error ? <ErrorState error={createSync.error} /> : null}

          <div className="flex items-center justify-between rounded border bg-muted/40 px-3 py-2">
            <span className="font-mono text-[10px] text-muted-foreground">
              Idempotency-Key {idempotencyRef.slice(0, 24)}…
            </span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={onCancel}>
                취소
              </Button>
              <Button
                size="sm"
                disabled={!canCreate || createSync.isRunning}
                onClick={() => void createSync.execute(undefined)}
              >
                {createSync.isRunning ? "생성 중..." : "동기화 생성"}
              </Button>
            </div>
          </div>
          {!isNameValid && displayName ? (
            <StatusPill intent="warning">
              이름에서 유효한 식별자를 만들 수 없습니다
            </StatusPill>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function EditorCard({
  icon: Icon,
  title,
  meta,
  children,
}: {
  icon: typeof Database;
  title: string;
  meta?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded border bg-card">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="flex items-center gap-1.5 text-[13px] font-semibold">
          <Icon className="size-3.5 text-muted-foreground" />
          {title}
        </span>
        {meta}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}
