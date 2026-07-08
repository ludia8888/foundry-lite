import type { GenericObject } from "@foundry-lite/sdk";

import type { AggregationMetric } from "./app-model";

const METRIC_LABELS: Record<AggregationMetric, string> = {
  count: "개수",
  sum: "합계",
  avg: "평균",
  min: "최소",
  max: "최대",
};

export function metricLabel(metric: AggregationMetric): string {
  return METRIC_LABELS[metric] ?? metric;
}

function numericValues(
  objects: readonly GenericObject[],
  property: string,
): number[] {
  const values: number[] = [];
  for (const object of objects) {
    const value = object.properties[property];
    if (typeof value === "number" && Number.isFinite(value)) values.push(value);
  }
  return values;
}

/** 객체 집합에 대한 단일 집계 값. count는 property 불필요. */
export function computeMetric(
  objects: readonly GenericObject[],
  metric: AggregationMetric,
  property?: string | null,
): number {
  if (metric === "count") return objects.length;
  if (!property) return 0;
  const values = numericValues(objects, property);
  if (values.length === 0) return 0;
  switch (metric) {
    case "sum":
      return values.reduce((total, value) => total + value, 0);
    case "avg":
      return values.reduce((total, value) => total + value, 0) / values.length;
    case "min":
      return Math.min(...values);
    case "max":
      return Math.max(...values);
    default:
      return 0;
  }
}

export type AggregateBucket = { label: string; value: number };

/** group-by 속성별 집계 버킷. 차트 축 데이터. */
export function groupAggregate(
  objects: readonly GenericObject[],
  groupByProperty: string,
  metric: AggregationMetric,
  property?: string | null,
): AggregateBucket[] {
  const buckets = new Map<string, GenericObject[]>();
  for (const object of objects) {
    const key = String(object.properties[groupByProperty] ?? "—");
    const list = buckets.get(key);
    if (list) list.push(object);
    else buckets.set(key, [object]);
  }
  return Array.from(buckets.entries())
    .map(([label, group]) => ({
      label,
      value: computeMetric(group, metric, property),
    }))
    .sort((a, b) => b.value - a.value);
}

export function formatMetricValue(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export type CrossAggregate = {
  categories: string[];
  /** 시리즈 라벨. 시리즈 속성이 없으면 단일 시리즈. */
  series: string[];
  /** matrix[categoryIndex][seriesIndex] = 집계 값. */
  matrix: number[][];
  categoryTotals: number[];
};

/**
 * 카테고리(group-by) × 시리즈(break-down) 교차 집계.
 * 시리즈가 없으면 단일 시리즈로 처리 → 누적 막대/다중 라인/영역의 공통 데이터.
 */
export function crossAggregate(
  objects: readonly GenericObject[],
  groupByProperty: string,
  seriesProperty: string | null | undefined,
  metric: AggregationMetric,
  property?: string | null,
): CrossAggregate {
  const categories: string[] = [];
  const seriesSet: string[] = [];
  const groups = new Map<string, Map<string, GenericObject[]>>();

  for (const object of objects) {
    const category = String(object.properties[groupByProperty] ?? "—");
    const series = seriesProperty
      ? String(object.properties[seriesProperty] ?? "—")
      : "전체";
    if (!groups.has(category)) {
      groups.set(category, new Map());
      categories.push(category);
    }
    if (!seriesSet.includes(series)) seriesSet.push(series);
    const bySeries = groups.get(category)!;
    const list = bySeries.get(series);
    if (list) list.push(object);
    else bySeries.set(series, [object]);
  }

  const matrix = categories.map((category) =>
    seriesSet.map((series) => {
      const list = groups.get(category)?.get(series) ?? [];
      return computeMetric(list, metric, property);
    }),
  );
  const categoryTotals = matrix.map((row) =>
    row.reduce((sum, value) => sum + value, 0),
  );

  // 합계 큰 카테고리 우선 정렬.
  const order = categories
    .map((_, index) => index)
    .sort((a, b) => categoryTotals[b] - categoryTotals[a]);

  return {
    categories: order.map((index) => categories[index]),
    series: seriesSet,
    matrix: order.map((index) => matrix[index]),
    categoryTotals: order.map((index) => categoryTotals[index]),
  };
}
