import type {
  ObjectAggregateRequest,
  ObjectAggregationGroup,
} from "@foundry-lite/sdk";
import { useFoundryLiteProvidedObjectAggregate } from "@foundry-lite/sdk/react";
import { LineChart, ServerCog } from "lucide-react";
import { useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import {
  OntologyRequiredState,
  isActiveOntologyMissingError,
} from "@/components/shared/OntologyRequiredState";
import { StatusPill } from "@/components/shared/StatusPill";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { PathBoard } from "./PathBoard";
import { formatMetricValue } from "./analytics-model";

interface AggregateDimension {
  property: string;
  label: string;
}

interface AggregateMetricConfig {
  id: string;
  label: string;
  request: ObjectAggregateRequest["select"][number];
}

interface ObjectSourceConfig {
  apiName: string;
  label: string;
  dimensions: AggregateDimension[];
  metrics: AggregateMetricConfig[];
}

/** 백엔드에 실재하는 Order/Customer 오브젝트 property만 노출한다 (fabricate 금지). */
const OBJECT_SOURCES: ObjectSourceConfig[] = [
  {
    apiName: "Order",
    label: "Order",
    dimensions: [{ property: "status", label: "status" }],
    metrics: [
      {
        id: "count",
        label: "행 수 (count)",
        request: { function: "count", name: "count" },
      },
      {
        id: "amountSum",
        label: "amount 합계 (sum)",
        request: { function: "sum", property: "amount", name: "amountSum" },
      },
      {
        id: "avgRisk",
        label: "riskScore 평균 (avg)",
        request: { function: "avg", property: "riskScore", name: "avgRisk" },
      },
    ],
  },
  {
    apiName: "Customer",
    label: "Customer",
    dimensions: [
      { property: "region", label: "region" },
      { property: "segment", label: "segment" },
    ],
    metrics: [
      {
        id: "count",
        label: "행 수 (count)",
        request: { function: "count", name: "count" },
      },
      {
        id: "avgRisk",
        label: "riskScore 평균 (avg)",
        request: { function: "avg", property: "riskScore", name: "avgRisk" },
      },
    ],
  },
];

const BAR_COLOR = "#54b85d";

/**
 * Quiver식 카테고리 차트 카드: 서버 사이드 objects.generic.aggregate.
 * datasource(Order/Customer) → groupBy → metric 선택 → 세로 막대 차트.
 * 클라이언트 집계(DISTRIBUTION board)와 달리 브라우저로 raw 오브젝트를 내리지 않고
 * 서버에서 집계한다 — request evidence(totalGroups)를 노출한다.
 */
export function ObjectAggregateCard() {
  const [sourceApiName, setSourceApiName] = useState(OBJECT_SOURCES[0].apiName);
  const source =
    OBJECT_SOURCES.find((item) => item.apiName === sourceApiName) ??
    OBJECT_SOURCES[0];
  const [groupBy, setGroupBy] = useState(source.dimensions[0].property);
  const [metricId, setMetricId] = useState(source.metrics[0].id);

  const activeGroupBy = source.dimensions.some(
    (dimension) => dimension.property === groupBy,
  )
    ? groupBy
    : source.dimensions[0].property;
  const activeMetric =
    source.metrics.find((metric) => metric.id === metricId) ??
    source.metrics[0];

  const request = useMemo<ObjectAggregateRequest>(
    () => ({
      select: [activeMetric.request],
      groupBy: [activeGroupBy],
    }),
    [activeMetric, activeGroupBy],
  );

  const aggregate = useFoundryLiteProvidedObjectAggregate(
    source.apiName,
    request,
  );

  const handleSourceChange = (apiName: string) => {
    const next =
      OBJECT_SOURCES.find((item) => item.apiName === apiName) ??
      OBJECT_SOURCES[0];
    setSourceApiName(apiName);
    setGroupBy(next.dimensions[0].property);
    setMetricId(next.metrics[0].id);
  };

  const bars = buildBars(
    aggregate.groups,
    activeGroupBy,
    activeMetric.request.name ?? activeMetric.id,
  );
  const maxValue = bars.reduce((max, bar) => Math.max(max, bar.value), 0);

  return (
    <PathBoard
      icon={LineChart}
      label="QUIVER · 오브젝트 집계 차트"
      actions={
        <StatusPill intent="info">
          <ServerCog className="size-3" />
          서버 집계
        </StatusPill>
      }
    >
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 border-b bg-muted/20 px-2.5 py-1.5 text-[12px]">
        <Select value={source.apiName} onValueChange={handleSourceChange}>
          <SelectTrigger size="sm" className="h-7 w-28 text-[12px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {OBJECT_SOURCES.map((item) => (
              <SelectItem
                key={item.apiName}
                value={item.apiName}
                className="text-[12px]"
              >
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={activeMetric.id} onValueChange={setMetricId}>
          <SelectTrigger size="sm" className="h-7 w-44 text-[12px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {source.metrics.map((metric) => (
              <SelectItem
                key={metric.id}
                value={metric.id}
                className="text-[12px]"
              >
                {metric.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-muted-foreground">by</span>
        <Select value={activeGroupBy} onValueChange={setGroupBy}>
          <SelectTrigger size="sm" className="h-7 w-28 text-[12px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {source.dimensions.map((dimension) => (
              <SelectItem
                key={dimension.property}
                value={dimension.property}
                className="text-[12px]"
              >
                {dimension.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="p-3">
        {aggregate.isLoading ? (
          <LoadingState rowCount={4} />
        ) : aggregate.error && isActiveOntologyMissingError(aggregate.error) ? (
          <OntologyRequiredState className="border-0 p-4" />
        ) : aggregate.error ? (
          <ErrorState error={aggregate.error} onRetry={aggregate.reload} />
        ) : bars.length === 0 ? (
          <p className="py-6 text-center text-[12px] text-muted-foreground">
            집계 결과가 없습니다.
          </p>
        ) : (
          <VerticalBarChart
            bars={bars}
            maxValue={maxValue}
            metricLabel={activeMetric.label}
            groupBy={activeGroupBy}
          />
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-3 py-1.5 font-mono text-[11px] text-muted-foreground">
        <span>POST /api/objects/{source.apiName}/aggregate</span>
        <span>totalGroups={aggregate.totalGroups}</span>
      </div>
    </PathBoard>
  );
}

interface ChartBar {
  key: string;
  value: number;
}

function buildBars(
  groups: readonly ObjectAggregationGroup[],
  groupBy: string,
  metricName: string,
): ChartBar[] {
  return groups
    .map((group) => ({
      key: String(group.key[groupBy] ?? "(null)"),
      value: group.metrics[metricName] ?? 0,
    }))
    .sort((a, b) => b.value - a.value);
}

interface VerticalBarChartProps {
  bars: readonly ChartBar[];
  maxValue: number;
  metricLabel: string;
  groupBy: string;
}

/** 세로 막대 차트 (Quiver 카테고리 chart 문법): y축 metric, x축 groupBy 카테고리. */
function VerticalBarChart({
  bars,
  maxValue,
  metricLabel,
  groupBy,
}: VerticalBarChartProps) {
  return (
    <div>
      <div className="mb-2 text-[11px] text-muted-foreground">
        <span className="font-medium text-primary">{metricLabel}</span>
        {" grouped by "}
        <span className="font-mono font-medium text-primary">{groupBy}</span>
      </div>
      <div className="flex h-40 items-end justify-around gap-3 border-b border-l pt-2 pl-1">
        {bars.map((bar) => {
          const heightPercent =
            maxValue > 0 ? Math.max(3, (bar.value / maxValue) * 100) : 0;
          return (
            <div
              key={bar.key}
              className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1"
            >
              <span className="font-mono text-[11px] tabular-nums text-foreground/80">
                {formatMetricValue(bar.value)}
              </span>
              <div
                className="w-full max-w-16 rounded-t-[2px] ring-1 ring-inset ring-black/5"
                style={{
                  height: `${heightPercent}%`,
                  backgroundColor: BAR_COLOR,
                }}
              />
            </div>
          );
        })}
      </div>
      <div className="flex justify-around gap-3 pt-1 pl-1">
        {bars.map((bar) => (
          <span
            key={bar.key}
            className="min-w-0 flex-1 truncate text-center text-[11px] text-muted-foreground"
            title={bar.key}
          >
            {bar.key}
          </span>
        ))}
      </div>
    </div>
  );
}
