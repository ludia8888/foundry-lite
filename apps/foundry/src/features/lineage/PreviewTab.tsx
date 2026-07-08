import type { FoundryLiteDatasetExplorerState } from "@foundry-lite/sdk/react";
import { Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { Input } from "@/components/ui/input";

interface PreviewTabProps {
  explorer: FoundryLiteDatasetExplorerState;
}

interface IndexedRow {
  index: number;
  row: Record<string, unknown>;
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "∅";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** 하단 미리보기 탭: 컴팩트 행 + 모노 값 + 컬럼 검색 (dataset-preview.png 재현). */
export function PreviewTab({ explorer }: PreviewTabProps) {
  const [columnQuery, setColumnQuery] = useState("");

  const allColumnKeys = useMemo(
    () => Object.keys(explorer.previewRows[0] ?? {}),
    [explorer.previewRows],
  );
  const visibleColumnKeys = useMemo(() => {
    const normalized = columnQuery.trim().toLowerCase();
    if (!normalized) return allColumnKeys;
    return allColumnKeys.filter((key) =>
      key.toLowerCase().includes(normalized),
    );
  }, [allColumnKeys, columnQuery]);

  const columns: DataTableColumn<IndexedRow>[] = useMemo(
    () =>
      visibleColumnKeys.map((key) => ({
        key,
        header: key,
        isMono: true,
        render: (indexed: IndexedRow) => formatCell(indexed.row[key]),
      })),
    [visibleColumnKeys],
  );
  const rows: IndexedRow[] = useMemo(
    () => explorer.previewRows.map((row, index) => ({ index, row })),
    [explorer.previewRows],
  );

  if (!explorer.hasDatasetSelection) {
    return (
      <EmptyState
        icon={Table2}
        title="데이터셋을 선택하세요"
        description="그래프에서 데이터셋 노드를 선택하면 최신 버전 미리보기가 표시됩니다."
      />
    );
  }
  if (explorer.isLoading) return <LoadingState rowCount={5} />;
  if (explorer.error) {
    return (
      <ErrorState
        error={explorer.error}
        onRetry={() => void explorer.reload()}
      />
    );
  }

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center gap-3">
        <span className="font-mono text-[12px] font-semibold">
          {explorer.selectedDatasetRef}
        </span>
        <span className="text-[11px] text-muted-foreground">
          전체 {explorer.rowCount?.toLocaleString() ?? "?"}행 중{" "}
          {explorer.previewRows.length}행 표시 · 컬럼 {allColumnKeys.length}개
          {explorer.inspectedVersion
            ? ` · v${explorer.inspectedVersion.version_number}`
            : ""}
        </span>
        <div className="relative ml-auto">
          <Search className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={columnQuery}
            onChange={(event) => setColumnQuery(event.target.value)}
            placeholder="컬럼 검색…"
            className="h-7 w-48 pl-7 text-xs"
          />
        </div>
      </div>
      <DataTable
        className="min-h-0 flex-1"
        columns={columns}
        rows={rows}
        rowKey={(indexed) => String(indexed.index)}
        emptyMessage="미리보기 행이 없습니다."
      />
    </div>
  );
}
