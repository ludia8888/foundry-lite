import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  ChevronLeft,
  ChevronRight,
  Clock,
  Database,
  Link2,
  RefreshCw,
  User,
} from "lucide-react";

import type {
  LineageGraphNode,
  NodeColorway,
} from "@/features/lineage/lineage-model";
import { SELECTION_ORANGE } from "@/features/lineage/lineage-model";
import { cn } from "@/lib/utils";

export type LineageNodeData = Record<string, unknown> & {
  model: LineageGraphNode;
  colorway: NodeColorway;
  isImpactHighlighted: boolean;
  hasSyncBadge: boolean;
};

export type LineageFlowNode = Node<LineageNodeData, "lineage">;

function selectionStyle(
  isSelected: boolean,
  isImpact: boolean,
): React.CSSProperties {
  if (isSelected) {
    return {
      boxShadow: `0 0 0 2px ${SELECTION_ORANGE}, 0 0 8px 1px ${SELECTION_ORANGE}66`,
    };
  }
  if (isImpact) {
    return { boxShadow: `0 0 0 1.5px ${SELECTION_ORANGE}99` };
  }
  return {};
}

function NodeBadges({
  model,
  hasSyncBadge,
}: {
  model: LineageGraphNode;
  hasSyncBadge: boolean;
}) {
  if (!hasSyncBadge && !model.isStale) return null;
  return (
    <div className="absolute -top-2.5 -right-1.5 flex gap-0.5">
      {hasSyncBadge ? (
        <span
          className="flex size-4 items-center justify-center rounded-full border bg-white text-slate-600 shadow-sm"
          title="동기화로 적재된 데이터셋"
        >
          <RefreshCw className="size-2.5" />
        </span>
      ) : null}
      {model.isStale ? (
        <span
          className="flex size-4 items-center justify-center rounded-full border bg-white text-amber-700 shadow-sm"
          title="만료됨 — 업스트림에 더 새로운 버전이 있습니다"
        >
          <Clock className="size-2.5" />
        </span>
      ) : null}
    </div>
  );
}

/** 플랫 컬러 사각형 데이터셋 노드 + 데이터 소스/오브젝트 타입 변형 (스크린샷 재현). */
export function LineageDatasetNode({
  data,
  selected,
}: NodeProps<LineageFlowNode>) {
  const { model, colorway, isImpactHighlighted, hasSyncBadge } = data;

  if (model.kind === "data_source") {
    return (
      <div
        className="relative flex h-16 w-32 flex-col items-center justify-center gap-1 rounded-[3px] border"
        style={{
          background: colorway.background,
          borderColor: colorway.border,
          color: colorway.text,
          ...selectionStyle(Boolean(selected), isImpactHighlighted),
        }}
      >
        <Handle
          type="source"
          position={Position.Right}
          className="opacity-0!"
        />
        <Database className="size-4" />
        <span className="max-w-28 truncate px-1 text-[10px] font-medium">
          {model.label}
        </span>
        {model.detail ? (
          <span className="font-mono text-[9px] opacity-80">
            {model.detail}
          </span>
        ) : null}
      </div>
    );
  }

  if (model.kind === "object_type") {
    return (
      <div
        className="relative w-36 overflow-hidden rounded-[3px] border"
        style={{
          borderColor: colorway.border,
          ...selectionStyle(Boolean(selected), isImpactHighlighted),
        }}
      >
        <Handle type="target" position={Position.Left} className="opacity-0!" />
        <div
          className="flex h-9 items-center justify-center"
          style={{ background: colorway.background, color: colorway.text }}
        >
          <User className="size-4" />
        </div>
        <div className="flex h-6 items-center gap-1 bg-slate-600 px-1.5 text-white">
          <ChevronLeft className="size-3 opacity-70" />
          <span className="flex-1 truncate text-center text-[11px] font-semibold">
            {model.label}
          </span>
          <Link2 className="size-3 opacity-70" />
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "relative flex h-8 w-52 items-center rounded-[3px] border px-1",
      )}
      style={{
        background: colorway.background,
        borderColor: colorway.border,
        color: colorway.text,
        ...selectionStyle(Boolean(selected), isImpactHighlighted),
      }}
    >
      <Handle type="target" position={Position.Left} className="opacity-0!" />
      <Handle type="source" position={Position.Right} className="opacity-0!" />
      <ChevronLeft className="size-3.5 shrink-0 opacity-70" />
      <span className="flex-1 truncate text-center font-mono text-[11px] font-semibold">
        {model.label}
      </span>
      <ChevronRight className="size-3.5 shrink-0 opacity-70" />
      <NodeBadges model={model} hasSyncBadge={hasSyncBadge} />
    </div>
  );
}
