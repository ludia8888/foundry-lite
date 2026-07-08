import type { StatusIntent } from "@/components/shared/StatusPill";

/** 소스 타입/kind → 한국어 라벨. */
const SOURCE_TYPE_LABELS: Record<string, string> = {
  rest: "REST 커넥터",
  rest_api: "REST API",
  postgres_jdbc: "Postgres 데이터베이스",
  sap_odata: "SAP OData",
  sharepoint_graph: "SharePoint Graph",
  webhook_listener: "인바운드 웹훅",
  debezium_cdc: "Debezium CDC",
  media_upload: "미디어 업로드",
  batch_file: "배치 파일 업로드",
  csv_upload: "CSV 업로드",
};

export function sourceTypeLabel(sourceType: string | null | undefined): string {
  if (!sourceType) return "알 수 없는 타입";
  return SOURCE_TYPE_LABELS[sourceType] ?? sourceType;
}

/** capability 코드 → 한국어 필 라벨. */
const CAPABILITY_LABELS: Record<string, string> = {
  batch: "배치 동기화",
  batch_file: "배치 파일",
  exploration: "소스 탐색",
  cdc: "CDC",
  code_use: "코드에서 사용",
  media: "미디어 동기화",
  streaming: "스트리밍",
};

export function capabilityLabel(capability: string): string {
  return CAPABILITY_LABELS[capability] ?? capability;
}

const STATUS_INTENTS: Record<string, StatusIntent> = {
  active: "success",
  succeeded: "success",
  ready: "success",
  completed: "success",
  running: "info",
  pending: "info",
  scheduled: "info",
  failed: "danger",
  error: "danger",
  disabled: "neutral",
};

export function statusIntent(status: string | null | undefined): StatusIntent {
  if (!status) return "neutral";
  return STATUS_INTENTS[status.toLowerCase()] ?? "neutral";
}

const STATUS_LABELS: Record<string, string> = {
  active: "활성",
  succeeded: "성공",
  running: "실행 중",
  pending: "대기",
  failed: "실패",
  disabled: "비활성",
};

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "—";
  return STATUS_LABELS[status.toLowerCase()] ?? status;
}

/** 백엔드 safe identifier 규칙(^[A-Za-z_][A-Za-z0-9_]*$)과 동일. */
const IDENTIFIER_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;

export function isValidIdentifier(value: string): boolean {
  return IDENTIFIER_PATTERN.test(value);
}

export function sanitizeIdentifier(raw: string): string {
  const collapsed = raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!collapsed) return "";
  return /^[0-9]/.test(collapsed) ? `src_${collapsed}` : collapsed;
}

const DATASET_REF_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$/;

export function isValidDatasetRef(value: string): boolean {
  return DATASET_REF_PATTERN.test(value);
}

/** 백엔드 operationsPath("/api/..." 또는 "/operations/...")를 앱 라우트로 변환. */
export function toOperationsHref(
  path: string | null | undefined,
): string | null {
  if (!path) return null;
  return path.startsWith("/api/") ? path.slice(4) : path;
}

export function toDatasetHref(
  datasetRef: string | null | undefined,
): string | null {
  if (!datasetRef) return null;
  return `/datasets?ref=${encodeURIComponent(datasetRef)}`;
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

/** JSON record에서 number 값을 안전하게 추출한다 (증거 표시에 사용). */
export function readNumberField(
  record: Record<string, unknown> | null | undefined,
  field: string,
): number | null {
  const value = record?.[field];
  return typeof value === "number" ? value : null;
}

export function readSyncRunRowCount(
  summary: Record<string, unknown> | null | undefined,
): number | null {
  const direct =
    readNumberField(summary, "rowCount") ??
    readNumberField(summary, "rows") ??
    readNumberField(summary, "recordCount");
  if (direct !== null) return direct;
  const workflowRun = summary?.workflowRun;
  if (!workflowRun || typeof workflowRun !== "object") return null;
  const output = (workflowRun as Record<string, unknown>).output;
  if (!output || typeof output !== "object") return null;
  return readNumberField(output as Record<string, unknown>, "rowCount");
}

export function readTextField(
  record: Record<string, unknown> | null | undefined,
  field: string,
): string | null {
  const value = record?.[field];
  return typeof value === "string" && value.length > 0 ? value : null;
}
