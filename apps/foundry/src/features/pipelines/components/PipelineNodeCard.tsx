import type { PipelineNode } from "@foundry-lite/sdk";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import {
  AlertCircle,
  Blend,
  Braces,
  Columns3,
  Combine,
  Database,
  Table2,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import { NODE_TYPE_META, nodeLabel } from "../pipeline-model";

export type PipelineFlowNodeData = {
  node: PipelineNode;
  errorCount: number;
};

export type PipelineFlowNode = Node<PipelineFlowNodeData, "pipelineNode">;

export const JOIN_TARGET_HANDLES = ["left", "right"] as const;

const NODE_ICONS: Record<string, LucideIcon> = {
  dataset: Table2,
  sql: Braces,
  python: Workflow,
  join: Blend,
  union: Combine,
  select_cast: Columns3,
  output_dataset: Database,
};

/** 입력 포트: 회색 채운 원 + 진회색 링 (노드 왼쪽 가장자리에 반쯤 겹침). */
const TARGET_HANDLE_CLASS =
  "!size-3 !border-2 !border-[#5F6B7C] !bg-[#8D99A6] !rounded-full";
/** 출력 포트: 흰 배경 + 회색 테두리 원. */
const SOURCE_HANDLE_CLASS =
  "!size-3 !border-2 !border-[#8D99A6] !bg-white !rounded-full";

/** 조인 노드 바디 행 높이/기준: 헤더 32px + 컬럼 행 24px + 좌/우 데이터셋 행 22px. */
const JOIN_LEFT_HANDLE_TOP = 32 + 24 + 11;
const JOIN_RIGHT_HANDLE_TOP = 32 + 24 + 22 + 11;

/**
 * Palantir식 노드 카드 (samplegraph-2x 대조):
 * - 소스: 연회색 타이틀 행(미니 테이블 아이콘 + 다크 볼드) + 구분선 + "N개 컬럼" 행.
 * - 변환: [연톤 아이콘 셀] + [뮤트 컬러 헤더 + 흰 볼드 타이틀] + 흰 바디.
 * - 조인: 민트 헤더 + 연보라 아이콘 셀, 바디에 좌/우 데이터셋 서브행 + 우측 '데이터셋' 출력 라벨.
 * - 출력: 전체 솔리드 블루 한 줄 + 좌측 흰 아이콘 셀.
 */
export function PipelineNodeCard({
  data,
  selected,
}: NodeProps<PipelineFlowNode>) {
  const { node, errorCount } = data;
  const meta = NODE_TYPE_META[node.type];
  const Icon = NODE_ICONS[node.type] ?? Table2;
  const columnCount = node.schema?.length ?? 0;
  const hasError = errorCount > 0;
  const isSource = node.type === "dataset";
  const isOutput = node.type === "output_dataset";
  const isJoin = node.type === "join";
  const columnText =
    columnCount > 0 ? `${columnCount}개 컬럼` : "스키마 미확정";

  return (
    <div
      className={cn(
        "w-[216px] rounded-[3px] border text-[12px] shadow-sm",
        isOutput ? "bg-[#147DB3]" : "bg-white",
        selected ? "ring-2 ring-primary/70" : null,
        hasError
          ? "border-destructive"
          : isOutput
            ? "border-[#9AA5B1]"
            : "border-[#D5DAE0]",
      )}
    >
      {/* 타깃 핸들: 조인은 좌/우 2개, 그 외 변환·출력은 1개 */}
      {!isSource && isJoin ? (
        <>
          <Handle
            id="left"
            type="target"
            position={Position.Left}
            style={{ top: JOIN_LEFT_HANDLE_TOP }}
            className={TARGET_HANDLE_CLASS}
          />
          <Handle
            id="right"
            type="target"
            position={Position.Left}
            style={{ top: JOIN_RIGHT_HANDLE_TOP }}
            className={TARGET_HANDLE_CLASS}
          />
        </>
      ) : null}
      {!isSource && !isJoin ? (
        <Handle
          type="target"
          position={Position.Left}
          className={TARGET_HANDLE_CLASS}
        />
      ) : null}
      {!isOutput ? (
        <Handle
          type="source"
          position={Position.Right}
          style={isJoin ? { top: JOIN_LEFT_HANDLE_TOP } : undefined}
          className={SOURCE_HANDLE_CLASS}
        />
      ) : null}

      {isSource ? (
        <div className="flex h-8 items-center gap-2 rounded-t-[2px] bg-[#F8F9FA] px-2.5">
          <Icon className={cn("size-4 shrink-0", meta.iconClassName)} />
          <span className="truncate text-[13px] font-bold text-[#1C2127]">
            {nodeLabel(node)}
          </span>
          {hasError ? (
            <AlertCircle className="ml-auto size-3.5 shrink-0 text-destructive" />
          ) : null}
        </div>
      ) : (
        <div
          className={cn(
            "flex h-8 items-stretch overflow-hidden",
            isOutput ? "rounded-[2px]" : "rounded-t-[2px]",
          )}
        >
          <div
            className={cn(
              "flex w-8 shrink-0 items-center justify-center",
              meta.iconCellClassName,
            )}
          >
            <Icon className={cn("size-4", meta.iconClassName)} />
          </div>
          <div
            className={cn(
              "flex min-w-0 flex-1 items-center gap-1.5 px-2",
              meta.headerClassName,
            )}
          >
            <span className="truncate text-[13px] font-bold">
              {nodeLabel(node)}
            </span>
            {hasError ? (
              <AlertCircle className="ml-auto size-3.5 shrink-0" />
            ) : null}
          </div>
        </div>
      )}

      {!isOutput ? (
        <div className="rounded-b-[2px] border-t border-[#E1E5EA] bg-[#F8F9FA]">
          <div className="flex h-6 items-center px-2.5 leading-none text-muted-foreground">
            {columnText}
          </div>
          {isJoin ? (
            <>
              <div className="flex h-[22px] items-center justify-between px-2.5 leading-none text-muted-foreground">
                <span>좌측 데이터셋</span>
                <span>데이터셋</span>
              </div>
              <div className="flex h-[22px] items-center px-2.5 leading-none text-muted-foreground">
                우측 데이터셋
              </div>
            </>
          ) : null}
          {hasError ? (
            <div className="px-2.5 pb-1.5 text-[11px] font-medium text-destructive">
              검증 오류 {errorCount}건
            </div>
          ) : (
            <div className="pb-1" />
          )}
        </div>
      ) : null}
    </div>
  );
}
