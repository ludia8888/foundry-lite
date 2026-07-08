/** Dataset inspect payload의 schema(Record<string, unknown>)를 화면 모델로 파싱하는 헬퍼. */

export interface SchemaColumn {
  name: string;
  type: string;
  nullable: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseSchemaColumns(
  schema: Record<string, unknown> | null,
): SchemaColumn[] {
  if (!schema || !Array.isArray(schema.columns)) return [];
  return schema.columns.flatMap((column) => {
    if (!isRecord(column) || typeof column.name !== "string") return [];
    return [
      {
        name: column.name,
        type: typeof column.type === "string" ? column.type : "unknown",
        nullable: column.nullable !== false,
      },
    ];
  });
}

export function parsePrimaryKey(
  schema: Record<string, unknown> | null,
): string[] {
  if (!schema || !Array.isArray(schema.primary_key)) return [];
  return schema.primary_key.filter(
    (key): key is string => typeof key === "string",
  );
}

export function formatByteSize(bytes: number | null): string {
  if (bytes === null) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatRowCount(rowCount: number | null): string {
  if (rowCount === null) return "-";
  return rowCount.toLocaleString("ko-KR");
}

/** ISO timestamp를 "YYYY-MM-DD HH:mm" 형태의 mono evidence로 축약한다. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "-";
  return iso.slice(0, 16).replace("T", " ");
}

/** 프리뷰 헤더 타입 서브라벨: 레퍼런스 문법에 맞춰 첫 글자를 대문자로 표기한다. */
export function formatColumnType(type: string): string {
  if (type.length === 0) return type;
  return type.charAt(0).toUpperCase() + type.slice(1);
}

export function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
