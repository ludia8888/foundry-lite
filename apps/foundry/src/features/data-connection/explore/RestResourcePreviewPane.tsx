import type {
  ConnectorResource,
  ConnectorResourceTestResult,
} from "@foundry-lite/sdk";
import { FileJson2, KeyRound, Loader2, Network, Search } from "lucide-react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { formatBytes, readNumberField, readTextField } from "../source-model";

interface RestResourcePreviewPaneProps {
  resource: ConnectorResource | null;
  result: ConnectorResourceTestResult | null;
  error: unknown;
  isRunning: boolean;
  requestId: string | null;
  onPreview: () => void;
}

export function RestResourcePreviewPane({
  resource,
  result,
  error,
  isRunning,
  requestId,
  onPreview,
}: RestResourcePreviewPaneProps) {
  if (!resource) {
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center text-xs text-muted-foreground">
        왼쪽에서 리소스를 선택하세요.
      </main>
    );
  }
  const columns = previewColumns(resource, result);
  const isSucceeded = result?.status.toLowerCase() === "succeeded";

  return (
    <main className="flex min-w-0 flex-1 flex-col">
      <div className="flex min-h-10 flex-wrap items-center gap-2 border-b px-3 py-1.5">
        <div className="min-w-0">
          <div className="truncate text-xs font-semibold">
            {resource.resourceName}
          </div>
          <div className="truncate font-mono text-[10px] text-muted-foreground">
            {resource.resourcePath}
          </div>
        </div>
        {result ? (
          <StatusPill intent={isSucceeded ? "success" : "danger"}>
            {isSucceeded ? "미리보기 성공" : "미리보기 실패"}
          </StatusPill>
        ) : null}
        <Button
          size="sm"
          className="ml-auto"
          disabled={isRunning}
          onClick={onPreview}
        >
          {isRunning ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Search className="size-3.5" />
          )}
          {isRunning ? "읽는 중" : result ? "다시 미리보기" : "미리보기"}
        </Button>
      </div>

      {error ? (
        <div className="p-3">
          <ErrorState error={error} onRetry={onPreview} />
        </div>
      ) : null}
      {!error && !result && !isRunning ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6 text-center">
          <FileJson2 className="mb-3 size-8 text-muted-foreground/50" />
          <div className="text-sm font-medium">원격 데이터 확인</div>
          <p className="mt-1 max-w-sm text-[11px] leading-5 text-muted-foreground">
            미리보기를 실행하면 최대 20개 샘플 행과 스키마를 확인합니다. 이
            작업은 Dataset version을 만들지 않습니다.
          </p>
        </div>
      ) : null}
      {!error && isRunning ? (
        <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> 원격 리소스를 읽는 중…
        </div>
      ) : null}
      {!error && result ? (
        <Tabs defaultValue="data" className="flex min-h-0 flex-1 flex-col">
          <TabsList variant="line" className="h-9 shrink-0 gap-4 border-b px-3">
            <TabsTrigger value="data" className="text-xs">
              데이터
              <span className="ml-1 text-[10px] text-muted-foreground">
                {result.rowCount}
              </span>
            </TabsTrigger>
            <TabsTrigger value="schema" className="text-xs">
              스키마
              <span className="ml-1 text-[10px] text-muted-foreground">
                {columns.length}
              </span>
            </TabsTrigger>
          </TabsList>
          <TabsContent value="data" className="mt-0 min-h-0 flex-1 overflow-auto">
            {renderPreviewTable(result.sampleRows, columns)}
          </TabsContent>
          <TabsContent value="schema" className="mt-0 min-h-0 flex-1 overflow-auto">
            {renderSchemaTable(result.sampleRows, columns, resource.primaryKey)}
          </TabsContent>
        </Tabs>
      ) : null}
      {result && Object.keys(result.networkEvidence).length > 0 ? (
        <PreviewNetworkEvidence evidence={result.networkEvidence} />
      ) : null}
      <div className="flex min-h-8 flex-wrap items-center gap-x-3 gap-y-1 border-t px-3 py-1.5 text-[10px] text-muted-foreground">
        <span>읽기 전용 · Dataset commit 없음</span>
        {requestId ? <span className="font-mono">request={requestId}</span> : null}
        {result ? (
          <span className="ml-auto font-mono">
            fingerprint={result.configFingerprint.slice(0, 18)}…
          </span>
        ) : null}
      </div>
    </main>
  );
}

