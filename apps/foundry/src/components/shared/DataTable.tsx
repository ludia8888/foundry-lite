import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface DataTableColumn<T> {
  key: string;
  header: string;
  className?: string;
  isMono?: boolean;
  render: (row: T) => ReactNode;
}

interface DataTableProps<T> {
  columns: readonly DataTableColumn<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedKey?: string | null;
  emptyMessage?: string;
  className?: string;
}

/** Palantir식 컴팩트 테이블: 대문자 회색 헤더 + 헤어라인 구분선 + 선택 연파랑. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  emptyMessage = "표시할 항목이 없습니다.",
  className,
}: DataTableProps<T>) {
  return (
    <div className={cn("overflow-auto rounded border bg-card", className)}>
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr className="border-b">
            {columns.map((column) => (
              <th
                key={column.key}
                className={cn(
                  "section-label h-8 px-3 text-left align-middle",
                  column.className,
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="h-16 px-3 text-center text-xs text-muted-foreground"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const key = rowKey(row);
              const isSelected = selectedKey === key;
              return (
                <tr
                  key={key}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn(
                    "border-b last:border-b-0",
                    onRowClick && "cursor-pointer hover:bg-muted/60",
                    isSelected && "bg-accent hover:bg-accent",
                  )}
                >
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={cn(
                        "h-8 px-3 align-middle whitespace-nowrap",
                        column.isMono && "font-mono text-[11px]",
                        column.className,
                      )}
                    >
                      {column.render(row)}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
