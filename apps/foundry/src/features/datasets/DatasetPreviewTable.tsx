import type { DatasetVersion, TabularRow } from "@foundry-lite/sdk";
import { Check, Filter, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import type { SchemaColumn } from "./dataset-schema";
import {
  formatCellValue,
  formatColumnType,
  formatRowCount,
} from "./dataset-schema";

interface DatasetPreviewTableProps {
  datasetName: string;
  rows: readonly TabularRow[];
  schemaColumns: readonly SchemaColumn[];
  totalRowCount: number | null;
  inspectedVersion: DatasetVersion | null;
}

interface PreviewColumn {
  name: string;
  type: string;
  isSystem: boolean;
  isNumeric: boolean;
}

type ValueFiltersByColumn = Readonly<Record<string, readonly string[]>>;

const NUMERIC_TYPE_PATTERN = /int|float|double|decimal|long|number/i;
const FILTER_VALUE_LIMIT = 50;

function buildPreviewColumns(
  rows: readonly TabularRow[],
  schemaColumns: readonly SchemaColumn[],
): PreviewColumn[] {
  const names = new Set(schemaColumns.map((column) => column.name));
  const systemNames =
    rows.length > 0
      ? Object.keys(rows[0]).filter((name) => !names.has(name))
      : [];
  return [
    ...schemaColumns.map((column) => ({
      name: column.name,
      type: column.type,
      isSystem: false,
      isNumeric: NUMERIC_TYPE_PATTERN.test(column.type),
    })),
    ...systemNames.map((name) => ({
      name,
      type: "system",
      isSystem: true,
      isNumeric: false,
    })),
  ];
}

function filterRows(
  rows: readonly TabularRow[],
  valueFiltersByColumn: ValueFiltersByColumn,
): readonly TabularRow[] {
  const activeFilters = Object.entries(valueFiltersByColumn).filter(
    ([, values]) => values.length > 0,
  );
  if (activeFilters.length === 0) return rows;
  return rows.filter((row) =>
    activeFilters.every(([columnName, values]) =>
      values.includes(formatCellValue(row[columnName])),
    ),
  );
}

function uniqueColumnValues(
  rows: readonly TabularRow[],
  columnName: string,
): string[] {
  const values = new Set<string>();
  for (const row of rows) {
    values.add(formatCellValue(row[columnName]));
    if (values.size >= FILTER_VALUE_LIMIT) break;
  }
  return [...values].sort();
}

/**
 * Palantir Foundry 프리뷰 테이블 문법:
 * [데이터셋명 + 행/컬럼 카운트 박스 + 컬럼 검색] 툴바 · 행 번호 거터 ·
 * 컬럼명(세미볼드)+타입 서브라벨+컬럼별 고유값 필터 헤더 · 헤어라인 행 구분선.
 * branch/version 시스템 컬럼은 기본 숨김("시스템 컬럼 표시" 토글).
 */
export function DatasetPreviewTable({
  datasetName,
  rows,
  schemaColumns,
  totalRowCount,
  inspectedVersion,
}: DatasetPreviewTableProps) {
  const [columnQuery, setColumnQuery] = useState("");
  const [valueFiltersByColumn, setValueFiltersByColumn] =
    useState<ValueFiltersByColumn>({});
  const [shouldShowSystemColumns, setShouldShowSystemColumns] = useState(false);

  const allColumns = useMemo(
    () => buildPreviewColumns(rows, schemaColumns),
    [rows, schemaColumns],
  );
  const dataColumnCount = allColumns.filter(
    (column) => !column.isSystem,
  ).length;

  const visibleColumns = useMemo(() => {
    const query = columnQuery.trim().toLowerCase();
    return allColumns.filter((column) => {
      if (column.isSystem && !shouldShowSystemColumns) return false;
      return query === "" || column.name.toLowerCase().includes(query);
    });
  }, [allColumns, columnQuery, shouldShowSystemColumns]);

  const filteredRows = useMemo(
    () => filterRows(rows, valueFiltersByColumn),
    [rows, valueFiltersByColumn],
  );

  const handleToggleFilterValue = (columnName: string, value: string) => {
    setValueFiltersByColumn((previous) => {
      const current = previous[columnName] ?? [];
      const next = current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value];
      return { ...previous, [columnName]: next };
    });
  };

  const handleClearFilter = (columnName: string) => {
    setValueFiltersByColumn((previous) => ({ ...previous, [columnName]: [] }));
  };

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Table2}
        title="프리뷰할 행이 없습니다"
        description="이 데이터셋 branch head에 커밋된 데이터가 없습니다. Data Connection 동기화 또는 Pipeline 실행으로 데이터를 적재하세요."
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-col rounded border bg-card">
      <PreviewToolbar
        datasetName={datasetName}
        visibleRowCount={filteredRows.length}
        totalRowCount={totalRowCount}
        columnCount={dataColumnCount}
        columnQuery={columnQuery}
        onColumnQueryChange={setColumnQuery}
        shouldShowSystemColumns={shouldShowSystemColumns}
        onToggleSystemColumns={() =>
          setShouldShowSystemColumns((previous) => !previous)
        }
      />

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead className="sticky top-0 z-10">
            <tr>
              <th className="sticky left-0 z-20 w-9 border-r border-b border-border/80 bg-background" />
              {visibleColumns.map((column) => (
                <PreviewColumnHeader
                  key={column.name}
                  column={column}
                  rows={rows}
                  selectedValues={valueFiltersByColumn[column.name] ?? []}
                  onToggleValue={(value) =>
                    handleToggleFilterValue(column.name, value)
                  }
                  onClearFilter={() => handleClearFilter(column.name)}
                />
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                <td className="sticky left-0 z-[5] h-5 w-9 border-r border-b border-border/80 bg-background pr-1.5 text-right font-mono text-[11px] text-muted-foreground">
                  {rowIndex + 1}
                </td>
                {visibleColumns.map((column) => {
                  const value = row[column.name];
                  const isNull = value === null || value === undefined;
                  return (
                    <td
                      key={column.name}
                      className={cn(
                        "h-5 border-r border-b border-border/80 px-2.5 whitespace-nowrap last:border-r-0",
                        column.isNumeric && "text-right tabular-nums",
                        isNull && "text-muted-foreground/60 italic",
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
            {filteredRows.length === 0 ? (
              <tr>
                <td
                  colSpan={visibleColumns.length + 1}
                  className="h-16 px-3 text-center text-xs text-muted-foreground"
                >
                  필터 조건에 맞는 행이 없습니다.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-3 py-1 font-mono text-[11px] text-muted-foreground">
        <span>
          미리보기 {rows.length}행 · 전체 {formatRowCount(totalRowCount)}행
        </span>
        {inspectedVersion ? (
          <span className="truncate" title={inspectedVersion.id}>
            version={inspectedVersion.id}
          </span>
        ) : null}
      </div>
    </div>
  );
}

interface PreviewToolbarProps {
  datasetName: string;
  visibleRowCount: number;
  totalRowCount: number | null;
  columnCount: number;
  columnQuery: string;
  onColumnQueryChange: (query: string) => void;
  shouldShowSystemColumns: boolean;
  onToggleSystemColumns: () => void;
}

/** 테이블 위 행: 데이터셋명(좌) · 행/컬럼 카운트 박스 + 시스템 컬럼 토글 + 컬럼 검색(우). */
function PreviewToolbar({
  datasetName,
  visibleRowCount,
  totalRowCount,
  columnCount,
  columnQuery,
  onColumnQueryChange,
  shouldShowSystemColumns,
  onToggleSystemColumns,
}: PreviewToolbarProps) {
  return (
    <div className="flex h-8 shrink-0 items-stretch border-b">
      <div className="flex min-w-0 flex-1 items-center gap-1.5 px-2.5">
        <Table2 className="size-3.5 shrink-0 text-primary" />
        <span className="truncate text-[13px] font-semibold">
          {datasetName}
        </span>
      </div>
      <div className="flex items-center border-l px-3 text-[12px] whitespace-nowrap text-muted-foreground">
        {visibleRowCount}행 표시 중 · 전체 {formatRowCount(totalRowCount)}행
      </div>
      <div className="flex items-center border-l px-3 text-[12px] whitespace-nowrap text-muted-foreground">
        {columnCount}개 컬럼
      </div>
      <button
        type="button"
        onClick={onToggleSystemColumns}
        className="flex items-center gap-1.5 border-l px-3 text-[12px] whitespace-nowrap text-muted-foreground hover:text-foreground"
      >
        <span
          className={cn(
            "flex size-3.5 items-center justify-center rounded-[2px] border",
            shouldShowSystemColumns &&
              "border-primary bg-primary text-primary-foreground",
          )}
        >
          {shouldShowSystemColumns ? <Check className="size-2.5" /> : null}
        </span>
        시스템 컬럼 표시
      </button>
      <div className="flex items-center gap-1.5 border-l px-2.5">
        <Search className="size-3.5 shrink-0 text-muted-foreground" />
        <input
          value={columnQuery}
          onChange={(event) => onColumnQueryChange(event.target.value)}
          placeholder="컬럼 검색..."
          className="w-32 bg-transparent text-[12px] outline-none placeholder:text-muted-foreground"
        />
      </div>
    </div>
  );
}

interface PreviewColumnHeaderProps {
  column: PreviewColumn;
  rows: readonly TabularRow[];
  selectedValues: readonly string[];
  onToggleValue: (value: string) => void;
  onClearFilter: () => void;
}

/** 컬럼 헤더 셀: 컬럼명(13px 세미볼드) + 타입 서브라벨(12px 회색) + 고유값 필터 popover. */
function PreviewColumnHeader({
  column,
  rows,
  selectedValues,
  onToggleValue,
  onClearFilter,
}: PreviewColumnHeaderProps) {
  const hasActiveFilter = selectedValues.length > 0;

  return (
    <th className="h-[42px] border-r border-b border-border/80 bg-background px-2.5 text-left align-middle whitespace-nowrap last:border-r-0">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-semibold text-muted-foreground">
          {column.name}
        </span>
        <ColumnFilterPopover
          columnName={column.name}
          rows={rows}
          selectedValues={selectedValues}
          hasActiveFilter={hasActiveFilter}
          onToggleValue={onToggleValue}
          onClearFilter={onClearFilter}
        />
      </div>
      <div className="text-[12px] leading-tight font-normal text-muted-foreground/70">
        {formatColumnType(column.type)}
      </div>
    </th>
  );
}

interface ColumnFilterPopoverProps {
  columnName: string;
  rows: readonly TabularRow[];
  selectedValues: readonly string[];
  hasActiveFilter: boolean;
  onToggleValue: (value: string) => void;
  onClearFilter: () => void;
}

function ColumnFilterPopover({
  columnName,
  rows,
  selectedValues,
  hasActiveFilter,
  onToggleValue,
  onClearFilter,
}: ColumnFilterPopoverProps) {
  const [isOpen, setIsOpen] = useState(false);
  const values = useMemo(
    () => (isOpen ? uniqueColumnValues(rows, columnName) : []),
    [isOpen, rows, columnName],
  );

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`${columnName} 컬럼 필터`}
          className={cn(
            "flex size-4 shrink-0 items-center justify-center rounded-[2px]",
            hasActiveFilter
              ? "bg-accent text-primary"
              : "bg-foreground/5 text-muted-foreground hover:bg-foreground/10",
          )}
        >
          <Filter className="size-2.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-52 p-0">
        <div className="flex items-center justify-between border-b px-2.5 py-1.5">
          <span className="text-[11px] font-semibold">
            {columnName} 고유값 필터
          </span>
          <button
            type="button"
            onClick={onClearFilter}
            disabled={!hasActiveFilter}
            className="text-[11px] text-primary hover:underline disabled:text-muted-foreground disabled:no-underline"
          >
            초기화
          </button>
        </div>
        <div className="max-h-56 overflow-auto py-1">
          {values.map((value) => {
            const isSelected = selectedValues.includes(value);
            return (
              <button
                key={value}
                type="button"
                onClick={() => onToggleValue(value)}
                className="flex h-6 w-full items-center gap-2 px-2.5 text-left text-[12px] hover:bg-muted/60"
              >
                <span
                  className={cn(
                    "flex size-3.5 shrink-0 items-center justify-center rounded-[2px] border",
                    isSelected &&
                      "border-primary bg-primary text-primary-foreground",
                  )}
                >
                  {isSelected ? <Check className="size-2.5" /> : null}
                </span>
                <span className="truncate font-normal" title={value}>
                  {value}
                </span>
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}
