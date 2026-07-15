import type {
  SourceManagedSync,
  SourceManagedSyncRun,
  SourceSchedulerDecision,
} from "@foundry-lite/sdk";
import { Activity, CalendarClock, Database, Rows3, Timer } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";

import {
  formatTimestamp,
  readSyncRunRowCount,
  statusIntent,
  statusLabel,
} from "../source-model";
import { readSchedule, scheduleSummary } from "./sync-config";

interface SyncOperationalSummaryProps {
  sync: SourceManagedSync;
  decision: SourceSchedulerDecision | null;
  latestRun: SourceManagedSyncRun | null;
  isScheduleLoading: boolean;
}

/** 선택한 sync의 현재 운영 상태를 한 줄에서 판독하는 밀도 높은 요약 rail. */
export function SyncOperationalSummary({
  sync,
  decision,
  latestRun,
  isScheduleLoading,
}: SyncOperationalSummaryProps) {
  const rowCount = readSyncRunRowCount(latestRun?.resultSummary);
  const schedule = readSchedule(sync);
  const metrics = [
    {
      label: "스케줄",
      icon: CalendarClock,
      value: (
        <StatusPill
          intent={
            sync.status === "paused"
              ? "warning"
              : decision?.enabled
                ? "success"
                : "neutral"
          }
        >
          {sync.status === "paused"
            ? `${decision?.autoPaused ? "자동 보호" : "일시정지"} · ${scheduleSummary(schedule)}`
            : scheduleSummary(schedule)}
        </StatusPill>
      ),
    },
    {
      label: "다음 실행",
      icon: Timer,
      value: isScheduleLoading
        ? "평가 중…"
        : sync.status === "paused"
          ? decision?.autoPaused
            ? "복구 빌드 필요"
            : "일시정지"
        : decision?.due
          ? "실행 대기"
        : decision?.nextDueAt
          ? formatTimestamp(decision.nextDueAt)
          : schedule.mode === "disabled"
            ? "중지됨"
            : "수동 실행",
    },
    {
      label: "마지막 빌드",
      icon: Activity,
      value: latestRun ? (
        <span className="flex items-center gap-1.5">
          <StatusPill intent={statusIntent(latestRun.status)}>
            {statusLabel(latestRun.status)}
          </StatusPill>
          <span className="font-mono text-[10px] text-muted-foreground">
            {formatTimestamp(latestRun.completedAt ?? latestRun.startedAt)}
          </span>
        </span>
      ) : (
        "실행 전"
      ),
    },
    {
      label: "마지막 행",
      icon: Rows3,
      value: rowCount === null ? "—" : rowCount.toLocaleString("ko-KR"),
    },
    {
      label: "데이터셋 버전",
      icon: Database,
      value: latestRun?.datasetVersionId ?? "—",
    },
  ] as const;

  return (
    <section
      aria-label="동기화 운영 요약"
      data-testid="sync-operational-summary"
      className="grid overflow-hidden rounded border bg-card sm:grid-cols-2 xl:grid-cols-5"
    >
      {metrics.map(({ label, icon: Icon, value }) => (
        <div
          key={label}
          className="min-w-0 border-b px-3 py-2.5 last:border-b-0 sm:border-r xl:border-b-0 xl:last:border-r-0"
        >
          <div className="mb-1 flex items-center gap-1 text-[10px] tracking-[0.5px] text-muted-foreground uppercase">
            <Icon className="size-3" />
            {label}
          </div>
          <div className="min-w-0 truncate font-mono text-[11px] font-medium">
            {value}
          </div>
        </div>
      ))}
    </section>
  );
}
