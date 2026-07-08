import type {
  GenericObject,
  ObjectAggregateRequest,
  ObjectFilter,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteProvidedObjectAggregate,
  type FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
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

import { linkTypesOf } from "../hooks/use-object-links";
import {
  formatPropertyValue,
  groupableProperties,
  numericProperties,
} from "../lib/explorer-model";
import { DistributionRows } from "./DistributionRows";
import { LinkDistributionCard } from "./LinkDistributionCard";
import { StackedBarCard } from "./StackedBarCard";

type MetricChoice = string; // "count" | `${"sum"|"avg"}:${property}`

const METRIC_COUNT: MetricChoice = "count";

const INLINE_SELECT_TRIGGER =
  "h-6 gap-1 rounded border-none bg-transparent px-1 text-[13px] font-semibold text-[#215db0] shadow-none focus-visible:ring-0 [&_svg]:!text-[#215db0]";

function metricRequest(metric: MetricChoice): ObjectAggregateRequest["select"] {
  if (metric === METRIC_COUNT) return [{ function: "count" }];
  const [fn, property] = metric.split(":");
  return [
    { function: "count" },
    {
      function: fn as "sum" | "avg",
      property,
      name: "metric",
    },
  ];
}

function metricValueOf(
  metrics: Record<string, number | null>,
  metric: MetricChoice,
): number {
  if (metric === METRIC_COUNT) return metrics.count ?? 0;
  return metrics.metric ?? 0;
}

interface AggregateCardProps {
  objectView: FoundryLiteOntologyObjectView;
  where: ObjectFilter | undefined;
  defaultGroupBy: string;
  dataVersion: number;
}

/** 분포/집계 카드: 헤더(속성명 볼드) + "측정/그룹 기준" 컨트롤 + 값·카운트·파란 막대 행. */
function AggregateCard({
  objectView,
  where,
  defaultGroupBy,
  dataVersion,
}: AggregateCardProps) {
  const [groupBy, setGroupBy] = useState(defaultGroupBy);
  const [metric, setMetric] = useState<MetricChoice>(METRIC_COUNT);
  const groupCandidates = groupableProperties(objectView);
  const numericCandidates = numericProperties(objectView);

  const request = useMemo<ObjectAggregateRequest>(
    () => ({
      select: metricRequest(metric),
      groupBy: [groupBy],
      filter: where ?? null,
    }),
    [groupBy, metric, where],
  );
  const aggregate = useFoundryLiteProvidedObjectAggregate(
    objectView.apiName,
    request,
    {
      key: [
        "objects",
        "aggregate",
        objectView.apiName,
        JSON.stringify(request),
        dataVersion,
      ],
    },
  );

  const rows = useMemo(
    () =>
      [...aggregate.groups]
        .map((group) => {
          const value = metricValueOf(group.metrics, metric);
          const count = group.metrics.count ?? 0;
          return {
            label: formatPropertyValue(group.key[groupBy]),
            value,
            display:
              metric === METRIC_COUNT
                ? formatPropertyValue(value)
                : `${formatPropertyValue(value)} (${count}건)`,
          };
        })
        .sort((left, right) => right.value - left.value),
    [aggregate.groups, groupBy, metric],
  );

  const groupByDisplayName =
    objectView.properties.find((property) => property.apiName === groupBy)
      ?.displayName ?? groupBy;

  return (
    <div className="rounded border border-[#dde3e9] bg-white shadow-[0_1px_2px_rgba(17,20,24,0.04)]">
      <div className="flex h-12 items-center justify-between border-b border-[#e4e9ed] px-4">
        <span className="text-[14px] font-bold text-[#1c2127]">
          {groupByDisplayName}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">
          그룹 {aggregate.totalGroups}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-b border-[#e4e9ed] bg-[#f6f8fa] px-4 py-2 text-[13px] text-[#1c2127]">
        <span>측정</span>
        <Select value={metric} onValueChange={setMetric}>
          <SelectTrigger size="sm" className={INLINE_SELECT_TRIGGER}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={METRIC_COUNT}>객체 수</SelectItem>
            {numericCandidates.flatMap((property) => [
              <SelectItem
                key={`sum:${property.apiName}`}
                value={`sum:${property.apiName}`}
              >
                합계 · {property.displayName}
              </SelectItem>,
              <SelectItem
                key={`avg:${property.apiName}`}
                value={`avg:${property.apiName}`}
              >
                평균 · {property.displayName}
              </SelectItem>,
            ])}
          </SelectContent>
        </Select>
        <span>그룹 기준</span>
        <Select value={groupBy} onValueChange={setGroupBy}>
          <SelectTrigger size="sm" className={INLINE_SELECT_TRIGGER}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {groupCandidates.map((property) => (
              <SelectItem key={property.apiName} value={property.apiName}>
                {property.displayName}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {aggregate.isLoading ? (
        <LoadingState rowCount={4} className="p-4" />
      ) : aggregate.error ? (
        <ErrorState
          error={aggregate.error}
          onRetry={aggregate.reload}
          className="m-4"
        />
      ) : rows.length === 0 ? (
        <EmptyState
          title="집계할 객체가 없습니다"
          description="필터 조건을 줄이거나 다른 그룹 기준을 선택해 보세요."
          className="p-6"
        />
      ) : (
        <DistributionRows rows={rows} />
      )}
      <div className="flex items-center justify-between border-t border-[#eef1f4] px-4 py-1.5 font-mono text-[10px] text-muted-foreground">
        <span>objects.generic.aggregate · 결과 목록과 분리 실행</span>
        {aggregate.requestId ? <span>req={aggregate.requestId}</span> : null}
      </div>
    </div>
  );
}

interface AggregatePanelProps {
  objectView: FoundryLiteOntologyObjectView;
  objectViewsByApiName: Record<string, FoundryLiteOntologyObjectView>;
  objects: GenericObject[];
  pageSize: number;
  where: ObjectFilter | undefined;
  dataVersion: number;
}

/** Explore 캔버스: 스택 바 차트 + 링크 순회 분포 + 분포 카드 (필터 결과와 분리 표시). */
export function AggregatePanel({
  objectView,
  objectViewsByApiName,
  objects,
  pageSize,
  where,
  dataVersion,
}: AggregatePanelProps) {
  const groupCandidates = groupableProperties(objectView);
  if (groupCandidates.length === 0) {
    return (
      <EmptyState
        title="집계 가능한 속성이 없습니다"
        description="이 객체 타입에는 그룹 기준으로 쓸 문자열 속성이 없습니다."
      />
    );
  }

  const firstLink = linkTypesOf(objectView)[0] ?? null;
  const linkTargetType = firstLink
    ? firstLink.fromObjectType === objectView.apiName
      ? firstLink.toObjectType
      : firstLink.fromObjectType
    : null;
  const linkTargetView = linkTargetType
    ? (objectViewsByApiName[linkTargetType] ?? null)
    : null;
  const distGroupBy = groupCandidates[1]?.apiName ?? groupCandidates[0].apiName;

  return (
    <div className="space-y-4">
      <StackedBarCard
        key={`stack:${objectView.apiName}`}
        objectView={objectView}
        objects={objects}
        pageSize={pageSize}
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {firstLink && linkTargetView ? (
          <LinkDistributionCard
            key={`link:${objectView.apiName}:${linkTargetView.apiName}`}
            targetView={linkTargetView}
            linkApiName={firstLink.apiName}
            dataVersion={dataVersion}
          />
        ) : null}
        <AggregateCard
          key={`dist:${objectView.apiName}`}
          objectView={objectView}
          where={where}
          defaultGroupBy={distGroupBy}
          dataVersion={dataVersion}
        />
      </div>
    </div>
  );
}
