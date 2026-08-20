import type { GenericObject } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import { ChartXY } from "../../components/ChartXY";
import { PieChartMini } from "../../components/MiniCharts";
import {
  formatCellValue,
  objectTitleOf,
  statusIntentOf,
} from "../../lib/app-model";
import {
  computeMetric,
  crossAggregate,
  formatMetricValue,
  groupAggregate,
  metricLabel,
} from "../../lib/aggregate";
import { useRuntimeDispatch, useRuntimeState } from "../../lib/runtime-state";

import {
  objectViewFor,
  useWidgetObjects,
  WidgetFrame,
  WidgetPlaceholder,
  type WidgetRuntimeProps,
} from "./widget-kit";

const MISSING_OBJECT = (
  <WidgetPlaceholder
    label="객체 타입 미지정"
    hint="인스펙터에서 객체 타입을 선택하세요."
  />
);

type ResolvedMetric = {
  label: string;
  value: number;
  unit?: string;
};

/** 값 색: 음수는 빨강(Palantir 조건부 서식). */
function metricValueClass(value: number): string {
  return value < 0 ? "text-[#cd4246]" : "text-[#1c2127]";
}

/**
 * Metric Card (Palantir 클론): 다중 지표를 카드/리스트/태그 레이아웃으로.
 * 음수는 빨강으로 조건부 서식. 단일 지표(metric/metricProperty)도 지원.
 */
