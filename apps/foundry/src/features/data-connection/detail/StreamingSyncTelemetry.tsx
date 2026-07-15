import type {
  SourceManagedStreamingSyncStatus,
  SourceManagedSync,
  SourceManagedSyncRun,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import {
  Activity,
  AlertTriangle,
  Archive,
  Check,
  Play,
  Radio,
  RefreshCw,
  Square,
  Wifi,
} from "lucide-react";
import { useCallback, useEffect, useRef } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { readNumberField, readTextField } from "../source-model";
import { useStreamingSyncStatus } from "../use-source-queries";

interface StreamingSyncTelemetryProps {
  sync: SourceManagedSync;
  latestRun: SourceManagedSyncRun | null;
  isSourceDisabled?: boolean;
}

export function StreamingSyncTelemetry({
  sync,
  latestRun,
  isSourceDisabled = false,
}: StreamingSyncTelemetryProps) {
  const client = useFoundryLiteClient();
  const statusQuery = useStreamingSyncStatus(sync.sourceType === "kafka" ? sync.syncName : null);
  const reloadStatus = statusQuery.reload;
  const startIdempotencyKey = useRef(
    streamingIdempotencyKey("start", sync.syncName, sync.configFingerprint),
  );
  const stopIdempotencyKey = useRef(
    streamingIdempotencyKey("stop", sync.syncName, sync.configFingerprint),
  );

  useEffect(() => {
    startIdempotencyKey.current = streamingIdempotencyKey(
      "start",
      sync.syncName,
      sync.configFingerprint,
    );
    stopIdempotencyKey.current = streamingIdempotencyKey(
      "stop",
      sync.syncName,
      sync.configFingerprint,
    );
  }, [sync.configFingerprint, sync.syncName]);

  const startStream = useFoundryLiteMutation(
    useCallback(
      () =>
        client.sources.managedSyncs.startStream(
          sync.syncName,
          { expectedConfigFingerprint: sync.configFingerprint },
          {
            idempotencyKey: startIdempotencyKey.current,
          },
        ),
      [client, sync.configFingerprint, sync.syncName],
    ),
    {
      onSuccess: () => {
        startIdempotencyKey.current = streamingIdempotencyKey(
          "start",
          sync.syncName,
          sync.configFingerprint,
        );
        void reloadStatus();
      },
    },
  );
  const stopStream = useFoundryLiteMutation(
    useCallback(
      () =>
        client.sources.managedSyncs.stopStream(
          sync.syncName,
          { expectedConfigFingerprint: sync.configFingerprint },
          {
            idempotencyKey: stopIdempotencyKey.current,
          },
        ),
      [client, sync.configFingerprint, sync.syncName],
    ),
    {
      onSuccess: () => {
        stopIdempotencyKey.current = streamingIdempotencyKey(
          "stop",
          sync.syncName,
          sync.configFingerprint,
        );
        void reloadStatus();
      },
    },
  );

  if (sync.sourceType !== "kafka") return null;

  const status = statusQuery.data;
  const telemetry = status?.telemetry;
  const kafka = telemetry?.kafka;
  const upstream = telemetry?.upstream;
  const fallbackSummary = latestRun?.resultSummary;
  const fallbackCheckpoint = latestRun?.checkpointEnd;
  const outputRecords = telemetry?.outputRecords ?? readNumberField(fallbackSummary, "eventCount") ?? 0;
  const currentOffset = kafka?.currentOffset ?? readNumberField(fallbackCheckpoint, "offset");
  const brokerLag = kafka?.brokerLag ?? readNumberField(fallbackSummary, "brokerLag");
  const partitionCount = kafka?.partitionCount ?? 1;
  const topic = kafka?.topic ?? readTextField(sync.configSummary, "topic") ?? "—";
  const lifecycle = status?.lifecycleState ?? "IDLE";
  const isActive = isStreamingActive(status);
  const checkpointAt = telemetry?.lastCheckpointAt ?? latestRun?.completedAt ?? null;
  const stages = [
    {
      icon: Wifi,
      label: "Upstream",
      value: upstream?.connectionState ?? "external producer",
      isDone: upstream ? upstream.connectionState === "CONNECTED" : isActive,
    },
    {
      icon: Radio,
      label: "Kafka",
      value:
        partitionCount > 1
          ? `${topic} · ${partitionCount} partitions`
          : currentOffset == null
            ? topic
            : `${topic} · offset ${currentOffset}`,
      isDone: isActive && kafka?.brokerLagStatus !== "unavailable",
    },
    {
      icon: Archive,
      label: "Dataset",
      value: `${outputRecords.toLocaleString()} records · this lifecycle`,
      isDone: Boolean(telemetry?.lastDatasetVersionId ?? latestRun?.datasetVersionId),
    },
    {
      icon: Check,
      label: "Checkpoint",
      value: checkpointAt ? formatLiveTime(checkpointAt) : "waiting",
      isDone: Boolean(checkpointAt),
    },
  ] as const;
  const metrics = [
    ["Input rate", rateValue(telemetry?.inputRatePerSecond)],
    ["Output rate", rateValue(telemetry?.outputRatePerSecond)],
    ["Broker lag", brokerLag == null ? "—" : brokerLag.toLocaleString()],
    ["Checkpoint", durationValue(telemetry?.checkpointDurationMs)],
    ["Reconnects", String(upstream?.reconnectCount ?? 0)],
    ["Partitions", String(partitionCount)],
  ] as const;

  return (
    <section
      className="overflow-hidden rounded border bg-card"
      data-testid="streaming-sync-telemetry"
      aria-label="실시간 스트리밍 상태"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-3 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Activity className="size-3.5 text-primary" />
            <div className="text-[13px] font-semibold">Streaming service</div>
            <StatusPill intent={lifecycleIntent(lifecycle)}>{lifecycle}</StatusPill>
            {status?.health ? (
              <StatusPill intent={healthIntent(status.health.status)}>
                health {status.health.status}
              </StatusPill>
            ) : null}
            {status?.isWorkerStale ? <StatusPill intent="danger">stale lease</StatusPill> : null}
          </div>
          <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
            {status?.workflowRunId ? `workflow=${status.workflowRunId}` : "아직 시작된 durable workflow가 없습니다"}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            aria-label="실시간 상태 새로고침"
            disabled={statusQuery.isRefreshing}
            onClick={() => void statusQuery.reload()}
          >
            <RefreshCw className={cn("size-3.5", statusQuery.isRefreshing && "animate-spin")} />
          </Button>
          <Button
            size="sm"
            disabled={isActive || isSourceDisabled || startStream.isRunning}
            onClick={() => void startStream.execute(undefined)}
          >
            <Play className="size-3.5" />
            {startStream.isRunning ? "시작 요청 중" : "Start"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!isActive || stopStream.isRunning}
            onClick={() => void stopStream.execute(undefined)}
          >
            <Square className="size-3.5" />
            {stopStream.isRunning ? "중지 요청 중" : "Stop"}
          </Button>
        </div>
      </div>

      <div className="grid bg-[#F6F8FA] md:grid-cols-4">
        {stages.map(({ icon: Icon, label, value, isDone }) => (
          <div
            key={label}
            className="flex min-w-0 items-center gap-2 border-b px-3 py-2.5 last:border-b-0 md:border-r md:border-b-0 md:last:border-r-0"
          >
            <span
              className={cn(
                "flex size-7 items-center justify-center rounded border bg-white",
                isDone && "border-success/50 text-success",
              )}
            >
              <Icon className="size-3.5" />
            </span>
            <span className="min-w-0">
              <span className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {label}
              </span>
              <span className="block truncate font-mono text-[11px]">{value}</span>
            </span>
            <span
              className={cn(
                "ml-auto size-1.5 rounded-full bg-muted-foreground/30",
                isDone && "bg-success",
              )}
            />
          </div>
        ))}
      </div>

      <div className="grid divide-y border-t sm:grid-cols-3 sm:divide-x sm:divide-y-0 xl:grid-cols-6">
        {metrics.map(([label, value]) => (
          <div key={label} className="px-3 py-2">
            <div className="text-[10px] text-muted-foreground">{label}</div>
            <div className="mt-0.5 font-mono text-xs font-semibold">{value}</div>
          </div>
        ))}
      </div>

      {kafka?.partitions && kafka.partitions.length > 0 ? (
        <div className="border-t">
          <div className="border-b bg-muted/25 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Partition checkpoints
          </div>
          <div className="grid divide-y md:grid-cols-2 md:divide-x md:divide-y-0 xl:grid-cols-4">
            {kafka.partitions.map((partition) => (
              <div
                key={partition.partition ?? "unknown"}
                className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 px-3 py-2 font-mono text-[10px]"
              >
                <span className="font-semibold">p{partition.partition ?? "—"}</span>
                <span className="text-right text-muted-foreground">
                  offset {partition.currentOffset ?? "—"}
                </span>
                <span className="text-muted-foreground">
                  {partition.eventCount ?? 0} records
                </span>
                <span className="text-right text-muted-foreground">
                  lag {partition.brokerLag ?? "—"}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {status?.health?.rules.length ? (
        <div className="border-t">
          <div className="flex items-center justify-between border-b bg-muted/25 px-3 py-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Production monitors
            </span>
            <span className="font-mono text-[10px] text-muted-foreground">
              {status.health.monitoringProfile ?? "source_streaming/v1"}
            </span>
          </div>
          <div className="grid gap-px bg-border md:grid-cols-2 xl:grid-cols-3">
            {status.health.rules.map((rule) => (
              <div key={rule.ruleId} className="bg-card px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium">{rule.label}</span>
                  <StatusPill intent={monitorIntent(rule.status)}>{rule.status}</StatusPill>
                </div>
                <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                  observed={monitorValue(rule.observedValue, rule.unit)} · threshold=
                  {monitorValue(rule.threshold, rule.unit)}
                </div>
                {rule.operatorAction && ["WARN", "FAIL"].includes(rule.status) ? (
                  <div className="mt-1 text-[10px] leading-4 text-warning">
                    {rule.operatorAction}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-3 py-2 font-mono text-[10px] text-muted-foreground">
        <span>heartbeat={formatLiveTime(telemetry?.lastHeartbeatAt ?? null)}</span>
        <span>checkpoint={formatLiveTime(checkpointAt)}</span>
        <span>batches={telemetry?.archivedBatches ?? 0}</span>
        <span>desired={status?.desiredState ?? "STOPPED"}</span>
      </div>

      {streamingGuidance(status) ? (
        <div
          className="flex items-start gap-2 border-t border-warning/30 bg-warning/5 px-3 py-2 text-[11px]"
          role="status"
        >
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />
          <span>{streamingGuidance(status)}</span>
        </div>
      ) : null}
      {statusQuery.error ? <ErrorState error={statusQuery.error} onRetry={() => void statusQuery.reload()} /> : null}
      {startStream.error ? <ErrorState error={startStream.error} onRetry={() => void startStream.execute(undefined)} /> : null}
      {stopStream.error ? <ErrorState error={stopStream.error} onRetry={() => void stopStream.execute(undefined)} /> : null}
    </section>
  );
}

function isStreamingActive(status: SourceManagedStreamingSyncStatus | null): boolean {
  return Boolean(
    status &&
      status.desiredState === "RUNNING" &&
      ["requested", "starting", "running", "start_unknown"].includes(status.status),
  );
}

function streamingIdempotencyKey(
  action: "start" | "stop",
  syncName: string,
  configFingerprint: string,
): string {
  return idempotencyKey(
    `stream-${action}`,
    `${syncName}:${configFingerprint}:${crypto.randomUUID()}`,
  );
}

function lifecycleIntent(lifecycle: string): "neutral" | "info" | "success" | "warning" | "danger" {
  if (lifecycle === "RUNNING") return "success";
  if (lifecycle === "STARTING") return "info";
  if (["DEGRADED", "UNHEALTHY", "STOP_REQUESTED"].includes(lifecycle)) return "warning";
  if (lifecycle === "FAILED") return "danger";
  return "neutral";
}

function healthIntent(status: string): "neutral" | "info" | "success" | "warning" | "danger" {
  if (status === "HEALTHY") return "success";
  if (status === "DEGRADED") return "warning";
  if (status === "UNHEALTHY") return "danger";
  if (status === "PENDING") return "info";
  return "neutral";
}

function monitorIntent(status: string): "neutral" | "info" | "success" | "warning" | "danger" {
  if (status === "PASS") return "success";
  if (status === "WARN") return "warning";
  if (status === "FAIL") return "danger";
  if (status === "PENDING") return "info";
  return "neutral";
}

function monitorValue(value: number | null | undefined, unit: string | undefined): string {
  if (value == null) return "—";
  return `${value.toLocaleString()}${unit ? ` ${unit}` : ""}`;
}

function streamingGuidance(status: SourceManagedStreamingSyncStatus | null): string | null {
  if (!status || status.lifecycleState === "IDLE") return null;
  if (status.isWorkerStale) {
    return "Worker heartbeat가 lease 만료 시각을 넘었습니다. 새 worker가 takeover하면 마지막 커밋 offset부터 재개합니다.";
  }
  if (status.lifecycleState === "STARTING") {
    return "시작 요청은 저장되었습니다. streaming worker가 lease를 획득하면 RUNNING으로 전환됩니다.";
  }
  if (status.lifecycleState === "DEGRADED") {
    return "상류 WebSocket 또는 Kafka 연결을 자동 재시도 중입니다. Dataset 체크포인트는 마지막 성공 커밋에서 유지됩니다.";
  }
  if (status.lifecycleState === "STOP_REQUESTED") {
    return "중지 요청이 저장되었습니다. 현재 micro-batch 경계에서 worker가 안전하게 종료됩니다.";
  }
  return null;
}

function rateValue(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}/s`;
}

function durationValue(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value).toLocaleString()} ms`;
}

function formatLiveTime(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleTimeString();
}
