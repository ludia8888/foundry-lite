import type { TransformRunResult } from "@foundry-lite/sdk";
import {
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  CircleDashed,
  Database,
  Loader2,
  RotateCw,
  Table2,
} from "lucide-react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { datasetPreviewHref } from "../code-model";
import type { CodeRepositoryState } from "../use-code-repository";

const PHASE_LABEL: Record<string, { label: string; intent: StatusIntent }> = {
  idle: { label: "대기", intent: "neutral" },
  registering: { label: "등록 중", intent: "info" },
  running: { label: "실행 중", intent: "info" },
  succeeded: { label: "성공", intent: "success" },
  failed: { label: "실패", intent: "danger" },
};

interface ExecutionPanelProps {
  code: CodeRepositoryState;
  lastIdempotencyKey: string | null;
  onRerun: () => void;
  canRerun: boolean;
}

/**
 * 우측 실행 패널: build/test + run evidence + preview output.
 * - phase 배지 + idempotency key / request id evidence (mono)
 * - 성공: 출력 데이터셋 버전·row count·manifest·schema hash + Dataset Preview 딥링크
 * - 실패: ErrorState (request id·retryability) + 재실행
 */
export function ExecutionPanel({
  code,
  lastIdempotencyKey,
  onRerun,
  canRerun,
}: ExecutionPanelProps) {
  const { submit, loadOutputPreview, outputPreview } = code;
  const phase = submit.phase;
  const phaseMeta = PHASE_LABEL[phase] ?? PHASE_LABEL.idle;
  const run = submit.run;

  return (
    <div className="flex w-96 shrink-0 flex-col border-l bg-card">
      {/* 헤더 */}
      <div className="flex h-8 shrink-0 items-center justify-between border-b px-3">
        <span className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
          실행 · 빌드
        </span>
        <StatusPill intent={phaseMeta.intent}>
          {phase === "running" || phase === "registering" ? (
            <Loader2 className="size-3 animate-spin" />
          ) : null}
          {phaseMeta.label}
        </StatusPill>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {/* mutation evidence: idempotency key / request id */}
        <section className="space-y-1 rounded border bg-muted/40 p-2">
          <div className="text-[11px] font-semibold text-muted-foreground">
            요청 증거
          </div>
          <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
            <div className="truncate">
              idempotency_key={lastIdempotencyKey ?? "—"}
            </div>
            <div className="truncate">request_id={submit.requestId ?? "—"}</div>
            <div>
              retryable={submit.error ? String(submit.error.retryable) : "—"}
            </div>
          </div>
        </section>

        <TransformSchedulerEvidence code={code} />

        {/* 실패 상태 */}
        {phase === "failed" && submit.error ? (
          <section className="space-y-2">
            <ErrorState
              error={submit.error}
              onRetry={canRerun ? onRerun : undefined}
            />
            <div className="rounded border border-warning/30 bg-warning/5 p-2 text-[11px] text-muted-foreground">
              <div className="mb-1 font-semibold text-warning">재실행 안내</div>
              SQL을 수정한 뒤 다시 빌드하면 동일 API 이름으로 정의가 갱신되어
              재등록·재실행됩니다.
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-7 w-full text-[12px]"
              disabled={!canRerun || submit.isRunning}
              onClick={onRerun}
            >
              <RotateCw
                className={cn("size-3.5", submit.isRunning && "animate-spin")}
              />
              같은 SQL로 재실행
            </Button>
          </section>
        ) : null}

        {/* 진행 중 */}
        {(phase === "registering" || phase === "running") && !run ? (
          <section className="flex items-center gap-2 rounded border bg-primary/5 p-3 text-[12px] text-primary">
            <Loader2 className="size-4 animate-spin" />
            {phase === "registering"
              ? "transform 정의를 등록하는 중..."
              : "transform을 실행하는 중..."}
          </section>
        ) : null}

        {/* 성공: run evidence */}
        {run ? (
          <RunEvidence
            run={run}
            onPreview={() => void loadOutputPreview.execute(run)}
            isPreviewLoading={loadOutputPreview.isRunning}
          />
        ) : null}

        {/* 출력 프리뷰 */}
        {outputPreview ? (
          <OutputPreviewTable
            datasetRef={outputPreview.datasetRef}
            rows={outputPreview.rows}
          />
        ) : null}

        {loadOutputPreview.error ? (
          <ErrorState error={loadOutputPreview.error} />
        ) : null}

        {/* 초기 안내 */}
        {phase === "idle" && !run ? (
          <section className="flex flex-col items-center gap-2 rounded border border-dashed bg-muted/20 p-6 text-center">
            <CircleDashed className="size-6 text-muted-foreground/50" />
            <div className="text-[12px] font-medium text-foreground">
              빌드를 실행하면 결과가 여기에 표시됩니다
            </div>
            <div className="text-[11px] text-muted-foreground">
              상단 그린 빌드 버튼으로 SQL transform을 등록하고 실행합니다.
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}

function TransformSchedulerEvidence({
  code,
}: {
  code: CodeRepositoryState;
}) {
  const latest = code.schedulerTick.result ?? code.transformsQuery.data;
  const dueItems = latest?.due ?? [];
  const startedItems = latest?.started ?? [];
  const skippedItems = latest?.skipped ?? [];

  return (
    <section className="space-y-2 rounded border bg-card p-2.5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 text-[12px] font-semibold">
            <CalendarClock className="size-3.5 text-primary" />
            Transform scheduler
            {latest ? (
              <StatusPill intent={dueItems.length > 0 ? "info" : "neutral"}>
                due {dueItems.length}
              </StatusPill>
            ) : null}
          </div>
          <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
            evaluatedAt={latest?.evaluatedAt ?? "—"}
          </div>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-[12px]"
          disabled={code.schedulerTick.isRunning}
          onClick={() => void code.schedulerTick.execute(undefined)}
        >
          {code.schedulerTick.isRunning ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RotateCw className="size-3.5" />
          )}
          scheduler tick
        </Button>
      </div>

      <div className="grid grid-cols-4 gap-1 font-mono text-[10px]">
        <SchedulerMetric
          label="evaluated"
          value={latest ? String(latest.evaluated) : "—"}
        />
        <SchedulerMetric label="due" value={String(dueItems.length)} />
        <SchedulerMetric label="started" value={String(startedItems.length)} />
        <SchedulerMetric label="skipped" value={String(skippedItems.length)} />
      </div>

      {dueItems.length > 0 ? (
        <div className="space-y-1">
          <div className="section-label">due transforms</div>
          {dueItems.slice(0, 3).map((decision) => (
            <div
              key={decision.transformId}
              className="rounded border bg-muted/30 px-2 py-1 font-mono text-[10px]"
            >
              <div className="truncate">transform={decision.apiName}</div>
              <div className="truncate text-muted-foreground">
                reason={decision.reason} · changed=
                {decision.changedInputRefs.join(",") || "—"}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {startedItems.length > 0 ? (
        <div className="rounded border border-success/30 bg-success/5 px-2 py-1.5 font-mono text-[10px]">
          <div className="section-label mb-1">tick started runs</div>
          <div className="space-y-0.5">
            {startedItems.slice(0, 3).map((item, index) => (
              <div key={`${readSchedulerRunField(item, "transformRunId") ?? index}`}>
                transform={readSchedulerDecisionField(item, "apiName") ?? "—"} ·
                run={readSchedulerRunField(item, "transformRunId") ?? "—"} ·
                version={readSchedulerRunField(item, "versionId") ?? "—"} · rows=
                {readSchedulerRunField(item, "rowCount") ?? "—"}
              </div>
            ))}
          </div>
          {code.schedulerTick.requestId ? (
            <div className="mt-1 text-muted-foreground">
              request_id={code.schedulerTick.requestId}
            </div>
          ) : null}
        </div>
      ) : null}

      {code.schedulerTick.error ? (
        <ErrorState
          error={code.schedulerTick.error}
          onRetry={() => void code.schedulerTick.execute(undefined)}
        />
      ) : null}
    </section>
  );
}

function SchedulerMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded border bg-muted/30 px-1.5 py-1">
      <div className="text-muted-foreground">{label}</div>
      <div>{value}</div>
    </div>
  );
}

function readSchedulerRunField(
  item: Record<string, unknown>,
  field: string,
): string | null {
  const run = readRecord(item["run"]);
  const value = run?.[field] ?? item[field];
  if (typeof value === "number") return String(value);
  return typeof value === "string" ? value : null;
}

function readSchedulerDecisionField(
  item: Record<string, unknown>,
  field: string,
): string | null {
  const decision = readRecord(item["decision"]);
  const value = decision?.[field];
  return typeof value === "string" ? value : null;
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

interface RunEvidenceProps {
  run: TransformRunResult;
  onPreview: () => void;
  isPreviewLoading: boolean;
}

function RunEvidence({ run, onPreview, isPreviewLoading }: RunEvidenceProps) {
  const rows: Array<{ label: string; value: string; mono?: boolean }> = [
    { label: "출력 데이터셋", value: run.dataset_ref, mono: true },
    { label: "버전", value: `v${run.version_number}`, mono: true },
    { label: "version_id", value: run.version_id, mono: true },
    { label: "transaction_id", value: run.transaction_id, mono: true },
    { label: "row count", value: String(run.row_count), mono: true },
    { label: "schema_hash", value: run.schema_hash, mono: true },
  ];

  return (
    <section className="space-y-2 rounded border border-success/30 bg-success/5 p-2.5">
      <div className="flex items-center gap-1.5 text-[12px] font-semibold text-success">
        <CheckCircle2 className="size-4" />
        빌드 성공 · 출력 데이터셋 생성됨
      </div>
      <dl className="space-y-1">
        {rows.map((row) => (
          <div key={row.label} className="flex items-baseline gap-2">
            <dt className="w-24 shrink-0 text-[11px] text-muted-foreground">
              {row.label}
            </dt>
            <dd
              className={cn(
                "min-w-0 flex-1 truncate text-[11px] text-foreground",
                row.mono && "font-mono",
              )}
              title={row.value}
            >
              {row.value}
            </dd>
          </div>
        ))}
        <div className="flex items-baseline gap-2">
          <dt className="w-24 shrink-0 text-[11px] text-muted-foreground">
            manifest
          </dt>
          <dd
            className="min-w-0 flex-1 truncate font-mono text-[10px] text-muted-foreground"
            title={run.manifest_uri}
          >
            {run.manifest_uri}
          </dd>
        </div>
      </dl>
      <div className="flex items-center gap-1.5 pt-1">
        <Button
          size="sm"
          variant="outline"
          className="h-7 flex-1 text-[12px]"
          disabled={isPreviewLoading}
          onClick={onPreview}
        >
          {isPreviewLoading ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Table2 className="size-3.5" />
          )}
          출력 프리뷰
        </Button>
        <Button
          asChild
          size="sm"
          variant="outline"
          className="h-7 flex-1 text-[12px] text-primary"
        >
          <Link to={datasetPreviewHref(run.dataset_ref)}>
            <Database className="size-3.5" />
            Dataset Preview
            <ArrowUpRight className="size-3" />
          </Link>
        </Button>
      </div>
    </section>
  );
}

interface OutputPreviewTableProps {
  datasetRef: string;
  rows: Record<string, unknown>[];
}

function OutputPreviewTable({ datasetRef, rows }: OutputPreviewTableProps) {
  if (rows.length === 0) {
    return (
      <section className="rounded border bg-card p-3 text-[11px] text-muted-foreground">
        {datasetRef} 프리뷰에 표시할 행이 없습니다.
      </section>
    );
  }
  const columns = Object.keys(rows[0]);
  return (
    <section className="space-y-1">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground">
        <Table2 className="size-3.5" />
        출력 프리뷰
        <span className="font-mono text-[10px] font-normal">
          {datasetRef} · {rows.length}행
        </span>
      </div>
      <div className="overflow-auto rounded border bg-card">
        <table className="w-full border-collapse text-[11px]">
          <thead>
            <tr className="border-b">
              {columns.map((column) => (
                <th
                  key={column}
                  className="section-label h-7 px-2 text-left align-middle whitespace-nowrap"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="border-b last:border-b-0">
                {columns.map((column) => (
                  <td
                    key={column}
                    className="h-7 px-2 align-middle font-mono whitespace-nowrap"
                    title={formatCell(row[column])}
                  >
                    {formatCell(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
