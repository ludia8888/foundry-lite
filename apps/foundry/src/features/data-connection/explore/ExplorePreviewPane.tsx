import { Check, Loader2, Plus, Table2, X } from "lucide-react";

import { EmptyState } from "@/components/shared/EmptyState";
import { cn } from "@/lib/utils";

import type { ExploreTablePreview } from "../explore-model";
import { formatSampleCell } from "../explore-model";
import { SourceExplorationEvidenceLink } from "./SourceExplorationEvidenceLink";

interface ExplorePreviewPaneProps {
  openTables: readonly string[];
  activeTable: string | null;
  previewsByTable: Readonly<Record<string, ExploreTablePreview>>;
  selectedTables: readonly string[];
  onActivateTable: (tableName: string) => void;
  onCloseTable: (tableName: string) => void;
  onToggleSelect: (tableName: string) => void;
}

/**
 * 중앙 프리뷰 pane (공식 db-explorer 중앙): 선택 테이블 탭 스트립 +
 * "N행 미리보는 중" + 타입 서브라벨이 있는 모노 데이터 프리뷰.
 */
export function ExplorePreviewPane({
  openTables,
  activeTable,
  previewsByTable,
  selectedTables,
  onActivateTable,
  onCloseTable,
  onToggleSelect,
}: ExplorePreviewPaneProps) {
  const preview = activeTable ? (previewsByTable[activeTable] ?? null) : null;
  const isActiveSelected =
    activeTable !== null && selectedTables.includes(activeTable);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-9 shrink-0 items-center border-b bg-muted/30">
        <div className="flex min-w-0 flex-1 items-center self-stretch overflow-x-auto">
          {openTables.map((tableName) => (
            <PreviewTab
              key={tableName}
              tableName={tableName}
              isActive={tableName === activeTable}
              onActivate={() => onActivateTable(tableName)}
              onClose={() => onCloseTable(tableName)}
            />
          ))}
          {preview ? (
            <span className="shrink-0 px-3 text-[11px] whitespace-nowrap text-muted-foreground">
              {preview.status === "running"
                ? "미리보기 실행 중…"
                : `${preview.rows.length}행 미리보는 중 · 컬럼 ${preview.columns.length}개`}
            </span>
          ) : null}
        </div>
        {activeTable ? (
          <button
            type="button"
            className={cn(
              "mr-2 flex shrink-0 items-center gap-1 rounded px-2 py-1 text-[11px] font-medium",
              isActiveSelected
                ? "text-success"
                : "text-primary hover:bg-primary/10",
            )}
            onClick={() => onToggleSelect(activeTable)}
          >
            {isActiveSelected ? (
              <>
                <Check className="size-3" /> 동기화에 추가됨
              </>
            ) : (
              <>
                <Plus className="size-3" /> 동기화에 추가
              </>
            )}
          </button>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {activeTable === null ? (
          <EmptyState
            icon={Table2}
            title="테이블을 선택하세요"
            description="좌측 소스 미리보기에서 테이블을 선택하면 샘플 데이터를 미리 볼 수 있습니다."
          />
        ) : (
          <PreviewBody preview={preview} tableName={activeTable} />
        )}
      </div>
    </div>
  );
}

function PreviewTab({
  tableName,
  isActive,
  onActivate,
  onClose,
}: {
  tableName: string;
  isActive: boolean;
  onActivate: () => void;
  onClose: () => void;
}) {
  return (
    <div
      className={cn(
        "flex shrink-0 items-center gap-1 self-stretch border-r px-2",
        isActive
          ? "-mb-px border-b-2 border-b-primary bg-card"
          : "text-muted-foreground hover:bg-accent/50",
      )}
    >
      <button
        type="button"
        className="flex items-center gap-1.5"
        onClick={onActivate}
      >
        <Table2 className="size-3.5 text-primary" />
        <span
          className={cn("font-mono text-[11px]", isActive && "font-semibold")}
        >
          {tableName}
        </span>
      </button>
      <button
        type="button"
        className="flex size-4 items-center justify-center rounded text-muted-foreground hover:bg-accent"
        onClick={onClose}
        aria-label={`${tableName} 탭 닫기`}
      >
        <X className="size-3" />
      </button>
    </div>
  );
}

function PreviewBody({
  preview,
  tableName,
}: {
  preview: ExploreTablePreview | null;
  tableName: string;
}) {
  if (!preview || preview.status === "running") {
    return (
      <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> {tableName} 샘플을
        불러오는 중…
      </div>
    );
  }
  if (preview.status === "failed") {
    return (
      <div className="space-y-2 p-4 text-xs text-destructive">
        <div>
          미리보기에 실패했습니다: {preview.errorMessage ?? "알 수 없는 오류"}
        </div>
        <SourceExplorationEvidenceLink
          result={previewEvidenceResult(preview)}
          className="inline-block text-xs"
          label="실패 실행 조사"
        />
      </div>
    );
  }
  const columnNames =
    preview.columns.length > 0
      ? preview.columns.map((column) => column.name)
      : preview.rows.length > 0
        ? Object.keys(preview.rows[0])
        : [];
  const typeByName = new Map(
    preview.columns.map((column) => [column.name, column.type]),
  );

  if (preview.rows.length === 0) {
    return (
      <div className="space-y-2 p-4 text-xs text-muted-foreground">
        <div>샘플 행이 없습니다.</div>
        <SourceExplorationEvidenceLink result={previewEvidenceResult(preview)} />
      </div>
    );
  }

  return (
    <div>
      <div className="border-b px-3 py-2 text-[10px]">
        <SourceExplorationEvidenceLink
          result={previewEvidenceResult(preview)}
        />
      </div>
      <table className="w-full text-left">
      <thead className="sticky top-0 bg-muted/60 backdrop-blur">
        <tr className="border-b">
          <th className="w-8 px-2 py-1.5 text-right font-mono text-[10px] font-normal text-muted-foreground">
            #
          </th>
          {columnNames.map((name) => (
            <th key={name} className="px-2 py-1.5 align-top whitespace-nowrap">
              <div className="font-mono text-[11px] font-semibold">{name}</div>
              <div className="font-mono text-[10px] font-normal text-muted-foreground uppercase">
                {typeByName.get(name) ?? "unknown"}
              </div>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {preview.rows.map((row, index) => (
          <tr key={index} className="h-8 border-b last:border-0">
            <td className="px-2 py-1 text-right font-mono text-[10px] text-muted-foreground">
              {index + 1}
            </td>
            {columnNames.map((name) => (
              <td
                key={name}
                className="max-w-48 truncate px-2 py-1 font-mono text-[11px] whitespace-nowrap"
              >
                {formatSampleCell(row[name])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
      </table>
    </div>
  );
}

function previewEvidenceResult(preview: ExploreTablePreview) {
  return preview.explorationRunId
    ? {
        explorationRunId: preview.explorationRunId,
        operationsPath: preview.operationsPath,
      }
    : null;
}
