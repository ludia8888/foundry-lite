import type { FoundryLiteDatasetExplorerState } from "@foundry-lite/sdk/react";
import { ArrowDownRight, ArrowUpLeft, MousePointerClick } from "lucide-react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import type {
  ImpactEntry,
  LineageGraphModel,
  LineageGraphNode,
} from "@/features/lineage/lineage-model";
import { getImpactEntries } from "@/features/lineage/lineage-model";

interface NodeDetailPanelProps {
  model: LineageGraphModel | null;
  node: LineageGraphNode | null;
  explorer: FoundryLiteDatasetExplorerState;
  onSelectNode: (nodeId: string) => void;
}

function MetaRow({
  label,
  value,
  isMono = true,
}: {
  label: string;
  value: React.ReactNode;
  isMono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="shrink-0 text-[11px] text-muted-foreground">
        {label}
      </span>
      <span
        className={
          isMono ? "truncate font-mono text-[11px]" : "truncate text-[11px]"
        }
      >
        {value}
      </span>
    </div>
  );
}

function ImpactList({
  title,
  icon,
  entries,
  emptyLabel,
  onSelectNode,
}: {
  title: string;
  icon: React.ReactNode;
  entries: ImpactEntry[];
  emptyLabel: string;
  onSelectNode: (nodeId: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="section-label flex items-center gap-1">
        {icon}
        {title} ({entries.length})
      </div>
      {entries.length === 0 ? (
        <div className="text-[11px] text-muted-foreground">{emptyLabel}</div>
      ) : (
        entries.map((entry) => (
          <button
            key={entry.node.id}
            type="button"
            onClick={() => onSelectNode(entry.node.id)}
            className="flex w-full items-center gap-2 rounded border px-2 py-1 text-left hover:bg-muted/60"
          >
            <StatusPill intent={entry.depth === 1 ? "info" : "neutral"}>
              {entry.depth}단계
            </StatusPill>
            <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
              {entry.node.label}
            </span>
          </button>
        ))
      )}
    </div>
  );
}

/** 선택 노드 상세 + 업/다운스트림 impact (acceptance: downstream 1단계 이상). */
export function NodeDetailPanel({
  model,
  node,
  explorer,
  onSelectNode,
}: NodeDetailPanelProps) {
  if (!node || !model) {
    return (
      <div className="p-3">
        <EmptyState
          icon={MousePointerClick}
          title="노드를 선택하세요"
          description="그래프에서 노드를 클릭하면 리소스 상세와 영향 범위가 표시됩니다."
        />
      </div>
    );
  }

  const downstream = getImpactEntries(model, node.id, "downstream");
  const upstream = getImpactEntries(model, node.id, "upstream");
  const isSelectedDataset =
    node.kind === "dataset" && explorer.selectedDatasetRef === node.datasetRef;

  return (
    <div className="space-y-4 p-3">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-mono text-[12px] font-semibold">
            {node.label}
          </span>
          {node.isStale ? <StatusPill intent="warning">만료</StatusPill> : null}
          {node.buildStatus === "failed" ? (
            <StatusPill intent="danger">빌드 실패</StatusPill>
          ) : null}
          {node.buildStatus === "success" ? (
            <StatusPill intent="success">빌드 성공</StatusPill>
          ) : null}
        </div>
        <div className="text-[11px] text-muted-foreground">
          {node.kind === "dataset"
            ? "데이터셋"
            : node.kind === "object_type"
              ? "오브젝트 타입"
              : "데이터 소스"}
          {node.detail ? ` · ${node.detail}` : ""}
        </div>
      </div>

      {node.kind === "dataset" ? (
        <div className="space-y-1.5 rounded border p-2">
          <div className="section-label">버전 evidence</div>
          {isSelectedDataset && explorer.isLoading ? (
            <LoadingState rowCount={3} />
          ) : isSelectedDataset && explorer.error ? (
            <ErrorState
              error={explorer.error}
              onRetry={() => void explorer.reload()}
            />
          ) : node.latestVersion ? (
            <>
              <MetaRow
                label="최신 버전"
                value={`v${node.latestVersion.version_number}`}
              />
              <MetaRow
                label="row count"
                value={node.latestVersion.row_count.toLocaleString()}
              />
              <MetaRow
                label="byte size"
                value={node.latestVersion.byte_size.toLocaleString()}
              />
              <MetaRow label="version id" value={node.latestVersion.id} />
              <MetaRow
                label="transaction"
                value={node.latestVersion.transaction_id}
              />
              <MetaRow label="브랜치" value={node.latestVersion.branch} />
            </>
          ) : (
            <div className="text-[11px] text-muted-foreground">
              아직 커밋된 버전이 없습니다.
            </div>
          )}
        </div>
      ) : null}

      <ImpactList
        title="다운스트림 영향"
        icon={<ArrowDownRight className="size-3" />}
        entries={downstream}
        emptyLabel="다운스트림 리소스가 없습니다."
        onSelectNode={onSelectNode}
      />
      <ImpactList
        title="업스트림 출처"
        icon={<ArrowUpLeft className="size-3" />}
        entries={upstream}
        emptyLabel="업스트림 리소스가 없습니다."
        onSelectNode={onSelectNode}
      />
    </div>
  );
}