export function MetricCardWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const { config } = widget;
  const objectApiName = config.objectApiName ?? null;
  const { objects, isTruncated } = useWidgetObjects(
    objectApiName,
    widget.config.variableFilters,
  );

  if (!objectApiName) return MISSING_OBJECT;

  const layout = config.metricLayout ?? "card";
  const specs =
    config.metrics && config.metrics.length > 0
      ? config.metrics
      : [
          {
            label: config.title || metricLabel(config.metric ?? "count"),
            metric: config.metric ?? "count",
            property: config.metricProperty,
            unit: config.unit,
          },
        ];
  const resolved: ResolvedMetric[] = specs.map((spec) => ({
    label: spec.label,
    value: computeMetric(objects, spec.metric, spec.property),
    unit: spec.unit,
  }));
  const sectionTitle = specs.length > 1
    ? `${config.title ?? ""}${isTruncated ? " (상한 표본)" : ""}`
    : null;

  if (layout === "list") {
    return (
      <div className="space-y-1.5">
        {sectionTitle ? <SectionTitle title={sectionTitle} /> : null}
        {resolved.map((item) => (
          <div key={item.label} className="flex items-baseline gap-2">
            <span className="text-[13px] text-[#5f6b7c]">{item.label}</span>
            <span
              className={cn(
                "text-[15px] font-bold tabular-nums",
                metricValueClass(item.value),
              )}
            >
              {formatMetricValue(item.value)}
              {item.unit ? (
                <span className="ml-0.5 text-[11px] text-[#8f99a8]">
                  {item.unit}
                </span>
              ) : null}
            </span>
          </div>
        ))}
      </div>
    );
  }

  if (layout === "tags") {
    return (
      <div className="space-y-1.5">
        {sectionTitle ? <SectionTitle title={sectionTitle} /> : null}
        <div className="flex flex-wrap gap-2">
          {resolved.map((item) => (
            <span
              key={item.label}
              className="rounded bg-[#eef1f4] px-2.5 py-1 text-[12px]"
            >
              <span className="text-[#5f6b7c]">{item.label}</span>{" "}
              <span
                className={cn(
                  "font-bold tabular-nums",
                  metricValueClass(item.value),
                )}
              >
                {formatMetricValue(item.value)}
                {item.unit ?? ""}
              </span>
            </span>
          ))}
        </div>
      </div>
    );
  }

  // card layout: 셀을 세로 구분선으로 나눈 가로 배치.
  return (
    <div className="space-y-1.5">
      {sectionTitle ? <SectionTitle title={sectionTitle} /> : null}
      <div className="flex min-w-[140px] divide-x divide-[#e4e9ed] overflow-hidden rounded-md border border-[#d5dce1] bg-white">
        {resolved.map((item) => (
          <div key={item.label} className="min-w-0 flex-1 p-3">
            <div className="truncate text-[11px] text-[#5f6b7c]">
              {item.label}
            </div>
            <div className="mt-0.5 flex items-baseline gap-1">
              <span
                className={cn(
                  "text-[26px] leading-none font-bold tabular-nums",
                  metricValueClass(item.value),
                )}
              >
                {formatMetricValue(item.value)}
              </span>
              {item.unit ? (
                <span className="text-[13px] font-medium text-[#8f99a8]">
                  {item.unit}
                </span>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SectionTitle({ title }: { title: string }) {
  return (
    <div className="text-[11px] font-semibold tracking-wide text-[#5f6b7c] uppercase">
      {title}
    </div>
  );
}

const CHART_TYPE_LABELS: Record<string, string> = {
  bar: "막대",
  horizontalBar: "가로막대",
  line: "라인",
  area: "영역",
  scatter: "산점도",
};

/** Chart XY: group-by(카테고리) × series(누적) 교차 집계를 축·범례와 함께 렌더. */
export function BarChartWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects, isTruncated } = useWidgetObjects(
    objectApiName,
    widget.config.variableFilters,
  );
  const objectView = objectViewFor(props, objectApiName);

  if (!objectApiName) return MISSING_OBJECT;

  const metric = widget.config.metric ?? "count";
  const chartType = widget.config.chartType ?? "bar";
  const groupBy = widget.config.groupByProperty;
  const seriesProperty = widget.config.seriesProperty ?? null;
  const title = widget.config.title || (groupBy ? `${groupBy} 분포` : "차트");

  if (!groupBy) {
    return (
      <WidgetFrame title={title}>
        <WidgetPlaceholder
          label="그룹 기준 속성 미지정"
          hint="인스펙터에서 group-by 속성을 선택하세요."
        />
      </WidgetFrame>
    );
  }

  const data = crossAggregate(
    objects,
    groupBy,
    seriesProperty,
    metric,
    widget.config.metricProperty,
  );
  const xLabel =
    objectView?.properties.find((p) => p.apiName === groupBy)?.displayName ??
    groupBy;
  const yLabel = metricLabel(metric);

  return (
    <WidgetFrame
      title={title}
      subtitle={`${CHART_TYPE_LABELS[chartType]} · ${yLabel}${isTruncated ? " · 상한 표본" : ""}`}
      bodyClassName="p-3"
    >
      <ChartXY
        data={data}
        chartType={chartType}
        xLabel={xLabel}
        yLabel={yLabel}
      />
    </WidgetFrame>
  );
}

/** group-by 속성별 비중 파이 차트. 항상 개수 기준. */
export function PieChartWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects, isTruncated } = useWidgetObjects(
    objectApiName,
    widget.config.variableFilters,
  );

  if (!objectApiName) return MISSING_OBJECT;

  const groupBy = widget.config.groupByProperty;
  const title = widget.config.title || (groupBy ? `${groupBy} 비중` : "비중");

  if (!groupBy) {
    return (
      <WidgetFrame title={title}>
        <WidgetPlaceholder
          label="그룹 기준 속성 미지정"
          hint="인스펙터에서 group-by 속성을 선택하세요."
        />
      </WidgetFrame>
    );
  }

  const buckets = groupAggregate(objects, groupBy, "count");

  return (
    <WidgetFrame
      title={title}
      subtitle={`${metricLabel("count")}${isTruncated ? " · 상한 표본" : ""}`}
      bodyClassName="p-3"
    >
      <PieChartMini buckets={buckets} />
    </WidgetFrame>
  );
}

const TIMELINE_LIMIT = 30;

function compareForTimeline(a: unknown, b: unknown): number {
  const aMissing = a === undefined || a === null;
  const bMissing = b === undefined || b === null;
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;
  if (typeof a === "number" && typeof b === "number") return b - a;
  return String(b).localeCompare(String(a));
}

/** 날짜 속성 기준 내림차순 타임라인. 항목 클릭으로 객체 선택. */
export function TimelineWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects, isTruncated } = useWidgetObjects(
    objectApiName,
    widget.config.variableFilters,
  );
  const objectView = objectViewFor(props, objectApiName);
  const dispatch = useRuntimeDispatch();
  const state = useRuntimeState();

  if (!objectApiName) return MISSING_OBJECT;

  const dateProp = widget.config.dateProperty;
  const title = widget.config.title || "타임라인";

  if (!dateProp) {
    return (
      <WidgetFrame title={title}>
        <WidgetPlaceholder
          label="날짜 속성 미지정"
          hint="인스펙터에서 날짜 속성을 선택하세요."
        />
      </WidgetFrame>
    );
  }

  const sorted = [...objects]
    .sort((a, b) =>
      compareForTimeline(a.properties[dateProp], b.properties[dateProp]),
    )
    .slice(0, TIMELINE_LIMIT);

  if (sorted.length === 0) {
    return (
      <WidgetFrame title={title}>
        <WidgetPlaceholder label="표시할 항목이 없습니다." />
      </WidgetFrame>
    );
  }

  return (
    <WidgetFrame
      title={title}
      subtitle={isTruncated ? "상한 표본" : undefined}
      bodyClassName="overflow-auto p-3"
    >
      <ol className="relative ml-1 border-l border-[#d5dce1]">
        {sorted.map((object) => (
          <TimelineItem
            key={object.objectId}
            object={object}
            objectView={objectView}
            dateProp={dateProp}
            isSelected={state.selectedObjectId === object.objectId}
            onSelect={() =>
              dispatch({ type: "selectObject", objectId: object.objectId })
            }
          />
        ))}
      </ol>
    </WidgetFrame>
  );
}

function TimelineItem({
  object,
  objectView,
  dateProp,
  isSelected,
  onSelect,
}: {
  object: GenericObject;
  objectView: FoundryLiteOntologyObjectView | null;
  dateProp: string;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const status = object.properties.status;
  const hasStatus = status !== undefined && status !== null;

  return (
    <li className="relative pl-4">
      <span
        className={cn(
          "absolute top-2 -left-[5px] size-2 rounded-full border border-white",
          isSelected ? "bg-[#2d72d2]" : "bg-[#8f99a8]",
        )}
      />
      <button
        type="button"
        onClick={onSelect}
        className={cn(
          "flex w-full cursor-pointer flex-col gap-0.5 rounded-md px-2 py-1.5 text-left hover:bg-[#f6f8fa]",
          isSelected && "bg-[#e8f0fb] hover:bg-[#e8f0fb]",
        )}
      >
        <span className="font-mono text-[11px] text-[#8f99a8]">
          {formatCellValue(object.properties[dateProp])}
        </span>
        <span className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-[#1c2127]">
            {objectTitleOf(object, objectView)}
          </span>
          {hasStatus ? (
            <StatusPill intent={statusIntentOf(status)}>
              {String(status)}
            </StatusPill>
          ) : null}
        </span>
      </button>
    </li>
  );
}
