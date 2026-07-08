import type { TabularRow } from "@foundry-lite/sdk";

/**
 * Contour식 분석 경로(analysis path) 모델.
 * dataset preview 행을 클라이언트에서 필터/집계하여 board 스택으로 표현한다.
 * 백엔드 실데이터(datasets.preview)만 사용하며 값을 fabricate하지 않는다.
 */

export const MASKED_TOKEN = "***MASKED***";

export const SYSTEM_COLUMN_NAMES = ["branch", "version"] as const;

export interface PreviewColumn {
  name: string;
  isSystem: boolean;
  isNumeric: boolean;
  isMasked: boolean;
}

/** preview 행에서 컬럼 메타(숫자/시스템/마스킹 여부)를 추론한다. */
export function derivePreviewColumns(
  rows: readonly TabularRow[],
): PreviewColumn[] {
  if (rows.length === 0) return [];
  const names = Object.keys(rows[0]);
  return names.map((name) => {
    const sample = rows.find(
      (row) => row[name] !== null && row[name] !== undefined,
    )?.[name];
    const isMasked = rows.some((row) => row[name] === MASKED_TOKEN);
    return {
      name,
      isSystem: (SYSTEM_COLUMN_NAMES as readonly string[]).includes(name),
      isNumeric:
        !isMasked && (typeof sample === "number" || isNumericString(sample)),
      isMasked,
    };
  });
}

function isNumericString(value: unknown): boolean {
  return (
    typeof value === "string" &&
    value.trim() !== "" &&
    !Number.isNaN(Number(value))
  );
}

/**
 * DISTRIBUTION 기본 groupBy: 고유값이 2개 이상이면서 가장 카디널리티가 낮은
 * 카테고리 컬럼을 고른다 (order_id 같은 고유 식별자 대신 의미있는 분포를 만든다).
 */
export function pickDefaultGroupByColumn(
  rows: readonly TabularRow[],
  columns: readonly PreviewColumn[],
): string {
  const candidates = columns.filter(
    (column) => !column.isSystem && !column.isNumeric,
  );
  if (candidates.length === 0) return "";
  const ranked = candidates
    .map((column) => {
      const distinct = new Set(
        rows.map((row) => formatCellValue(row[column.name])),
      ).size;
      return { name: column.name, distinct };
    })
    .sort((a, b) => a.distinct - b.distinct);
  const meaningful = ranked.find(
    (column) => column.distinct > 1 && column.distinct < rows.length,
  );
  return (meaningful ?? ranked[0]).name;
}

/** null/객체/마스킹 값을 셀 문자열로 정규화한다. */
export function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function formatMetricValue(value: number): string {
  if (Number.isInteger(value)) return value.toLocaleString("ko-KR");
  return value.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
}

export function toNumericValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (isNumericString(value)) return Number(value);
  return null;
}

// ── 필터 board ─────────────────────────────────────────────

export const FILTER_OPERATORS = [
  { id: "eq", label: "=" },
  { id: "neq", label: "≠" },
  { id: "contains", label: "포함" },
  { id: "gt", label: ">" },
  { id: "gte", label: "≥" },
  { id: "lt", label: "<" },
  { id: "lte", label: "≤" },
] as const;

export type FilterOperator = (typeof FILTER_OPERATORS)[number]["id"];

export interface FilterCondition {
  id: string;
  column: string;
  operator: FilterOperator;
  value: string;
}

const NUMERIC_OPERATORS: readonly FilterOperator[] = ["gt", "gte", "lt", "lte"];

function matchCondition(row: TabularRow, condition: FilterCondition): boolean {
  const raw = row[condition.column];
  const cell = formatCellValue(raw);
  const target = condition.value;
  if (NUMERIC_OPERATORS.includes(condition.operator)) {
    const left = toNumericValue(raw);
    const right = Number(target);
    if (left === null || Number.isNaN(right)) return false;
    switch (condition.operator) {
      case "gt":
        return left > right;
      case "gte":
        return left >= right;
      case "lt":
        return left < right;
      case "lte":
        return left <= right;
      default:
        return false;
    }
  }
  switch (condition.operator) {
    case "eq":
      return cell === target;
    case "neq":
      return cell !== target;
    case "contains":
      return cell.toLowerCase().includes(target.toLowerCase());
    default:
      return false;
  }
}

/** "Keep rows where ..." — 모든 조건을 AND로 적용한다 (Contour keep-rows 시맨틱). */
export function applyFilters(
  rows: readonly TabularRow[],
  conditions: readonly FilterCondition[],
): readonly TabularRow[] {
  const active = conditions.filter(
    (condition) => condition.column && condition.value.trim() !== "",
  );
  if (active.length === 0) return rows;
  return rows.filter((row) =>
    active.every((condition) => matchCondition(row, condition)),
  );
}

// ── 집계 board (distribution) ──────────────────────────────

export type AggregateFunction = "count" | "sum" | "avg";

export const AGGREGATE_FUNCTIONS = [
  { id: "count", label: "행 수 (count)" },
  { id: "sum", label: "합계 (sum)" },
  { id: "avg", label: "평균 (avg)" },
] as const;

export interface AggregateBar {
  key: string;
  value: number;
}

export interface AggregateResult {
  bars: AggregateBar[];
  maxValue: number;
  totalValue: number;
}

/**
 * groupBy 컬럼별로 metric 컬럼을 집계한다 (클라이언트 집계).
 * count는 metric 없이, sum/avg는 숫자 metric 컬럼이 필요하다.
 */
export function computeAggregate(
  rows: readonly TabularRow[],
  groupBy: string,
  fn: AggregateFunction,
  metricColumn: string | null,
): AggregateResult {
  const bucketsByKey = new Map<string, { sum: number; count: number }>();
  for (const row of rows) {
    const key = formatCellValue(row[groupBy]);
    const bucket = bucketsByKey.get(key) ?? { sum: 0, count: 0 };
    bucket.count += 1;
    if (fn !== "count" && metricColumn) {
      const numeric = toNumericValue(row[metricColumn]);
      if (numeric !== null) bucket.sum += numeric;
    }
    bucketsByKey.set(key, bucket);
  }

  const bars: AggregateBar[] = [...bucketsByKey.entries()]
    .map(([key, bucket]) => {
      const value =
        fn === "count"
          ? bucket.count
          : fn === "sum"
            ? bucket.sum
            : bucket.count === 0
              ? 0
              : bucket.sum / bucket.count;
      return { key, value };
    })
    .sort((a, b) => b.value - a.value);

  const maxValue = bars.reduce((max, bar) => Math.max(max, bar.value), 0);
  const totalValue = bars.reduce((total, bar) => total + bar.value, 0);
  return { bars, maxValue, totalValue };
}
