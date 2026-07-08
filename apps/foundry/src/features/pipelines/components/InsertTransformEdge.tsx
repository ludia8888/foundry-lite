import type { PipelineNodeType } from "@foundry-lite/sdk";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getStraightPath,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { Plus } from "lucide-react";
import { useState } from "react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

import { NODE_TYPE_META } from "../pipeline-model";

export type InsertTransformEdgeData = {
  onInsertTransform: (edgeId: string, type: PipelineNodeType) => void;
};

export type PipelineFlowEdge = Edge<InsertTransformEdgeData, "insertEdge">;

const INSERTABLE_TYPES: readonly PipelineNodeType[] = [
  "sql",
  "python",
  "join",
  "union",
  "select_cast",
];

/**
 * 공식 그래프의 얇은 직선 엣지 + 중간 ⊕ 버튼 (클릭 시 두 노드 사이에 변환 삽입 메뉴).
 */
export function InsertTransformEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  selected,
}: EdgeProps<PipelineFlowEdge>) {
  const [isOpen, setIsOpen] = useState(false);
  const [edgePath, labelX, labelY] = getStraightPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
  });

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: selected ? "#5F6B7C" : "#8D99A6",
          strokeWidth: 1.5,
        }}
      />
      <EdgeLabelRenderer>
        <div
          className="nodrag nopan absolute"
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            pointerEvents: "all",
          }}
        >
          <Popover open={isOpen} onOpenChange={setIsOpen}>
            <PopoverTrigger asChild>
              <button
                type="button"
                title="두 노드 사이에 변환 삽입"
                className="flex size-[18px] items-center justify-center rounded-full border border-[#8D99A6] bg-white text-[#5F6B7C] shadow-sm transition-colors hover:bg-[#EFF3F6]"
              >
                <Plus className="size-3" strokeWidth={2.5} />
              </button>
            </PopoverTrigger>
            <PopoverContent align="center" side="bottom" className="w-44 p-1">
              <div className="px-2 py-1 text-[11px] font-semibold text-muted-foreground">
                변환 삽입
              </div>
              {INSERTABLE_TYPES.map((type) => (
                <button
                  key={type}
                  type="button"
                  className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-[12px] hover:bg-muted"
                  onClick={() => {
                    data?.onInsertTransform(id, type);
                    setIsOpen(false);
                  }}
                >
                  <span
                    className={`size-2.5 shrink-0 rounded-[2px] ${NODE_TYPE_META[type].headerClassName}`}
                  />
                  {NODE_TYPE_META[type].label}
                </button>
              ))}
            </PopoverContent>
          </Popover>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
