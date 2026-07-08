import type { TabularRow } from "@foundry-lite/sdk";
import { Table2 } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import { PathBoard } from "./PathBoard";
import type { PreviewColumn } from "./analytics-model";
import { formatCellValue, MASKED_TOKEN } from "./analytics-model";

interface TableBoardProps {
  datasetRef: string;
  rows: readonly TabularRow[];
  columns: readonly PreviewColumn[];
  totalRows: number;
  shouldShowSystemColumns: boolean;
  onToggleSystemColumns: () => void;
}

/**
 * TABLE board: dataset preview 스프레드시트 그리드.
 * 행 번호 거터 + 컬럼명(세미볼드)+타입 서브라벨 헤더 + hairline 셀 구분선.
 * 재사용 출처: Dataset Explorer의 DatasetPreviewTable 프리뷰 문법.
 */
export function TableBoard({
  datasetRef,
  rows,
  columns,
  totalRows,
  shouldShowSystemColumns,
  onToggleSystemColumns,
}: TableBoardProps) {
  const visibleColumns = columns.filter(
    (column) => shouldShowSystemColumns || !column.isSystem,
  );
  const dataColumnCount = columns.filter((column) => !column.isSystem).length;

  return (
    <PathBoard
      icon={Table2}
      label="TABLE · 데이터 프리뷰"
      actions={
        <>
          <span className="font-mono text-[11px] text-muted-foreground">
            {rows.length}행 · {dataColumnCount}컬럼
          </span>
          <button
            type="button"
            onClick={onToggleSystemColumns}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <span
              className={cn(
                "flex size-3 items-center justify-center rounded-[2px] border text-[8px]",
                shouldShowSystemColumns &&
                  "border-primary bg-primary text-primary-foreground",
              )}
            >
              {shouldShowSystemColumns ? "✓" : ""}
            </span>
            시스템 컬럼
          </button>
        </>
      }
    >
      <div className="max-h-72 min-h-0 overflow-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead className="sticky top-0 z-10">
            <tr>
              <th className="sticky left-0 z-20 w-9 border-r border-b bg-background" />
              {visibleColumns.map((column) => (
                <th
                  key={column.name}
                  className="border-r border-b bg-background px-2.5 py-1 text-left align-top whitespace-nowrap last:border-r-0"
                >
                  <div className="flex items-center gap-1 text-[12px] font-semibold text-foreground/80">
                    {column.name}
                    {column.isMasked ? (
                      <StatusPill intent="warning" className="text-[9px]">
                        마스킹
                      </StatusPill>
                    ) : null}
                  </div>
                  <div className="text-[10.5px] leading-tight font-normal text-muted-foreground/70">
                    {column.isSystem
                      ? "system"
                      : column.isNumeric
                        ? "numeric"
                        : "string"}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                <td className="sticky left-0 z-[5] w-9 border-r border-b bg-background pr-1.5 text-right font-mono text-[11px] text-muted-foreground">
                  {rowIndex + 1}
                </td>
                {visibleColumns.map((column) => {
                  const value = row[column.name];
                  const isNull = value === null || value === undefined;
                  const isMasked = value === MASKED_TOKEN;
                  return (
                    <td
                      key={column.name}
                      className={cn(
                        "h-6 border-r border-b px-2.5 whitespace-nowrap last:border-r-0",
                        column.isNumeric && "text-right tabular-nums",
                        isNull && "text-muted-foreground/50 italic",
                        isMasked && "font-mono text-[11px] text-warning",
                        column.isSystem &&
                          "font-mono text-[11px] text-muted-foreground",
                      )}
                    >
                      {formatCellValue(value)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="border-t px-2.5 py-1 font-mono text-[11px] text-muted-foreground">
        {datasetRef} · 미리보기 {rows.length}행 (백엔드 datasets.preview limit)
        {totalRows > rows.length ? ` · 전체 ${totalRows}행 이상` : ""}
      </div>
    </PathBoard>
  );
}
