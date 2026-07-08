import { Filter, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { PathBoard } from "./PathBoard";
import type { FilterCondition, PreviewColumn } from "./analytics-model";
import { FILTER_OPERATORS } from "./analytics-model";

interface FilterBoardProps {
  columns: readonly PreviewColumn[];
  conditions: readonly FilterCondition[];
  keptRows: number;
  totalRows: number;
  onAddCondition: () => void;
  onUpdateCondition: (id: string, patch: Partial<FilterCondition>) => void;
  onRemoveCondition: (id: string) => void;
}

/**
 * FILTER board: "Keep rows where [컬럼] [연산] [값]" 조건 스택 (AND).
 * 재사용 출처: Contour boards-filter의 keep-rows 시맨틱 + Dataset Explorer 컬럼 필터.
 */
export function FilterBoard({
  columns,
  conditions,
  keptRows,
  totalRows,
  onAddCondition,
  onUpdateCondition,
  onRemoveCondition,
}: FilterBoardProps) {
  const filterableColumns = columns.filter((column) => !column.isSystem);
  const removedRows = totalRows - keptRows;

  return (
    <PathBoard
      icon={Filter}
      label="FILTER · Keep rows"
      isActive={conditions.length > 0}
      actions={
        <span className="font-mono text-[11px] text-muted-foreground">
          {keptRows}/{totalRows}행 유지
        </span>
      }
    >
      <div className="space-y-1.5 p-2.5">
        {conditions.length === 0 ? (
          <p className="text-[12px] text-muted-foreground">
            조건이 없습니다. 모든 {totalRows}행이 유지됩니다.
          </p>
        ) : (
          conditions.map((condition, index) => (
            <div
              key={condition.id}
              className="flex items-center gap-1.5 text-[12px]"
            >
              <span className="w-14 shrink-0 text-[11px] font-medium text-primary">
                {index === 0 ? "Keep rows" : "AND"}
              </span>
              <span className="text-muted-foreground">where</span>
              <Select
                value={condition.column}
                onValueChange={(column) =>
                  onUpdateCondition(condition.id, { column })
                }
              >
                <SelectTrigger size="sm" className="h-7 w-36 text-[12px]">
                  <SelectValue placeholder="컬럼" />
                </SelectTrigger>
                <SelectContent>
                  {filterableColumns.map((column) => (
                    <SelectItem
                      key={column.name}
                      value={column.name}
                      className="text-[12px]"
                    >
                      {column.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={condition.operator}
                onValueChange={(operator) =>
                  onUpdateCondition(condition.id, {
                    operator: operator as FilterCondition["operator"],
                  })
                }
              >
                <SelectTrigger size="sm" className="h-7 w-20 text-[12px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FILTER_OPERATORS.map((operator) => (
                    <SelectItem
                      key={operator.id}
                      value={operator.id}
                      className="text-[12px]"
                    >
                      {operator.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                value={condition.value}
                onChange={(event) =>
                  onUpdateCondition(condition.id, {
                    value: event.target.value,
                  })
                }
                placeholder="값"
                className="h-7 w-32 text-[12px]"
              />
              <button
                type="button"
                aria-label="조건 제거"
                onClick={() => onRemoveCondition(condition.id)}
                className="flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-destructive"
              >
                <X className="size-3.5" />
              </button>
            </div>
          ))
        )}

        <div className="flex items-center justify-between pt-1">
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-[12px]"
            onClick={onAddCondition}
            disabled={filterableColumns.length === 0}
          >
            <Plus className="size-3.5" />
            조건 추가
          </Button>
          {conditions.length > 0 ? (
            <span className="font-mono text-[11px] text-muted-foreground">
              {removedRows}행 제외됨
            </span>
          ) : null}
        </div>
      </div>
    </PathBoard>
  );
}
