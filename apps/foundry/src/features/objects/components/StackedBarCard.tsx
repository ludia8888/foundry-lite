import type { GenericObject } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import {
  formatPropertyValue,
  groupableProperties,
  numericProperties,
  STACK_CHART_COLORS,
} from "../lib/explorer-model";

const NO_STACK = "__none__";
const OTHER_STACK_KEY = "기타";

type MetricFn = "count" | "sum" | "avg";

type StackSegment = {
  stackKey: string;
  value: number;
};

type StackCategory = {
  key: string;
  total: number;
  segments: StackSegment[];
};

const INLINE_SELECT_TRIGGER =
  "h-6 gap-1 rounded border-none bg-transparent px-1 text-[13px] font-semibold text-[#215db0] shadow-none focus-visible:ring-0 [&_svg]:!text-[#215db0]";

function metricValue(
  objects: GenericObject[],
  fn: MetricFn,
  metricProp: string | null,
): number {
  if (fn === "count" || !metricProp) return objects.length;
  const values = objects
    .map((object) => object.properties[metricProp])
    .filter((value): value is number => typeof value === "number");
  const sum = values.reduce((total, value) => total + value, 0);
  if (fn === "sum") return sum;
  return values.length > 0 ? sum / values.length : 0;
}

/** objects 페이지를 xProp × stackProp 격자로 클라이언트 집계한다 (스택 4색 제한 → 기타 병합). */
function aggregateStacks(
  objects: GenericObject[],
  xProp: string,
  stackProp: string | null,
  fn: MetricFn,
  metricProp: string | null,
): { categories: StackCategory[]; stackKeys: string[] } {
  const byCategory = new Map<string, GenericObject[]>();
  for (const object of objects) {
    const key = formatPropertyValue(object.properties[xProp]);
    byCategory.set(key, [...(byCategory.get(key) ?? []), object]);
  }

  const stackTotals = new Map<string, number>();
  if (stackProp) {
    for (const object of objects) {
      const key = formatPropertyValue(object.properties[stackProp]);
      stackTotals.set(key, (stackTotals.get(key) ?? 0) + 1);
    }
  }
  const rankedStacks = [...stackTotals.entries()]
    .sort((left, right) => right[1] - left[1])
    .map(([key]) => key);
  const topStacks =
    rankedStacks.length > STACK_CHART_COLORS.length
      ? rankedStacks.slice(0, STACK_CHART_COLORS.length - 1)
      : rankedStacks;
  const hasOther = stackProp !== null && rankedStacks.length > topStacks.length;
  const stackKeys = stackProp
    ? [...topStacks, ...(hasOther ? [OTHER_STACK_KEY] : [])]
    : ["전체"];

  const categories = [...byCategory.entries()]
    .sort(([left], [right]) => left.localeCompare(right, "ko"))
    .slice(0, 12)
    .map(([key, members]) => {
      const segments: StackSegment[] = stackKeys
        .map((stackKey) => {
          const group = stackProp
            ? members.filter((member) => {
                const memberKey = formatPropertyValue(
                  member.properties[stackProp],
                );
                return stackKey === OTHER_STACK_KEY
                  ? !topStacks.includes(memberKey)
                  : memberKey === stackKey;
              })
            : members;
          return { stackKey, value: metricValue(group, fn, metricProp) };
        })
        .filter((segment) => segment.value > 0);
      const total = segments.reduce((sum, segment) => sum + segment.value, 0);
      return { key, total, segments };
    });

  return { categories, stackKeys };
}

function formatMetric(value: number): string {
  return Number.isInteger(value)
    ? value.toLocaleString("ko-KR")
    : value.toLocaleString("ko-KR", { maximumFractionDigits: 1 });
}

/** 축 스케일용 nice ceiling: 1/2/4/5 × 10^k 중 max 이상 최솟값. */
function niceCeiling(max: number): number {
  if (max <= 0) return 1;
  const exponent = Math.floor(Math.log10(max));
  const base = 10 ** exponent;
  for (const step of [1, 2, 4, 5, 10]) {
    if (step * base >= max) return step * base;
  }
  return 10 * base;
}

interface StackedBarCardProps {
  objectView: FoundryLiteOntologyObjectView;
  objects: GenericObject[];
  pageSize: number;
}

