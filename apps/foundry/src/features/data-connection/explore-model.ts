/** 소스 탐색(resultSummary) 응답을 안전하게 파싱하는 헬퍼. */

export interface ExploreColumn {
  name: string;
  type: string;
}

export interface ExploreTable {
  tableName: string;
  columns: ExploreColumn[];
}

function readColumns(value: unknown): ExploreColumn[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((column) => {
    if (typeof column !== "object" || column === null) return [];
    const record = column as Record<string, unknown>;
    if (typeof record.name !== "string") return [];
    return [
      {
        name: record.name,
        type: typeof record.type === "string" ? record.type : "unknown",
      },
    ];
  });
}

/** postgres 탐색(테이블 목록) 결과 → 리소스 트리 항목. */
export function readExploreTables(
  summary: Record<string, unknown> | null | undefined,
): ExploreTable[] {
  const tables = summary?.tables;
  if (!Array.isArray(tables)) return [];
  return tables.flatMap((table) => {
    if (typeof table !== "object" || table === null) return [];
    const record = table as Record<string, unknown>;
    if (typeof record.tableName !== "string") return [];
    return [
      { tableName: record.tableName, columns: readColumns(record.columns) },
    ];
  });
}

/** 테이블/REST 프리뷰 결과의 샘플 행 목록. */
export function readExploreSampleRows(
  summary: Record<string, unknown> | null | undefined,
): Record<string, unknown>[] {
  const sample = summary?.sample;
  if (!Array.isArray(sample)) return [];
  return sample.filter(
    (row): row is Record<string, unknown> =>
      typeof row === "object" && row !== null && !Array.isArray(row),
  );
}

/** 프리뷰 결과의 스키마 컬럼 목록. */
export function readExploreSchemaColumns(
  summary: Record<string, unknown> | null | undefined,
): ExploreColumn[] {
  const schema = summary?.schema;
  if (typeof schema !== "object" || schema === null) return [];
  return readColumns((schema as Record<string, unknown>).columns);
}

/** 샘플 셀 값을 mono 텍스트로 표시하기 위한 직렬화. */
export function formatSampleCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

/** 테이블 프리뷰 탭 하나의 캐시 상태 (테이블별 exploration.run 결과). */
export interface ExploreTablePreview {
  status: "running" | "succeeded" | "failed";
  rows: Record<string, unknown>[];
  columns: ExploreColumn[];
  explorationRunId: string | null;
  errorMessage: string | null;
}

/** 컬럼명 기반으로 추정된 FK 관계 (그래프 뷰 엣지). */
export interface ExploreFkEdge {
  id: string;
  sourceTable: string;
  targetTable: string;
  columnName: string;
}

function matchesFkTarget(tableName: string, base: string): boolean {
  const normalized = tableName.toLowerCase();
  return (
    normalized === base ||
    normalized === `${base}s` ||
    normalized === `${base}es` ||
    normalized.endsWith(`_${base}`) ||
    normalized.endsWith(`_${base}s`)
  );
}

/**
 * `*_id` 컬럼명 → 대상 테이블명(단수/복수/접두사 변형) 매칭으로 FK를 추정한다.
 * 예: customer_id → customers / erp_customers.
 */
export function inferForeignKeyEdges(tables: ExploreTable[]): ExploreFkEdge[] {
  return tables.flatMap((table) =>
    table.columns.flatMap((column) => {
      const name = column.name.toLowerCase();
      if (!name.endsWith("_id") || name.length <= 3) return [];
      const base = name.slice(0, -3);
      const target = tables.find(
        (candidate) =>
          candidate.tableName !== table.tableName &&
          matchesFkTarget(candidate.tableName, base),
      );
      if (!target) return [];
      return [
        {
          id: `fk:${table.tableName}.${column.name}->${target.tableName}`,
          sourceTable: table.tableName,
          targetTable: target.tableName,
          columnName: column.name,
        },
      ];
    }),
  );
}

/** 테이블별 FK 컬럼명 lookup (그래프 노드에서 FK 컬럼 강조). */
export function fkColumnNamesByTable(
  edges: ExploreFkEdge[],
): Map<string, string[]> {
  const namesByTable = new Map<string, string[]>();
  for (const edge of edges) {
    const existing = namesByTable.get(edge.sourceTable) ?? [];
    namesByTable.set(edge.sourceTable, [...existing, edge.columnName]);
  }
  return namesByTable;
}

const GRAPH_COLUMN_COUNT = 3;
const GRAPH_CELL_WIDTH = 280;
const GRAPH_CELL_HEIGHT = 170;

/** 그래프 뷰 초기 배치: 3열 그리드 + 약간의 세로 스태거. */
export function computeExploreGraphPositions(
  tables: ExploreTable[],
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  tables.forEach((table, index) => {
    const column = index % GRAPH_COLUMN_COUNT;
    const row = Math.floor(index / GRAPH_COLUMN_COUNT);
    positions.set(table.tableName, {
      x: column * GRAPH_CELL_WIDTH,
      y: row * GRAPH_CELL_HEIGHT + column * 28,
    });
  });
  return positions;
}
