import {
  Boxes,
  ChevronsLeft,
  ChevronsRight,
  Database,
  FileStack,
  Network,
  Plus,
  Search,
  TableProperties,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";

import {
  StatusPill,
  type StatusIntent,
} from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import {
  nodeDataOf,
  nodeLabel,
  type PipelineCanvasNode,
} from "../pipeline-model";

interface PipelineOutputsTabProps {
  outputNodes: readonly PipelineCanvasNode[];
  selectedNodeIds: readonly string[];
  onFocusNode: (nodeId: string) => void;
  onAddOutput: () => void;
}

type OutputPlane = "outputs" | "objects" | "links";

const OUTPUT_PLANE_LABELS: Record<OutputPlane, string> = {
  outputs: "출력 노드",
  objects: "객체 타입",
  links: "링크 타입",
};

type OutputPresentation = {
  label: string;
  contract: string;
  icon: LucideIcon;
};

const OUTPUT_PRESENTATIONS: Record<string, OutputPresentation> = {
  "output.dataset": {
    label: "Dataset",
    contract: "immutable Dataset version",
    icon: Database,
  },
  "output.media_set": {
    label: "Media Set",
    contract: "immutable committed Media Set selection",
    icon: FileStack,
  },
  "output.semantic_index": {
    label: "Semantic index",
    contract: "vector index generation",
    icon: Search,
  },
  "output.virtual_table": {
    label: "Virtual table",
    contract: "virtual table reference",
    icon: TableProperties,
  },
  "output.ontology": {
    label: "Ontology mapping",
    contract: "governed ontology mapping candidate",
    icon: Network,
  },
};

/**
 * 우측 가장자리 세로 '파이프라인 출력' 접이식 탭 (공식 Pipeline outputs 탭).
 * 펼치면 출력 노드 목록 — 클릭 시 해당 노드 선택·포커스.
 */
export function PipelineOutputsTab({
  outputNodes,
  selectedNodeIds,
  onFocusNode,
  onAddOutput,
}: PipelineOutputsTabProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [activePlane, setActivePlane] = useState<OutputPlane>("outputs");

  if (!isOpen) {
    return (
      <button
        type="button"
        aria-label="파이프라인 출력 열기"
        className="flex w-8 shrink-0 flex-col items-center gap-2 border-l border-[#C5CBD3] bg-card pt-2 hover:bg-muted/50"
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
    <section
      className="flex w-72 shrink-0 flex-col border-l border-[#C5CBD3] bg-card"
      aria-label="파이프라인 출력"
    >
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-[#C5CBD3] px-2.5">
        <span className="text-[13px] font-semibold">파이프라인 출력</span>
        <button
          type="button"
          aria-label="파이프라인 출력 접기"
          className="flex size-6 items-center justify-center text-muted-foreground hover:bg-muted"
          title="파이프라인 출력 접기"
          onClick={() => setIsOpen(false)}
        >
          <ChevronsRight className="size-3.5" />
        </button>
      </div>
      <div
        role="tablist"
        aria-label="출력 종류"
        className="grid h-9 shrink-0 grid-cols-3 border-b border-[#C5CBD3]"
      >
        {(Object.keys(OUTPUT_PLANE_LABELS) as OutputPlane[]).map((plane) => (
          <button
            key={plane}
            type="button"
            role="tab"
            aria-selected={activePlane === plane}
            className={cn(
              "border-b-2 px-1 text-[11px]",
              activePlane === plane
                ? "border-primary bg-[#F4F8FD] font-semibold text-primary"
                : "border-transparent text-muted-foreground hover:bg-muted/50",
            )}
            onClick={() => setActivePlane(plane)}
          >
            {OUTPUT_PLANE_LABELS[plane]}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {activePlane === "outputs" ? (
          <ConfiguredOutputs
            outputNodes={outputNodes}
            selectedNodeIds={selectedNodeIds}
            onFocusNode={onFocusNode}
          />
        ) : (
          <UnavailableOutputPlane plane={activePlane} />
        )}
      </div>
      <button
        type="button"
        className="flex h-9 shrink-0 items-center justify-center gap-1.5 border-t border-[#C5CBD3] text-[12px] font-semibold text-primary hover:bg-[#EEF5FD]"
        onClick={onAddOutput}
      >
        <Plus className="size-3.5" />
        출력 추가
      </button>
    </section>
  );
}

function ConfiguredOutputs({
  outputNodes,
  selectedNodeIds,
  onFocusNode,
}: Omit<PipelineOutputsTabProps, "onAddOutput">) {
  if (outputNodes.length === 0) {
    return (
      <p className="px-3 py-4 text-[11px] leading-5 text-muted-foreground">
        출력 노드가 없습니다. 아래의 <strong>출력 추가</strong>에서
        Dataset, Media Set, Index 등 필요한 출력 계약을 선택하세요.
      </p>
    );
  }
  return (
    <div className="divide-y divide-[#E1E5EA]">
      {outputNodes.map((node) => {
        const presentation = outputPresentation(node);
        const OutputIcon = presentation.icon;
        const outputRef = configuredOutputRef(node);
        const capability = outputCapability(node);
        const columnCount = node.schema?.length ?? 0;
        return (
          <button
            key={node.id}
            type="button"
            className={cn(
              "flex w-full flex-col gap-1 border-l-2 px-3 py-2 text-left",
              selectedNodeIds.includes(node.id)
                ? "border-l-primary bg-[#EEF5FD]"
                : "border-l-transparent hover:bg-muted/40",
            )}
            onClick={() => onFocusNode(node.id)}
          >
            <span className="flex w-full items-center gap-1.5 text-[12px] font-semibold">
              <OutputIcon className="size-3.5 shrink-0 text-[#147DB3]" />
              <span className="truncate">{nodeLabel(node)}</span>
              <StatusPill intent={capability.intent} className="ml-auto">
                {capability.label}
              </StatusPill>
            </span>
            <span className="text-[10px] text-muted-foreground">
              {presentation.label} · {presentation.contract}
            </span>
            {outputRef ? (
              <span className="truncate font-mono text-[10px] text-muted-foreground">
                {outputRef}
              </span>
            ) : null}
            <span className="text-[10px] text-muted-foreground">
              {node.descriptorId === "output.dataset"
                ? columnCount > 0
                  ? `출력 스키마 · ${columnCount}개 컬럼`
                  : "출력 스키마 미확정"
                : `${node.descriptorId}@${node.specVersion}`}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function outputPresentation(node: PipelineCanvasNode): OutputPresentation {
  return (
    OUTPUT_PRESENTATIONS[node.descriptorId] ?? {
      label: node.descriptorId,
      contract: "server-owned output contract",
      icon: Boxes,
    }
  );
}

function configuredOutputRef(node: PipelineCanvasNode): string | null {
  const data = nodeDataOf(node);
  for (const key of [
    "outputDatasetRef",
    "mediaSetRef",
    "indexRef",
    "virtualTableRef",
    "mappingRef",
    "resourceRef",
  ]) {
    const value = data[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

function outputCapability(node: PipelineCanvasNode): {
  label: string;
  intent: StatusIntent;
} {
  if (node.isReadOnly) return { label: "읽기 전용", intent: "neutral" };
  if (node.descriptor?.availability === "validation_only") {
    return { label: "검증 전용", intent: "warning" };
  }
  if (
    node.descriptor?.availability === "legacy_executable" ||
    node.descriptorId === "output.dataset"
  ) {
    return { label: "실행 가능", intent: "success" };
  }
  return { label: "상태 미확인", intent: "neutral" };
}

function UnavailableOutputPlane({
  plane,
}: {
  plane: Exclude<OutputPlane, "outputs">;
}) {
  return (
    <div className="px-3 py-4 text-[11px] leading-5 text-muted-foreground">
      <p className="font-semibold text-foreground">
        {OUTPUT_PLANE_LABELS[plane]} 출력
      </p>
      <p className="mt-1">
        서버 descriptor에는 계약이 표시되지만 현재 v1 캔버스에서는 아직
        authoring할 수 없습니다. 카탈로그에서 정확한 지원 상태와 이유를
        확인할 수 있습니다.
      </p>
    </div>
  );
}
