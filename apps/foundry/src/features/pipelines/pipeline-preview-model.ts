import type { PipelinePreviewRun } from "@foundry-lite/sdk";

export type PreviewRecord = Record<string, unknown>;

export function previewOutput(
  run: PipelinePreviewRun | null,
  targetNodeId: string | null,
): PreviewRecord | null {
  if (!run) return null;
  const outputs = recordList(run.outputs);
  if (!targetNodeId) return outputs[0] ?? null;
  return (
    outputs.find((output) => textValue(output.nodeId) === targetNodeId) ??
    outputs[0] ??
    null
  );
}

export function previewArtifacts(
  run: PipelinePreviewRun | null,
): PreviewRecord[] {
  return run ? recordList(run.artifacts) : [];
}

export function previewItems(output: PreviewRecord | null): PreviewRecord[] {
  return output ? recordList(output.items) : [];
}

export function previewPassport(
  value: PreviewRecord | null,
): PreviewRecord | null {
  return value ? recordValue(value.passport) : null;
}

export function recordList(value: unknown): PreviewRecord[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const record = recordValue(item);
    return record ? [record] : [];
  });
}

export function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

export function recordValue(value: unknown): PreviewRecord | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  return value as PreviewRecord;
}

export function textValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function booleanValue(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

export function displayCell(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (Array.isArray(value)) return `[${value.length}개 값]`;
  if (typeof value === "object") return compactJson(value);
  return String(value);
}

export function compactJson(value: unknown): string {
  const rendered = JSON.stringify(value);
  if (!rendered) return "-";
  return rendered.length > 120 ? `${rendered.slice(0, 117)}...` : rendered;
}

export function previewStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    QUEUED: "대기 중",
    RUNNING: "실행 중",
    CANCEL_REQUESTED: "취소 요청됨",
    SUCCEEDED: "성공",
    PARTIAL: "부분 성공",
    FAILED: "실패",
    CANCELLED: "취소됨",
  };
  return labels[status.toUpperCase()] ?? status;
}