function PreviewNetworkEvidence({
  evidence,
}: {
  evidence: Record<string, unknown>;
}) {
  const resources = readRecord(evidence.networkResources);
  const pageCount = readNumberField(evidence, "pageCount") ?? 1;
  return (
    <div
      data-testid="source-preview-network-evidence"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t bg-primary/[0.025] px-3 py-2 text-[10px]"
    >
      <span className="inline-flex items-center gap-1 font-semibold">
        <Network className="size-3 text-primary" /> 실제 미리보기 경로
      </span>
      <span>
        {readTextField(evidence, "origin") === "agent-proxy"
          ? "Agent proxy"
          : "Direct egress"}
      </span>
      <span>{readTextField(resources, "agentId") ?? "—"}</span>
      <span>{pageCount} pages</span>
      <span>
        ↑ {formatBytes(readNumberField(evidence, "bytesSent"))} · ↓{" "}
        {formatBytes(readNumberField(evidence, "bytesReceived"))}
      </span>
    </div>
  );
}

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function renderPreviewTable(
  rows: Array<Record<string, unknown>>,
  columns: readonly string[],
) {
  if (rows.length === 0 || columns.length === 0) {
    return (
      <div className="p-4 text-xs text-muted-foreground">
        표시할 샘플 행이 없습니다.
      </div>
    );
  }
  return (
    <table className="w-full min-w-max text-left text-[11px]">
      <thead className="sticky top-0 bg-muted/90 text-muted-foreground backdrop-blur">
        <tr>
          <th className="w-10 border-r px-2 py-2 text-right font-normal">#</th>
          {columns.map((column) => (
            <th key={column} className="border-r px-3 py-2 font-mono font-medium">
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="divide-y divide-border/60">
        {rows.map((row, index) => (
          <tr key={previewRowKey(row, index)} className="hover:bg-muted/30">
            <td className="border-r px-2 py-2 text-right text-muted-foreground">
              {index + 1}
            </td>
            {columns.map((column) => (
              <td
                key={column}
                className="max-w-72 truncate border-r px-3 py-2 font-mono"
                title={previewCell(row[column])}
              >
                {previewCell(row[column])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function renderSchemaTable(
  rows: Array<Record<string, unknown>>,
  columns: readonly string[],
  primaryKey: readonly string[],
) {
  return (
    <table className="w-full text-left text-[11px]">
      <thead className="bg-muted/40 text-muted-foreground">
        <tr>
          <th className="px-3 py-2 font-medium">컬럼</th>
          <th className="px-3 py-2 font-medium">추론 타입</th>
          <th className="px-3 py-2 font-medium">역할</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border/60">
        {columns.map((column) => (
          <tr key={column}>
            <td className="px-3 py-2 font-mono font-medium">{column}</td>
            <td className="px-3 py-2 font-mono text-muted-foreground">
              {inferColumnType(rows, column)}
            </td>
            <td className="px-3 py-2">
              {primaryKey.includes(column) ? (
                <span className="inline-flex items-center gap-1">
                  <KeyRound className="size-3 text-primary" /> primary key
                </span>
              ) : (
                <span className="text-muted-foreground">property</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function previewColumns(
  resource: ConnectorResource,
  result: ConnectorResourceTestResult | null,
): string[] {
  const schemaColumns = result?.schema.columns;
  if (Array.isArray(schemaColumns)) {
    const normalized = schemaColumns.filter(
      (column): column is string => typeof column === "string",
    );
    if (normalized.length > 0) return normalized;
  }
  if (resource.schemaColumns.length > 0) return resource.schemaColumns;
  return Object.keys(result?.sampleRows[0] ?? {});
}

function inferColumnType(
  rows: Array<Record<string, unknown>>,
  column: string,
): string {
  const value = rows.find((row) => row[column] !== null)?.[column];
  if (value === undefined || value === null) return "unknown";
  if (Array.isArray(value)) return "array";
  if (typeof value === "object") return "object";
  return typeof value;
}

function previewRowKey(row: Record<string, unknown>, index: number): string {
  const identity = row.id ?? row.key ?? row.name;
  return identity === undefined
    ? `rest-preview-${index}-${JSON.stringify(row).slice(0, 32)}`
    : String(identity);
}

function previewCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}
