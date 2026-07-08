import type { GenericObject, ObjectFilter } from "@foundry-lite/sdk";
import type {
  FoundryLiteOntologyObjectView,
  FoundryLiteOntologyPropertyView,
} from "@foundry-lite/sdk/react";

import type { StatusIntent } from "@/components/shared/StatusPill";

/** 객체 상세 화면이 참조하는 최소 좌표 (list↔detail 왕복에 사용). */
export type ObjectRef = {
  objectType: string;
  objectId: string;
};

export type FilterOp = "eq" | "contains" | "gte" | "lte";

/** 필터 칩 바에 표시되는 제거 가능한 필터 1건. dataType은 값 강제 변환용. */
export type FilterPill = {
  id: string;
  property: string;
  op: FilterOp;
  value: string;
  dataType: string;
};

export const FILTER_OPS: ReadonlyArray<{ op: FilterOp; label: string }> = [
  { op: "eq", label: "같음" },
  { op: "contains", label: "포함" },
  { op: "gte", label: "이상" },
  { op: "lte", label: "이하" },
];

export const FILTER_OP_LABELS: Record<FilterOp, string> = {
  eq: "=",
  contains: "포함",
  gte: "≥",
  lte: "≤",
};

/** 필터 필 자연어 라벨 파트: "Manufacturer is Bombardier" 스타일 (값만 볼드 렌더). */
export type PillLabelParts = {
  prefix: string;
  value: string;
  suffix: string;
};

export function pillLabelParts(pill: FilterPill): PillLabelParts {
  switch (pill.op) {
    case "eq":
      return {
        prefix: `${pill.property}이(가) `,
        value: pill.value,
        suffix: "",
      };
    case "contains":
      return {
        prefix: `${pill.property}에 `,
        value: pill.value,
        suffix: " 포함",
      };
    case "gte":
      return {
        prefix: `${pill.property}이(가) `,
        value: pill.value,
        suffix: " 이상",
      };
    case "lte":
      return {
        prefix: `${pill.property}이(가) `,
        value: pill.value,
        suffix: " 이하",
      };
  }
}

/** 링크 순회로 파생된 탐색의 출처 (필터 바 링크 필에 표시). */
export type LinkOrigin = {
  fromTypeName: string;
  linkApiName: string;
};

/** 필터 undo/redo 히스토리 1스냅샷. */
export type ExplorationSnapshot = {
  pills: FilterPill[];
  search: string;
};

/** 브라우저식 탐색 탭 1개: 타입·필터·검색·정렬을 독립 유지한다. */
export type Exploration = {
  id: string;
  typeName: string;
  pills: FilterPill[];
  search: string;
  history: ExplorationSnapshot[];
  historyIndex: number;
  linkOrigin: LinkOrigin | null;
};

