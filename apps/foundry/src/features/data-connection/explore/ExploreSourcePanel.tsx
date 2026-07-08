import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  PlusCircle,
  Search,
  Table2,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import type { ExploreTable } from "../explore-model";

interface ExploreSourcePanelProps {
  tables: ExploreTable[];
  explorationRunId: string | null;
  activeTable: string | null;
  selectedTables: readonly string[];
  onPreviewTable: (tableName: string) => void;
  onToggleSelect: (tableName: string) => void;
  onSelectTables: (tableNames: string[]) => void;
}

/**
 * 좌측 "소스 미리보기" 패널 (공식 db-explorer 좌측 pane):
 * 자유 텍스트 필터 + 테이블 트리 + 행별 ⊕ 동기화 선택 + 하단 "모두" 링크.
 */
export function ExploreSourcePanel({
  tables,
  explorationRunId,
  activeTable,
  selectedTables,
  onPreviewTable,
  onToggleSelect,
  onSelectTables,
}: ExploreSourcePanelProps) {
  const [filter, setFilter] = useState("");
  const [expandedTables, setExpandedTables] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const selectedSet = useMemo(() => new Set(selectedTables), [selectedTables]);
  const visibleTables = useMemo(() => {
    const query = filter.trim().toLowerCase();
    if (!query) return tables;
    return tables.filter((table) =>
      table.tableName.toLowerCase().includes(query),
    );
  }, [tables, filter]);

  const handleToggleExpand = (tableName: string) => {
    setExpandedTables((current) => {
      const next = new Set(current);
      if (next.has(tableName)) next.delete(tableName);
      else next.add(tableName);
      return next;
    });
  };

  return (
    <div className="flex w-64 shrink-0 flex-col border-r">
      <div className="border-b px-3 py-2">
        <div className="text-[13px] font-semibold">소스 미리보기</div>
        <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
          이 테이블 리소스들은 Foundry로 동기화할 수 있는 항목의 미리보기입니다.
        </p>
      </div>
      <div className="border-b p-2">
        <div className="relative">
          <Search className="absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="이름으로 필터..."
            className="h-7 pl-7 text-xs"
          />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {visibleTables.length === 0 ? (
          <div className="px-3 py-2 text-[11px] text-muted-foreground">
            일치하는 테이블이 없습니다.
          </div>
        ) : (
          visibleTables.map((table) => (
            <SourceTreeRow
              key={table.tableName}
              table={table}
              isActive={table.tableName === activeTable}
              isSelected={selectedSet.has(table.tableName)}
              isExpanded={expandedTables.has(table.tableName)}
              onToggleExpand={() => handleToggleExpand(table.tableName)}
              onPreview={() => onPreviewTable(table.tableName)}
              onToggleSelect={() => onToggleSelect(table.tableName)}
            />
          ))
        )}
      </div>
      <div className="flex items-center justify-between border-t px-3 py-2 text-[11px]">
        <span className="text-muted-foreground">
          테이블 {tables.length}개 · 선택 {selectedTables.length}개
        </span>
        <button
          type="button"
          className="font-medium text-primary hover:underline"
          onClick={() =>
            onSelectTables(visibleTables.map((table) => table.tableName))
          }
        >
          모두
        </button>
      </div>
      {explorationRunId ? (
        <div className="border-t px-3 py-1.5">
          <div className="truncate font-mono text-[10px] text-muted-foreground">
            run={explorationRunId}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SourceTreeRow({
  table,
  isActive,
  isSelected,
  isExpanded,
  onToggleExpand,
  onPreview,
  onToggleSelect,
}: {
  table: ExploreTable;
  isActive: boolean;
  isSelected: boolean;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onPreview: () => void;
  onToggleSelect: () => void;
}) {
  return (
    <div>
      <div
        className={cn(
          "flex h-8 items-center gap-1 px-2",
          isActive ? "bg-accent" : "hover:bg-accent/50",
        )}
      >
        <button
          type="button"
          className="flex size-5 shrink-0 items-center justify-center text-muted-foreground"
          onClick={onToggleExpand}
          aria-label={`${table.tableName} 컬럼 ${isExpanded ? "접기" : "펼치기"}`}
        >
          {isExpanded ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
        </button>
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
          onClick={onPreview}
        >
          <Table2 className="size-3.5 shrink-0 text-primary" />
          <span
            className={cn(
              "min-w-0 truncate font-mono text-[11px]",
              isActive && "font-semibold",
            )}
          >
            {table.tableName}
          </span>
        </button>
        <button
          type="button"
          className={cn(
            "flex size-5 shrink-0 items-center justify-center",
            isSelected
              ? "text-primary"
              : "text-muted-foreground/60 hover:text-primary",
          )}
          onClick={onToggleSelect}
          aria-label={
            isSelected
              ? `${table.tableName} 동기화 선택 해제`
              : `${table.tableName} 동기화에 추가`
          }
        >
          {isSelected ? (
            <CheckCircle2 className="size-4" />
          ) : (
            <PlusCircle className="size-4" />
          )}
        </button>
      </div>
      {isExpanded ? (
        <ul className="ml-[18px] border-l pb-1">
          {table.columns.map((column) => (
            <li
              key={column.name}
              className="flex items-baseline gap-2 py-0.5 pr-2 pl-4"
            >
              <span className="min-w-0 truncate font-mono text-[11px]">
                {column.name}
              </span>
              <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground">
                {column.type}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
