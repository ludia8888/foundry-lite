import type { Node, NodeProps } from "@xyflow/react";
import { Handle, Position } from "@xyflow/react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  PlusCircle,
  Table2,
} from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

import type { ExploreColumn } from "../explore-model";

export type ExploreFlowNode = Node<
  {
    tableName: string;
    columns: ExploreColumn[];
    fkColumnNames: string[];
    isSelected: boolean;
    isActive: boolean;
    onToggleSelect: (tableName: string) => void;
  },
  "exploreTable"
>;

/**
 * 그래프 뷰 테이블 노드 (공식 db-explorer): 테이블명 + 동기화 선택 배지 +
 * 확장형 컬럼 목록 (FK 컬럼은 오렌지 강조).
 */
export function ExploreTableNode({ data }: NodeProps<ExploreFlowNode>) {
  const [isExpanded, setIsExpanded] = useState(false);
  const fkNames = new Set(data.fkColumnNames);

  return (
    <div
      className={cn(
        "w-52 rounded border bg-card text-left shadow-sm",
        data.isActive ? "border-primary" : "border-border",
      )}
    >
      <Handle type="target" position={Position.Left} className="!opacity-0" />
      <Handle type="source" position={Position.Right} className="!opacity-0" />
      <div className="flex items-center gap-1.5 border-b px-2 py-1.5">
        <span className="flex size-5 shrink-0 items-center justify-center rounded bg-primary/10">
          <Table2 className="size-3 text-primary" />
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] font-semibold">
          {data.tableName}
        </span>
        <button
          type="button"
          className={cn(
            "flex size-4 shrink-0 items-center justify-center",
            data.isSelected
              ? "text-primary"
              : "text-muted-foreground/60 hover:text-primary",
          )}
          onClick={(event) => {
            event.stopPropagation();
            data.onToggleSelect(data.tableName);
          }}
          aria-label={
            data.isSelected
              ? `${data.tableName} 동기화 선택 해제`
              : `${data.tableName} 동기화에 추가`
          }
        >
          {data.isSelected ? (
            <CheckCircle2 className="size-3.5" />
          ) : (
            <PlusCircle className="size-3.5" />
          )}
        </button>
      </div>
      {isExpanded ? (
        <ul className="max-h-36 overflow-y-auto py-1">
          {data.columns.map((column) => (
            <li
              key={column.name}
              className="flex items-baseline gap-2 px-2 py-0.5"
            >
              <span
                className={cn(
                  "min-w-0 truncate font-mono text-[10px]",
                  fkNames.has(column.name) && "font-semibold text-warning",
                )}
              >
                {column.name}
              </span>
              <span className="ml-auto shrink-0 font-mono text-[9px] text-muted-foreground uppercase">
                {column.type}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      <button
        type="button"
        className="flex w-full items-center justify-between px-2 py-1 text-[10px] text-muted-foreground hover:bg-accent/50"
        onClick={(event) => {
          event.stopPropagation();
          setIsExpanded((current) => !current);
        }}
      >
        <span>컬럼 {data.columns.length}개</span>
        {isExpanded ? (
          <ChevronUp className="size-3" />
        ) : (
          <ChevronDown className="size-3" />
        )}
      </button>
    </div>
  );
}