export function createExplorationId(): string {
  return `exp-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

export function createExploration(
  typeName: string,
  linkOrigin: LinkOrigin | null = null,
): Exploration {
  return {
    id: createExplorationId(),
    typeName,
    pills: [],
    search: "",
    history: [{ pills: [], search: "" }],
    historyIndex: 0,
    linkOrigin,
  };
}

/** 스택 바 차트 카테고리 컬러 (레퍼런스 실측: 파랑/그린/앰버/레드). */
export const STACK_CHART_COLORS = [
  "#4580E6",
  "#43A047",
  "#F2C14E",
  "#E4572E",
] as const;

/** 분포 카드 가로 막대 컬러 (레퍼런스 실측). */
export const DIST_BAR_COLOR = "#377CB8";

const NUMERIC_DATA_TYPES = new Set([
  "integer",
  "int",
  "long",
  "float",
  "double",
  "decimal",
  "number",
]);

export function isNumericDataType(dataType: string): boolean {
  return NUMERIC_DATA_TYPES.has(dataType.toLowerCase());
}

export function numericProperties(
  objectView: FoundryLiteOntologyObjectView | null,
): FoundryLiteOntologyPropertyView[] {
  return (objectView?.properties ?? []).filter((property) =>
    isNumericDataType(property.dataType),
  );
}

/** 그룹 기준 후보: primary key가 아닌 문자열 속성. */
export function groupableProperties(
  objectView: FoundryLiteOntologyObjectView | null,
): FoundryLiteOntologyPropertyView[] {
  return (objectView?.properties ?? []).filter(
    (property) =>
      !property.isPrimaryKey && !isNumericDataType(property.dataType),
  );
}

function coercePillValue(pill: FilterPill): unknown {
  if (isNumericDataType(pill.dataType)) {
    const numeric = Number(pill.value);
    return Number.isNaN(numeric) ? pill.value : numeric;
  }
  return pill.value;
}

/** 필터 칩 목록을 objects.generic.query가 받는 ObjectFilter AST로 변환한다. */
export function buildObjectFilter(
  pills: readonly FilterPill[],
): ObjectFilter | undefined {
  if (pills.length === 0) return undefined;
  const leaves: ObjectFilter[] = pills.map((pill) => ({
    property: pill.property,
    op: pill.op,
    value: coercePillValue(pill),
  }));
  return leaves.length === 1 ? leaves[0] : { and: leaves };
}

function isFilterLeaf(
  node: unknown,
): node is { property: string; op: FilterOp; value: unknown } {
  if (typeof node !== "object" || node === null) return false;
  const leaf = node as { property?: unknown; op?: unknown; value?: unknown };
  return (
    typeof leaf.property === "string" &&
    typeof leaf.op === "string" &&
    FILTER_OPS.some((candidate) => candidate.op === leaf.op) &&
    (typeof leaf.value === "string" ||
      typeof leaf.value === "number" ||
      typeof leaf.value === "boolean")
  );
}

/**
 * 저장된 ObjectSet의 filter 정의를 필터 칩으로 되돌린다.
 * 단순 AND 결합만 지원하며, 복합 필터는 null을 반환한다 (future gap).
 */
export function parseObjectFilterToPills(filter: unknown): FilterPill[] | null {
  if (filter === null || filter === undefined) return [];
  const nodes = isFilterLeaf(filter)
    ? [filter]
    : typeof filter === "object" &&
        Array.isArray((filter as { and?: unknown }).and)
      ? (filter as { and: unknown[] }).and
      : null;
  if (!nodes) return null;
  const pills: FilterPill[] = [];
  for (const node of nodes) {
    if (!isFilterLeaf(node)) return null;
    pills.push({
      id: createPillId(),
      property: node.property,
      op: node.op,
      value: String(node.value),
      dataType: typeof node.value === "number" ? "double" : "string",
    });
  }
  return pills;
}

export function createPillId(): string {
  return `pill-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

/** titleProperty 우선, 없으면 objectId를 제목으로 쓴다. */
export function objectTitle(
  object: GenericObject,
  objectView: FoundryLiteOntologyObjectView | null,
): string {
  const titleProperty = objectView?.objectType.titleProperty ?? null;
  const raw = titleProperty ? object.properties[titleProperty] : undefined;
  if (typeof raw === "string" && raw.length > 0) return raw;
  if (typeof raw === "number") return String(raw);
  return object.objectId;
}

export function formatPropertyValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString("ko-KR")
      : value.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
  }
  if (typeof value === "boolean") return value ? "참" : "거짓";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

const TYPE_ICON_CLASSES = [
  "bg-primary",
  "bg-success",
  "bg-warning",
  "bg-destructive",
] as const;

const TYPE_CHIP_CLASSES = [
  "bg-primary/10 text-primary",
  "bg-success/10 text-success",
  "bg-warning/10 text-warning",
  "bg-destructive/10 text-destructive",
] as const;

function typeColorIndex(apiName: string): number {
  let hash = 0;
  for (const char of apiName) hash = (hash * 31 + char.charCodeAt(0)) % 997;
  return hash % TYPE_ICON_CLASSES.length;
}

/** 객체 타입별 컬러 사각 아이콘 배경 (Palantir 스타일 컬러 아이콘 칩). */
export function objectTypeIconClass(apiName: string): string {
  return TYPE_ICON_CLASSES[typeColorIndex(apiName)];
}

export function objectTypeChipClass(apiName: string): string {
  return TYPE_CHIP_CLASSES[typeColorIndex(apiName)];
}

const STATUS_INTENTS: Record<string, StatusIntent> = {
  APPROVED: "success",
  ACTIVE: "success",
  DONE: "success",
  PENDING: "warning",
  REVIEW: "info",
  REJECTED: "danger",
  FAILED: "danger",
  CANCELLED: "neutral",
};

export function statusIntentFor(value: unknown): StatusIntent {
  if (typeof value !== "string") return "neutral";
  return STATUS_INTENTS[value.toUpperCase()] ?? "neutral";
}

export type LineageEvidence = {
  lineageCount: number;
  runIds: string[];
};

/**
 * objects.generic.get(explain=true) 응답의 lineage 근거를 방어적으로 파싱한다.
 * (SDK 타입에는 explain.lineage가 명시돼 있지 않아 런타임 shape을 확인한다.)
 */
export function lineageEvidence(object: GenericObject | null): LineageEvidence {
  const explain = (object as { explain?: unknown } | null)?.explain;
  if (typeof explain !== "object" || explain === null) {
    return { lineageCount: 0, runIds: [] };
  }
  const lineage = (explain as { lineage?: unknown }).lineage;
  if (!Array.isArray(lineage)) return { lineageCount: 0, runIds: [] };
  const runIds = lineage
    .map((entry) =>
      typeof entry === "object" && entry !== null
        ? (entry as { created_by_run_id?: unknown }).created_by_run_id
        : undefined,
    )
    .filter((runId): runId is string => typeof runId === "string");
  return { lineageCount: lineage.length, runIds: [...new Set(runIds)] };
}
