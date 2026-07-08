import { Columns3, Database, RefreshCw, Search, User } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Input } from "@/components/ui/input";
import type {
  LineageGraphModel,
  LineageGraphNode,
} from "@/features/lineage/lineage-model";

interface SearchPanelProps {
  model: LineageGraphModel | null;
  onSelectNode: (nodeId: string | null) => void;
}

function nodeIcon(node: LineageGraphNode) {
  if (node.kind === "object_type") return <User className="size-3.5" />;
  if (node.kind === "data_source") return <RefreshCw className="size-3.5" />;
  return <Database className="size-3.5" />;
}

function nodeKindLabel(node: LineageGraphNode): string {
  if (node.kind === "object_type") return "오브젝트 타입";
  if (node.kind === "data_source") return "데이터 소스";
  return "데이터셋";
}

/** 그래프 리소스 검색 패널 (find 툴). */
export function SearchPanel({ model, onSelectNode }: SearchPanelProps) {
  const [query, setQuery] = useState("");
  const nodes = model?.nodes ?? [];
  const normalizedQuery = query.trim().toLowerCase();
  const matchedNodes = normalizedQuery
    ? nodes.filter((node) => node.label.toLowerCase().includes(normalizedQuery))
    : nodes;

  return (
    <div className="flex flex-col gap-2 p-3">
      <div className="section-label">그래프에서 찾기</div>
      <div className="relative">
        <Search className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="리소스 이름 검색"
          className="h-7 pl-7 text-xs"
        />
      </div>

      {matchedNodes.length === 0 ? (
        <EmptyState
          title="일치하는 리소스가 없습니다"
          description="다른 검색어를 입력하거나 데이터 연결에서 새 소스를 등록하세요."
        />
      ) : (
        <div className="flex flex-col">
          {matchedNodes.map((node) => (
            <button
              key={node.id}
              type="button"
              onClick={() => onSelectNode(node.id)}
              className="flex items-center gap-2 rounded px-1.5 py-1.5 text-left hover:bg-muted/60"
            >
              <span className="text-muted-foreground">{nodeIcon(node)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[11px]">
                  {node.label}
                </span>
                <span className="block text-[10px] text-muted-foreground">
                  {nodeKindLabel(node)}
                </span>
              </span>
              {node.isStale ? (
                <StatusPill intent="warning">만료</StatusPill>
              ) : null}
            </button>
          ))}
        </div>
      )}

      <div className="mt-2 flex items-center gap-2 border-t pt-2">
        <Columns3 className="size-3.5 text-muted-foreground" />
        <span className="text-[11px] text-muted-foreground">
          열 이름 기반 데이터셋 검색
        </span>
        <StatusPill intent="neutral">future</StatusPill>
      </div>
    </div>
  );
}