/** 스택 바 차트 카드: 헤더(x 속성) + 표시/기준/그룹 컨트롤 + 4색 스택 바 + 범례. */
export function StackedBarCard({
  objectView,
  objects,
  pageSize,
}: StackedBarCardProps) {
  const groupCandidates = groupableProperties(objectView);
  const numericCandidates = numericProperties(objectView);
  const xProp = groupCandidates[0]?.apiName ?? null;
  const [fn, setFn] = useState<MetricFn>("count");
  const [metricProp, setMetricProp] = useState<string | null>(
    numericCandidates[0]?.apiName ?? null,
  );
  const [stackProp, setStackProp] = useState<string>(
    groupCandidates[1]?.apiName ?? NO_STACK,
  );

  const { categories, stackKeys } = useMemo(
    () =>
      xProp
        ? aggregateStacks(
            objects,
            xProp,
            stackProp === NO_STACK ? null : stackProp,
            fn,
            fn === "count" ? null : metricProp,
          )
        : { categories: [], stackKeys: [] },
    [objects, xProp, stackProp, fn, metricProp],
  );
  const maxTotal = categories.reduce(
    (max, category) => Math.max(max, category.total),
    0,
  );
  const niceMax = niceCeiling(maxTotal);
  const colorByStack = new Map(
    stackKeys.map((key, index) => [
      key,
      STACK_CHART_COLORS[index % STACK_CHART_COLORS.length],
    ]),
  );

  if (!xProp) return null;
  const xDisplayName =
    groupCandidates.find((property) => property.apiName === xProp)
      ?.displayName ?? xProp;
  const chartHeight = 208;

  return (
    <div className="rounded border border-[#dde3e9] bg-white shadow-[0_1px_2px_rgba(17,20,24,0.04)]">
      <div className="flex h-12 items-center border-b border-[#e4e9ed] px-4 text-[14px] font-bold text-[#1c2127]">
        {xDisplayName}
      </div>
      <div className="flex items-center gap-2 border-b border-[#e4e9ed] bg-[#f6f8fa] px-4 py-2 text-[13px] text-[#1c2127]">
        <span>표시:</span>
        <Select value={fn} onValueChange={(next) => setFn(next as MetricFn)}>
          <SelectTrigger size="sm" className={INLINE_SELECT_TRIGGER}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="count">개수</SelectItem>
            <SelectItem value="sum" disabled={numericCandidates.length === 0}>
              합계
            </SelectItem>
            <SelectItem value="avg" disabled={numericCandidates.length === 0}>
              평균
            </SelectItem>
          </SelectContent>
        </Select>
        {fn !== "count" && metricProp ? (
          <>
            <span>기준:</span>
            <Select value={metricProp} onValueChange={setMetricProp}>
              <SelectTrigger size="sm" className={INLINE_SELECT_TRIGGER}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {numericCandidates.map((property) => (
                  <SelectItem key={property.apiName} value={property.apiName}>
                    {property.displayName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        ) : null}
      </div>
      <div className="flex items-center gap-2 border-b border-[#e4e9ed] bg-[#f6f8fa] px-4 py-2 text-[13px] text-[#1c2127]">
        <span>그룹:</span>
        <Select value={stackProp} onValueChange={setStackProp}>
          <SelectTrigger size="sm" className={INLINE_SELECT_TRIGGER}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_STACK}>그룹 없음</SelectItem>
            {groupCandidates.map((property) => (
              <SelectItem key={property.apiName} value={property.apiName}>
                {property.displayName}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="px-6 pt-8 pb-2">
        {categories.length === 0 ? (
          <EmptyState
            title="차트로 그릴 객체가 없습니다"
            description="필터 조건을 줄이거나 다른 속성을 선택해 보세요."
            className="p-6"
          />
        ) : (
          <>
            <div
              className="relative ml-9 flex items-end justify-around gap-4 border-b border-[#c5cdd4]"
              style={{ height: chartHeight }}
            >
              {[0.25, 0.5, 0.75, 1].map((ratio) => {
                const tickValue = niceMax * ratio;
                const hasCleanLabel =
                  Number.isInteger(tickValue) || niceMax >= 4;
                return (
                  <span
                    key={ratio}
                    className="pointer-events-none absolute inset-x-0 border-t border-[#eef1f4]"
                    style={{ bottom: (chartHeight - 26) * ratio }}
                  >
                    {hasCleanLabel ? (
                      <span className="absolute -left-9 w-7 -translate-y-1/2 text-right text-[12px] text-[#5f6b7c]">
                        {formatMetric(tickValue)}
                      </span>
                    ) : null}
                  </span>
                );
              })}
              {categories.map((category) => (
                <div
                  key={category.key}
                  className="relative flex h-full w-full max-w-24 flex-col items-center justify-end"
                >
                  <span className="pb-0.5 text-[12px] text-[#383e47]">
                    {formatMetric(category.total)}
                  </span>
                  <div className="flex w-full flex-col-reverse">
                    {category.segments.map((segment) => {
                      const height =
                        niceMax > 0
                          ? (segment.value / niceMax) * (chartHeight - 26)
                          : 0;
                      const segmentColor = colorByStack.get(segment.stackKey);
                      const hasDarkLabel =
                        segmentColor === STACK_CHART_COLORS[2] ||
                        segmentColor === STACK_CHART_COLORS[3];
                      return (
                        <div
                          key={segment.stackKey}
                          className="flex w-full items-center justify-center border-t border-white first:border-t-0"
                          style={{
                            height: Math.max(height, 3),
                            backgroundColor: segmentColor,
                          }}
                        >
                          {height > 18 ? (
                            <span
                              className={cn(
                                "text-[11px] font-medium",
                                hasDarkLabel ? "text-[#383e47]" : "text-white",
                              )}
                            >
                              {formatMetric(segment.value)}
                            </span>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
            <div className="ml-9 flex justify-around gap-4 pt-1.5">
              {categories.map((category) => (
                <span
                  key={category.key}
                  className="w-full max-w-24 truncate text-center text-[12px] text-[#5f6b7c]"
                >
                  {category.key}
                </span>
              ))}
            </div>
            <div className="flex flex-wrap items-center justify-center gap-x-5 gap-y-1 py-3">
              {stackKeys.map((stackKey) => (
                <span
                  key={stackKey}
                  className="flex items-center gap-1.5 text-[13px] text-[#1c2127]"
                >
                  <span
                    className="size-2.5 rounded-[2px]"
                    style={{ backgroundColor: colorByStack.get(stackKey) }}
                  />
                  {stackKey}
                </span>
              ))}
            </div>
          </>
        )}
      </div>
      <div className="border-t border-[#eef1f4] px-4 py-1.5 font-mono text-[10px] text-muted-foreground">
        objects.generic.query 현재 페이지 {objects.length}건 클라이언트 집계 ·
        최대 {pageSize}건 제한
      </div>
    </div>
  );
}
