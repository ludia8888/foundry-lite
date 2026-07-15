import type { SourceManagedSync } from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import {
  AlertTriangle,
  ChevronDown,
  Database,
  FileSearch,
  Play,
  Table2,
} from "lucide-react";
import { useCallback, useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

import {
  statusIntent,
  statusLabel,
  toDatasetHref,
} from "../source-model";
import {
  useSourceSchedulerStatus,
  useSyncRuns,
} from "../use-source-queries";
import {
  readRemainingConfig,
  readSyncIncremental,
  readSyncPreQuery,
  readSyncQuery,
  TRANSACTION_MODES,
} from "./sync-config";
import { SyncFailureFlightRecorder } from "./SyncFailureFlightRecorder";
import { SyncOperationalSummary } from "./SyncOperationalSummary";
import { SyncRunHistory } from "./SyncRunHistory";
import { SyncSchedulePanel } from "./SyncSchedulePanel";
import { StreamingSyncTelemetry } from "./StreamingSyncTelemetry";

interface SyncDetailViewProps {
  sync: SourceManagedSync;
  isSourceDisabled?: boolean;
  onRunStarted?: () => void;
  onSyncUpdated: (sync: SourceManagedSync) => void;
}

/**
 * 동기화 상세 (Palantir sync 페이지 구조):
 * 좌상단 출력 데이터셋 링크 + 우상단 빌드 버튼, 좌측 핵심 구성 사이드바,
 * 우측 소스별 구성(SQL 쿼리/Incremental 카드), 하단 run evidence.
 */
export function SyncDetailView({
  sync,
  isSourceDisabled = false,
  onRunStarted,
  onSyncUpdated,
}: SyncDetailViewProps) {
  const client = useFoundryLiteClient();
  const runsQuery = useSyncRuns(sync.syncName);
  const schedulerQuery = useSourceSchedulerStatus(sync.syncName);
  const [isBuildOptionsOpen, setIsBuildOptionsOpen] = useState(false);
  const [shouldLimitBatch, setShouldLimitBatch] = useState(false);
  const [batchLimitText, setBatchLimitText] = useState("100");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const startRun = useFoundryLiteMutation(
    useCallback(
      (payload: { batchLimit: number | null }) =>
        client.sources.managedSyncs.startRun(
          sync.syncName,
          {
            triggerType: "manual",
            batchLimit: payload.batchLimit,
          },
          { idempotencyKey: idempotencyKey("sync-run", crypto.randomUUID()) },
        ),
      [client, sync.syncName],
    ),
    {
      onSuccess: () => {
        void runsQuery.reload();
        void schedulerQuery.reload();
        onRunStarted?.();
      },
    },
  );

  const recoveryRun = useFoundryLiteMutation(
    useCallback(
      () =>
        client.sources.managedSyncs.startRun(
          sync.syncName,
          { triggerType: "recovery", batchLimit: null },
          {
            idempotencyKey: idempotencyKey(
              "sync-recovery",
              crypto.randomUUID(),
            ),
          },
        ),
      [client, sync.syncName],
    ),
    {
      onSuccess: () => {
        void runsQuery.reload();
        void schedulerQuery.reload();
        void client.sources.managedSyncs.get(sync.syncName).then(onSyncUpdated, () => undefined);
        onRunStarted?.();
      },
    },
  );

  const handleBuild = () => {
    setIsBuildOptionsOpen(false);
    const parsed = Number.parseInt(batchLimitText, 10);
    void startRun.execute({
      batchLimit: shouldLimitBatch && Number.isFinite(parsed) ? parsed : null,
    });
  };

  const query = readSyncQuery(sync);
  const preQuery = readSyncPreQuery(sync);
  const incremental = readSyncIncremental(sync);
  const remainingConfig = readRemainingConfig(sync);
  const remainingConfigEntries = Object.entries(remainingConfig);
  const datasetHref = toDatasetHref(sync.targetDatasetRef);
  const latestRun = runsQuery.data?.[0] ?? null;
  const latestFailure =
    (runsQuery.data ?? []).find((run) => run.status === "failed") ?? null;
  const activeFailure =
    latestRun?.status === "failed" ||
    (schedulerQuery.data?.consecutiveFailureCount ?? 0) > 0
      ? latestFailure
      : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {datasetHref ? (
            <Link
              to={datasetHref}
              className="flex items-center gap-1.5 text-[15px] font-semibold text-primary hover:underline"
            >
              <Table2 className="size-4" />
              {sync.targetDatasetRef}
            </Link>
          ) : (
            <div className="text-[15px] font-semibold">{sync.displayName}</div>
          )}
          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className="font-mono">{sync.syncName}</span>
            <StatusPill intent={statusIntent(sync.status)}>
              {statusLabel(sync.status)}
            </StatusPill>
            <span className="font-mono">
              fp {sync.configFingerprint.slice(0, 10)}
            </span>
          </div>
        </div>
        <div className="flex items-center">
          <Button
            size="sm"
            className="rounded-r-none bg-success text-success-foreground hover:bg-success/90"
            disabled={startRun.isRunning || isSourceDisabled}
            onClick={handleBuild}
          >
            <Play className="size-3.5" />
            {startRun.isRunning ? "빌드 중..." : "빌드"}
          </Button>
          <Popover
            open={isBuildOptionsOpen}
            onOpenChange={setIsBuildOptionsOpen}
          >
            <PopoverTrigger asChild>
              <Button
                size="sm"
                aria-label="빌드 옵션"
                disabled={startRun.isRunning || isSourceDisabled}
                className="rounded-l-none border-l border-success-foreground/20 bg-success px-1.5 text-success-foreground hover:bg-success/90"
              >
                <ChevronDown className="size-3.5" />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-64 p-3">
              <div className="section-label mb-2">빌드 옵션</div>
              <label className="flex items-center gap-2 text-xs">
                <Checkbox
                  checked={shouldLimitBatch}
                  onCheckedChange={(checked) =>
                    setShouldLimitBatch(checked === true)
                  }
                />
                배치 행 수 제한
              </label>
              {shouldLimitBatch ? (
                <Input
                  value={batchLimitText}
                  onChange={(event) => setBatchLimitText(event.target.value)}
                  className="mt-2 h-7 font-mono text-xs"
                  inputMode="numeric"
                />
              ) : null}
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {isSourceDisabled ? (
        <div
          className="flex items-start gap-2 rounded border border-warning/40 bg-warning/5 px-3 py-2 text-[11px]"
          data-testid="source-disabled-run-guard"
          role="status"
        >
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />
          <div>
            <div className="font-semibold text-warning">Source가 비활성 상태입니다</div>
            <p className="mt-0.5 text-muted-foreground">
              기존 실행 이력은 보존되지만 새 빌드와 예약 실행은 차단됩니다. 개요에서 Source를 다시
              활성화하면 실행할 수 있습니다.
            </p>
          </div>
        </div>
      ) : null}

      <SyncOperationalSummary
        sync={sync}
        decision={schedulerQuery.data}
        latestRun={latestRun}
        isScheduleLoading={schedulerQuery.isLoading}
      />

      <SyncFailureFlightRecorder
        sourceType={sync.sourceType}
        decision={schedulerQuery.data}
        latestFailure={activeFailure}
        isRecoveryRunning={recoveryRun.isRunning}
        isRecoveryDisabled={isSourceDisabled}
        onInspectFailure={setSelectedRunId}
        onStartRecovery={() => void recoveryRun.execute(undefined)}
      />

      <StreamingSyncTelemetry
        sync={sync}
        latestRun={latestRun}
        isSourceDisabled={isSourceDisabled}
      />

      {startRun.error ? (
        <ErrorState
          error={startRun.error}
          onRetry={() => void startRun.execute({ batchLimit: null })}
        />
      ) : null}
      {startRun.result ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-success/30 bg-success/5 px-3 py-2 font-mono text-[11px]">
          <span>run_id={startRun.result.runId}</span>
          <StatusPill intent={statusIntent(startRun.result.status)}>
            {statusLabel(startRun.result.status)}
          </StatusPill>
          {startRun.requestId ? (
            <span className="text-muted-foreground">
              request_id={startRun.requestId}
            </span>
          ) : null}
        </div>
      ) : null}
      {recoveryRun.error ? (
        <ErrorState
          error={recoveryRun.error}
          onRetry={() => void recoveryRun.execute(undefined)}
        />
      ) : null}
      {recoveryRun.result ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-primary/30 bg-primary/5 px-3 py-2 font-mono text-[11px]">
          <span>recovery_run_id={recoveryRun.result.runId}</span>
          <StatusPill intent={statusIntent(recoveryRun.result.status)}>
            {statusLabel(recoveryRun.result.status)}
          </StatusPill>
          <span className="text-muted-foreground">성공 시 다음 예약 시점이 새로 계산됩니다.</span>
        </div>
      ) : null}
      {schedulerQuery.error ? (
        <ErrorState
          error={schedulerQuery.error}
          onRetry={() => void schedulerQuery.reload()}
        />
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <div className="space-y-3">
          <ConfigCard icon={Database} title="핵심 구성">
            <ConfigRow label="목적지">
              {datasetHref ? (
                <Link
                  to={datasetHref}
                  className="font-mono text-[11px] text-primary hover:underline"
                >
                  {sync.targetDatasetRef}
                </Link>
              ) : (
                <span className="font-mono text-[11px]">
                  {sync.targetMediaSetId ?? "—"}
                </span>
              )}
            </ConfigRow>
            <ConfigRow label="트랜잭션 유형">
              <div className="space-y-1">
                {TRANSACTION_MODES.map((mode) => (
                  <div key={mode.value} className="flex items-start gap-1.5">
                    <span
                      className={
                        sync.mode === mode.value
                          ? "mt-1 size-2 shrink-0 rounded-full bg-primary"
                          : "mt-1 size-2 shrink-0 rounded-full border border-muted-foreground/40"
                      }
                    />
                    <span
                      className={
                        sync.mode === mode.value
                          ? "text-[11px] font-medium"
                          : "text-[11px] text-muted-foreground"
                      }
                    >
                      {mode.label}
                      <span className="block text-[10px] font-normal text-muted-foreground">
                        {mode.description}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </ConfigRow>
            <ConfigRow label="용량">
              <span className="text-[11px]">{sync.capability}</span>
            </ConfigRow>
          </ConfigCard>
          <SyncSchedulePanel
            key={`${sync.syncName}:${sync.configFingerprint}`}
            sync={sync}
            decision={schedulerQuery.data}
            onUpdated={(updated) => {
              onSyncUpdated(updated);
              void schedulerQuery.reload();
            }}
          />
        </div>

        <div className="space-y-3">
          {query ? (
            <ConfigCard icon={FileSearch} title="SQL 쿼리">
              <pre className="overflow-x-auto rounded bg-muted/60 p-2.5 font-mono text-[11px] leading-5">
                {query}
              </pre>
              {preQuery ? (
                <>
                  <div className="section-label mt-2 mb-1">선 쿼리</div>
                  <pre className="overflow-x-auto rounded bg-muted/60 p-2.5 font-mono text-[11px] leading-5">
                    {preQuery}
                  </pre>
                </>
              ) : null}
            </ConfigCard>
          ) : null}
          {incremental ? (
            <ConfigCard
              icon={Database}
              title="Incremental"
              meta={<StatusPill intent="success">활성</StatusPill>}
            >
              <p className="mb-2 text-[11px] text-muted-foreground">
                단조 증가 컬럼 기준으로 마지막 값 이후의 행만 추가로 가져옵니다.
              </p>
              <ConfigRow label="컬럼">
                <span className="font-mono text-[11px]">
                  {incremental.column}
                </span>
              </ConfigRow>
              <ConfigRow label="마지막 값">
                <span className="font-mono text-[11px]">
                  {incremental.initialValue || "— (첫 실행 전)"}
                </span>
              </ConfigRow>
            </ConfigCard>
          ) : null}
          {remainingConfigEntries.length > 0 ? (
            <ConfigCard icon={Database} title="소스별 구성">
              {remainingConfigEntries.map(([key, value]) => (
                <ConfigRow key={key} label={configLabel(key)}>
                  <span className="break-words font-mono text-[11px]">
                    {configValue(key, value)}
                  </span>
                </ConfigRow>
              ))}
            </ConfigCard>
          ) : null}
        </div>
      </div>

      <SyncRunHistory
        runs={runsQuery.data ?? []}
        isLoading={runsQuery.isLoading}
        error={runsQuery.error}
        selectedRunId={selectedRunId}
        onSelectRun={setSelectedRunId}
        onReload={() => void runsQuery.reload()}
      />
    </div>
  );
}

function configLabel(key: string): string {
  const labels: Record<string, string> = {
    connectorName: "커넥터",
    resourceName: "리소스",
    tableName: "테이블",
    checkpointColumn: "체크포인트",
    databaseUrlSecretRef: "DB 자격 증명",
  };
  return labels[key] ?? key;
}

function configValue(key: string, value: unknown): string {
  if (/password|token|secret/i.test(key)) return "보안 저장소 참조";
  if (typeof value === "string" || typeof value === "number") {
    return String(value);
  }
  if (typeof value === "boolean") return value ? "활성" : "비활성";
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value && typeof value === "object") return "구성됨";
  return "—";
}

function ConfigCard({
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

function ConfigRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 py-1">
      <span className="w-20 shrink-0 pt-0.5 text-[10px] tracking-[0.5px] text-muted-foreground uppercase">
        {label}
      </span>
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  );
}
