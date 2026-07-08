import { BarChart3 } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import { PathBoard } from "./PathBoard";
import type {
  AggregateFunction,
  AggregateResult,
  PreviewColumn,
} from "./analytics-model";
import { AGGREGATE_FUNCTIONS, formatMetricValue } from "./analytics-model";

interface AggregateBoardProps {
  columns: readonly PreviewColumn[];
  groupBy: string;
  aggregateFn: AggregateFunction;
  metricColumn: string;
  result: AggregateResult;
  serverEvidence: ServerAggregateEvidence;
  onGroupByChange: (column: string) => void;
  onFunctionChange: (fn: AggregateFunction) => void;
  onMetricColumnChange: (column: string) => void;
}

const MAX_BARS = 12;

interface ServerAggregateEvidence {
  endpoint: string;
  status: "idle" | "loading" | "ready" | "error";
  rowCount: number;
  filteredRowCount: number;
  requestId: string | null;
  errorMessage: string | null;
}

/**
 * DISTRIBUTION board: groupBy 컬럼별 count/sum/avg를 가로 막대로 표시.
 * "[fn] over [groupBy]" 서술 라벨 + periwinkle 막대 (Contour distribution 문법).
 * 재사용 출처: Contour board-descriptions-distribution 히스토그램 board.
 */
export function AggregateBoard({
  columns,
  groupBy,
  aggregateFn,
  metricColumn,
  result,
  serverEvidence,
  onGroupByChange,
  onFunctionChange,
  onMetricColumnChange,
}: AggregateBoardProps) {
  const groupableColumns = columns.filter(
    (column) => !column.isSystem && !column.isNumeric,
  );
  const numericColumns = columns.filter(
    (column) => !column.isSystem && column.isNumeric,
  );
  const needsMetric = aggregateFn !== "count";
  const bars = result.bars.slice(0, MAX_BARS);
  const fnLabel =
    AGGREGATE_FUNCTIONS.find((fn) => fn.id === aggregateFn)?.label ?? "count";

  return (
    <PathBoard
      icon={BarChart3}
      label="DISTRIBUTION · 집계 차트"
      isActive
      actions={
        <div className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span>{result.bars.length}개 그룹</span>
          <span>datasets.aggregate</span>
        </div>
      }
    >
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 border-b bg-muted/20 px-2.5 py-1.5 text-[12px]">
        <Select
          value={aggregateFn}
          onValueChange={(value) =>
            onFunctionChange(value as AggregateFunction)
          }
        >
          <SelectTrigger size="sm" className="h-7 w-40 text-[12px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {AGGREGATE_FUNCTIONS.map((fn) => (
              <SelectItem key={fn.id} value={fn.id} className="text-[12px]">
                {fn.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {needsMetric ? (
          <>
            <span className="text-muted-foreground">of</span>
            <Select value={metricColumn} onValueChange={onMetricColumnChange}>
              <SelectTrigger size="sm" className="h-7 w-32 text-[12px]">
                <SelectValue placeholder="숫자 컬럼" />
              </SelectTrigger>
              <SelectContent>
                {numericColumns.map((column) => (
                  <SelectItem
                    key={column.name}
                    value={column.name}
                    className="text-[12px]"
                  >
                    {column.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        ) : null}
        <span className="text-muted-foreground">grouped by</span>
        <Select value={groupBy} onValueChange={onGroupByChange}>
          <SelectTrigger size="sm" className="h-7 w-32 text-[12px]">
            <SelectValue placeholder="컬럼" />
          </SelectTrigger>
          <SelectContent>
            {groupableColumns.map((column) => (
              <SelectItem
                key={column.name}
                value={column.name}
                className="text-[12px]"
              >
                {column.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="p-2.5">
        {needsMetric && !metricColumn ? (
          <p className="py-4 text-center text-[12px] text-muted-foreground">
            집계할 숫자 컬럼을 선택하세요.
          </p>
        ) : bars.length === 0 ? (
          <p className="py-4 text-center text-[12px] text-muted-foreground">
            집계할 데이터가 없습니다.
          </p>
        ) : (
          <div className="space-y-1">
            <div className="mb-1.5 text-[11px] text-muted-foreground">
              <span className="font-medium text-primary">{fnLabel}</span>
              {needsMetric ? (
                <>
                  {" of "}
                  <span className="font-mono font-medium text-primary">
                    {metricColumn}
                  </span>
                </>
              ) : null}
              {" grouped by "}
              <span className="font-mono font-medium text-primary">
                {groupBy}
              </span>
            </div>
            {bars.map((bar) => (
              <BarRow
                key={bar.key}
                label={bar.key}
                value={bar.value}
                maxValue={result.maxValue}
              />
            ))}
            {result.bars.length > MAX_BARS ? (
              <div className="pt-1 text-[11px] text-muted-foreground">
                상위 {MAX_BARS}개 그룹 표시 (전체 {result.bars.length}개)
              </div>
            ) : null}
          </div>
        )}
        <ServerAggregateEvidenceRow evidence={serverEvidence} />
      </div>
    </PathBoard>
  );
}

function ServerAggregateEvidenceRow({
  evidence,
}: {
  evidence: ServerAggregateEvidence;
}) {
  const statusText =
    evidence.status === "ready"
      ? `${evidence.filteredRowCount}/${evidence.rowCount} rows`
      : evidence.status === "loading"
        ? "loading"
        : evidence.status === "error"
          ? evidence.errorMessage
          : "waiting";
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 border-t pt-2 font-mono text-[11px] text-muted-foreground">
      <span>{evidence.endpoint}</span>
      <span>{statusText}</span>
      {evidence.requestId ? <span>req={evidence.requestId}</span> : null}
    </div>
  );
}

interface BarRowProps {
  label: string;
  value: number;
  maxValue: number;
}

/** 가로 막대 1행: 라벨(좌) + periwinkle 막대(폭=값/max) + 값(우, mono). */
function BarRow({ label, value, maxValue }: BarRowProps) {
  const widthPercent = maxValue > 0 ? Math.max(2, (value / maxValue) * 100) : 0;
  return (
    <div className="flex items-center gap-2 text-[12px]">
      <span
        className="w-24 shrink-0 truncate text-right text-muted-foreground"
        title={label}
      >
        {label}
      </span>
      <div className="relative h-4 flex-1 overflow-hidden rounded-[2px] bg-muted/50">
        <div
          className={cn(
            "absolute inset-y-0 left-0 rounded-[2px]",
            "bg-[#adc4ef] ring-1 ring-inset ring-[#2d72d2]/40",
          )}
          style={{ width: `${widthPercent}%` }}
        />
      </div>
      <span className="w-16 shrink-0 text-right font-mono text-[11px] tabular-nums text-foreground/80">
        {formatMetricValue(value)}
      </span>
    </div>
  );
}
