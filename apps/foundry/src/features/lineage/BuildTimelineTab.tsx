import type { Dataset } from "@foundry-lite/sdk";
import type {
  FoundryLiteOperationsRunListState,
  FoundryLiteOperationsRunRow,
} from "@foundry-lite/sdk/react";
import { ChartNoAxesGantt } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  NODE_COLORWAYS,
  readRunTargetLabel,
  readRunTimestamp,
} from "@/features/lineage/lineage-model";
import type { SelectedRunRef } from "@/features/lineage/lineage-selection";

interface BuildTimelineTabProps {
  runList: FoundryLiteOperationsRunListState;
  datasets: Dataset[];
  onSelectRun: (run: SelectedRunRef) => void;
}

interface TimelineMarker {
  run: FoundryLiteOperationsRunRow;
  timestamp: number;
}

const PERIOD_OPTIONS = [
  { value: "7", label: "지난 7일" },
  { value: "30", label: "지난 30일" },
  { value: "365", label: "지난 1년" },
] as const;

function markerColor(run: FoundryLiteOperationsRunRow): string {
  if (run.isFailure) return NODE_COLORWAYS.failedRed.background;
  if (run.isRunning || run.isPending)
    return NODE_COLORWAYS.runningBlue.background;
  if (run.isSucceeded) return NODE_COLORWAYS.syncGreen.background;
  return NODE_COLORWAYS.neutralGray.border;
}

function runTimestampMs(run: FoundryLiteOperationsRunRow): number | null {
  const raw =
    readRunTimestamp(run, "completed_at") ??
    readRunTimestamp(run, "created_at");
  if (!raw) return null;
  const parsed = new Date(raw).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

/** 하단 빌드 타임라인 탭: 대상 리소스별 행 + 시간축 마커 (build-timeline.png 재현). */
export function BuildTimelineTab({
  runList,
  datasets,
  onSelectRun,
}: BuildTimelineTabProps) {
  const [periodDays, setPeriodDays] = useState<string>("30");

  const datasetById = useMemo(
    () => new Map(datasets.map((dataset) => [dataset.id, dataset])),
    [datasets],
  );

  const { rows, rangeStart, rangeEnd } = useMemo(() => {
    const now = Date.now();
    const start = now - Number(periodDays) * 24 * 60 * 60 * 1000;
    const markersByTarget = new Map<string, TimelineMarker[]>();

    for (const run of runList.runRows) {
      if (
        !["sync", "transform", "index", "materialization"].includes(
          String(run.runType),
        )
      ) {
        continue;
      }
      const timestamp = runTimestampMs(run);
      if (timestamp === null || timestamp < start) continue;
      const target = readRunTargetLabel(run, datasetById);
      markersByTarget.set(target, [
        ...(markersByTarget.get(target) ?? []),
        { run, timestamp },
      ]);
    }
    return {
      rows: [...markersByTarget.entries()].sort(([a], [b]) =>
        a.localeCompare(b),
      ),
      rangeStart: start,
      rangeEnd: now,
    };
  }, [runList.runRows, datasetById, periodDays]);

  if (runList.isLoading) return <LoadingState rowCount={5} />;
  if (runList.error) {
    return (
      <ErrorState error={runList.error} onRetry={() => void runList.reload()} />
    );
  }

  const range = Math.max(rangeEnd - rangeStart, 1);
  const tickCount = 5;
  const ticks = Array.from({ length: tickCount }, (_, index) => {
    const timestamp = rangeStart + (range * index) / (tickCount - 1);
    return new Date(timestamp).toLocaleDateString("ko-KR", {
      month: "short",
      day: "numeric",
    });
  });

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-muted-foreground">기간</span>
        <Select value={periodDays} onValueChange={setPeriodDays}>
          <SelectTrigger size="sm" className="h-7 w-28 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PERIOD_OPTIONS.map((option) => (
              <SelectItem
                key={option.value}
                value={option.value}
                className="text-xs"
              >
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-[11px] text-muted-foreground">
          색상 기준: 상태
        </span>
        <span className="ml-auto flex items-center gap-3 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <span
              className="size-2.5 rounded-[2px]"
              style={{ background: NODE_COLORWAYS.syncGreen.background }}
            />
            성공
          </span>
          <span className="flex items-center gap-1">
            <span
              className="size-2.5 rounded-[2px]"
              style={{ background: NODE_COLORWAYS.failedRed.background }}
            />
            실패
          </span>
          <span className="flex items-center gap-1">
            <span
              className="size-2.5 rounded-[2px]"
              style={{ background: NODE_COLORWAYS.runningBlue.background }}
            />
            실행 중
          </span>
        </span>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          icon={ChartNoAxesGantt}
          title="이 기간의 빌드가 없습니다"
          description="기간을 늘리거나 파이프라인에서 빌드를 실행하세요."
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto rounded border bg-card">
          {rows.map(([target, markers]) => (
            <div
              key={target}
              className="flex h-7 items-center border-b last:border-b-0"
            >
              <div className="w-44 shrink-0 truncate border-r px-2 font-mono text-[10px]">
                {target}
              </div>
              <div className="relative h-full flex-1">
                {markers.map((marker) => (
                  <button
                    key={`${marker.run.runType}-${marker.run.runId}`}
                    type="button"
                    title={`${marker.run.runId ?? ""} (${marker.run.status ?? "?"})`}
                    onClick={() =>
                      marker.run.runId
                        ? onSelectRun({
                            runType: String(marker.run.runType),
                            runId: marker.run.runId,
                          })
                        : undefined
                    }
                    className="absolute top-1/2 h-4 w-1.5 -translate-y-1/2 rounded-[1px] hover:scale-125"
                    style={{
                      left: `${((marker.timestamp - rangeStart) / range) * 100}%`,
                      background: markerColor(marker.run),
                    }}
                  />
                ))}
              </div>
            </div>
          ))}
          <div className="flex h-6 items-center">
            <div className="w-44 shrink-0 border-r" />
            <div className="flex flex-1 justify-between px-1 text-[10px] text-muted-foreground">
              {ticks.map((tick, index) => (
                <span key={index}>{tick}</span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
