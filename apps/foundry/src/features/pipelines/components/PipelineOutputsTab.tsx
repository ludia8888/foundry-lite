import type { PipelineNode } from "@foundry-lite/sdk";
import { ChevronsLeft, ChevronsRight, Database } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

import { nodeDatasetRef, nodeLabel } from "../pipeline-model";

interface PipelineOutputsTabProps {
  outputNodes: readonly PipelineNode[];
  selectedNodeIds: readonly string[];
  onFocusNode: (nodeId: string) => void;
}

/**
 * 우측 가장자리 세로 '파이프라인 출력' 접이식 탭 (공식 Pipeline outputs 탭).
 * 펼치면 출력 노드 목록 — 클릭 시 해당 노드 선택·포커스.
 */
export function PipelineOutputsTab({
  outputNodes,
  selectedNodeIds,
  onFocusNode,
}: PipelineOutputsTabProps) {
  const [isOpen, setIsOpen] = useState(false);

  if (!isOpen) {
    return (
      <button
        type="button"
        className="flex w-7 shrink-0 flex-col items-center gap-2 border-l bg-card pt-2 hover:bg-muted/50"
        title="파이프라인 출력 열기"
        onClick={() => setIsOpen(true)}
      >
        <ChevronsLeft className="size-3.5 text-muted-foreground" />
        <span
          className="text-[12px] font-semibold text-foreground"
          style={{ writingMode: "vertical-rl" }}
        >
          파이프라인 출력
        </span>
      </button>
    );
  }

  return (
    <div className="flex w-60 shrink-0 flex-col border-l bg-card">
      <div className="flex h-8 shrink-0 items-center justify-between border-b px-2.5">
        <span className="text-[12px] font-semibold">파이프라인 출력</span>
        <button
          type="button"
          className="flex size-5 items-center justify-center rounded text-muted-foreground hover:bg-muted"
          title="파이프라인 출력 접기"
          onClick={() => setIsOpen(false)}
        >
          <ChevronsRight className="size-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {outputNodes.length === 0 ? (
          <p className="px-2 py-3 text-[11px] text-muted-foreground">
            출력 노드가 없습니다. 툴바의 '출력 추가'로 데이터셋 출력을
            연결하세요.
          </p>
        ) : (
          outputNodes.map((node) => {
            const datasetRef = nodeDatasetRef(node);
            const columnCount = node.schema?.length ?? 0;
            return (
              <button
                key={node.id}
                type="button"
                className={cn(
                  "flex w-full flex-col gap-0.5 rounded border px-2 py-1.5 text-left transition-colors",
                  selectedNodeIds.includes(node.id)
                    ? "border-primary/60 bg-primary/5"
                    : "border-transparent hover:bg-muted/60",
                )}
                onClick={() => onFocusNode(node.id)}
              >
                <span className="flex items-center gap-1.5 text-[12px] font-semibold">
                  <Database className="size-3.5 shrink-0 text-[#147DB3]" />
                  <span className="truncate">{nodeLabel(node)}</span>
                </span>
                {datasetRef ? (
                  <span className="truncate font-mono text-[10px] text-muted-foreground">
                    {datasetRef}
                  </span>
                ) : null}
                <span className="text-[11px] text-muted-foreground">
                  {columnCount > 0 ? `${columnCount}개 컬럼` : "스키마 미확정"}
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
