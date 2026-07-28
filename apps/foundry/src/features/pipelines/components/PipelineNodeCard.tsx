import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import {
  AlertCircle,
  ArrowRightLeft,
  Blend,
  Braces,
  Columns3,
  Combine,
  Database,
  Eye,
  FileSearch,
  FileText,
  Images,
  LockKeyhole,
  Network,
  Scissors,
  Search,
  Sparkles,
  Table2,
  TableProperties,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import {
  NODE_TYPE_META,
  nodeInputPorts,
  nodeLabel,
  nodeOutputPorts,
  type PipelineCanvasNode,
} from "../pipeline-model";

export type PipelineFlowNodeData = {
  node: PipelineCanvasNode;
  errorCount: number;
};

export type PipelineFlowNode = Node<PipelineFlowNodeData, "pipelineNode">;

export const JOIN_TARGET_HANDLES = ["left", "right"] as const;

const NODE_ICONS: Record<string, LucideIcon> = {
  unknown_v2: LockKeyhole,
  dataset: Table2,
  sql: Braces,
  python: Workflow,
  join: Blend,
  union: Combine,
  select_cast: Columns3,
  output_dataset: Database,
  source_media_set: Images,
  media_transform: Images,
  document_extract: FileSearch,
  chunk: Scissors,
  embedding_text: Search,
  embedding_vision: Eye,
  media_to_table_rows: ArrowRightLeft,
  content_units_to_dataset: ArrowRightLeft,
  use_llm: Sparkles,
  output_media_set: Images,
  output_virtual_table: TableProperties,
  output_semantic_index: FileText,
  output_ontology: Network,
};

/** 입력 포트: 회색 채운 원 + 진회색 링 (노드 왼쪽 가장자리에 반쯤 겹침). */
const TARGET_HANDLE_CLASS =
  "!size-3 !border-2 !border-[#5F6B7C] !bg-[#8D99A6] !rounded-full";
/** 출력 포트: 흰 배경 + 회색 테두리 원. */
const SOURCE_HANDLE_CLASS =
  "!size-3 !border-2 !border-[#8D99A6] !bg-white !rounded-full";

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
  const isSource = node.kind === "source";
  const isOutput = node.kind === "output";
  const isReadOnly = node.isReadOnly === true;
  const isJoin = node.type === "join";
  const inputPorts = nodeInputPorts(node);
  const outputPorts = nodeOutputPorts(node);
  const maximumPortCount = Math.max(inputPorts.length, outputPorts.length);
  const columnText =
    columnCount > 0 ? `${columnCount}개 컬럼` : "스키마 미확정";

  return (
    <div
      data-read-only={isReadOnly ? "true" : "false"}
      style={
        maximumPortCount > 3
          ? { minHeight: `${maximumPortCount * 18 + 20}px` }
          : undefined
      }
      title={
        isReadOnly
          ? `${node.descriptorId}@${node.specVersion} · 읽기 전용으로 보존됨`
          : undefined
      }
      className={cn(
        "w-44 rounded-[2px] border text-[11px] shadow-[0_1px_3px_rgb(17_20_24/0.22)]",
        isOutput ? "bg-[#147DB3]" : "bg-white",
        isReadOnly ? "border-dashed" : null,
        selected ? "ring-2 ring-primary/70" : null,
        hasError
          ? "border-destructive"
          : isOutput
            ? "border-[#9AA5B1]"
            : "border-[#D5DAE0]",
      )}
    >
      {inputPorts.map((port, index) => (
        <Handle
          key={`input-${port}`}
          id={port}
          type="target"
          position={Position.Left}
          style={{ top: handleTop(index, inputPorts.length) }}
          className={TARGET_HANDLE_CLASS}
          isConnectable={!isReadOnly}
        />
      ))}
      {!isOutput
        ? outputPorts.map((port, index) => (
            <Handle
              key={`output-${port}`}
              id={port}
              type="source"
              position={Position.Right}
              style={{ top: handleTop(index, outputPorts.length) }}
              className={SOURCE_HANDLE_CLASS}
              isConnectable={!isReadOnly}
            />
          ))
        : null}

      {isSource ? (
        <div className="flex h-7 items-center gap-1.5 rounded-t-[1px] bg-[#F8F9FA] px-2">
          <Icon className={cn("size-3.5 shrink-0", meta.iconClassName)} />
          <span className="truncate text-[12px] font-bold text-[#1C2127]">
            {nodeLabel(node)}
          </span>
          {isReadOnly ? (
            <LockKeyhole className="ml-auto size-3.5 shrink-0 text-[#5F6B7C]" />
          ) : null}
          {hasError ? (
            <AlertCircle
              className={cn(
                "size-3.5 shrink-0 text-destructive",
                isReadOnly ? null : "ml-auto",
              )}
            />
          ) : null}
        </div>
      ) : (
        <div
          className={cn(
            "flex h-7 items-stretch overflow-hidden",
            isOutput ? "rounded-[1px]" : "rounded-t-[1px]",
          )}
        >
          <div
            className={cn(
              "flex w-7 shrink-0 items-center justify-center",
              meta.iconCellClassName,
            )}
          >
            <Icon className={cn("size-3.5", meta.iconClassName)} />
          </div>
          <div
            className={cn(
              "flex min-w-0 flex-1 items-center gap-1.5 px-2",
              meta.headerClassName,
            )}
          >
            <span className="truncate text-[12px] font-bold">
              {nodeLabel(node)}
            </span>
            {isReadOnly ? (
              <LockKeyhole className="ml-auto size-3.5 shrink-0" />
            ) : null}
            {hasError ? (
              <AlertCircle
                className={cn(
                  "size-3.5 shrink-0",
                  isReadOnly ? null : "ml-auto",
                )}
              />
            ) : null}
          </div>
        </div>
      )}

      {!isOutput ? (
        <div className="rounded-b-[1px] border-t border-[#E1E5EA] bg-[#F8F9FA]">
          <div className="flex h-6 items-center px-2 leading-none text-muted-foreground">
            {columnText}
          </div>
          <div className="flex min-h-5 items-center gap-1 border-t border-[#E1E5EA] px-2 py-1 font-mono text-[9px] text-[#5F6B7C]">
            {inputPorts.length > 0 ? (
              <span className="truncate">{inputPorts.join(" + ")}</span>
            ) : (
              <span>source</span>
            )}
            <span className="ml-auto text-[#98A2AD]">→</span>
            <span className="truncate">{outputPorts.join(" + ")}</span>
          </div>
          {isReadOnly ? (
            <div className="border-t border-[#D5DAE0] bg-[#FFF8E7] px-2 py-1 text-[9px] font-medium text-[#725B20]">
              read-only · 저장 시 원본 계약 보존
            </div>
          ) : null}
          {isJoin ? (
            <>
              <div className="flex h-[22px] items-center justify-between px-2 leading-none text-muted-foreground">
                <span>좌측 데이터셋</span>
                <span>데이터셋</span>
              </div>
              <div className="flex h-[22px] items-center px-2 leading-none text-muted-foreground">
                우측 데이터셋
              </div>
            </>
          ) : null}
          {hasError ? (
            <div className="px-2 pb-1.5 text-[10px] font-medium text-destructive">
              검증 오류 {errorCount}건
            </div>
          ) : (
            <div className="pb-1" />
          )}
        </div>
      ) : null}
      {isOutput && isReadOnly ? (
        <div className="border-t border-[#A9C6D6] bg-[#FFF8E7] px-2 py-1 text-[9px] font-medium text-[#725B20]">
          read-only · 원본 output 계약 보존
        </div>
      ) : null}
    </div>
  );
}

function handleTop(index: number, count: number): string {
  if (count <= 1) return "50%";
  return `${((index + 1) / (count + 1)) * 100}%`;
}
