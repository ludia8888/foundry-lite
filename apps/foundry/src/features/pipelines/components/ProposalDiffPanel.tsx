import type { PipelineProposal } from "@foundry-lite/sdk";
import { GitCompareArrows } from "lucide-react";
import { useCallback, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import {
  asGraph,
  asText,
  diffGraphNodes,
  diffSchemaColumns,
  emptyGraphDoc,
  type GraphNodeChange,
  type GraphNodeChangeKind,
  normalizeGraphDoc,
  parseBranchDiff,
  shortFingerprint,
} from "../pipeline-model";
import type { PipelineActions } from "../use-pipeline-actions";
import { useSafeQuery } from "../use-safe-query";

const CHANGE_BADGE: Record<
  GraphNodeChangeKind,
  { label: string; intent: StatusIntent }
> = {
  added: { label: "ADDED", intent: "success" },
  removed: { label: "REMOVED", intent: "danger" },
  modified: { label: "MODIFIED", intent: "warning" },
};

interface ProposalDiffPanelProps {
  proposal: PipelineProposal;
  actions: PipelineActions;
}

/** 제안 diff 패널: 브랜치 diff evidence + 노드 추가/삭제/변경 목록 + 스키마 Before/After. */
export function ProposalDiffPanel({
  proposal,
  actions,
}: ProposalDiffPanelProps) {
  const branchId = asText(proposal.branchId);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const loadDiff = useCallback(async () => {
    if (!branchId) return null;
    const diffPayload = await actions.recipe.diff(branchId);
    const diff = parseBranchDiff(diffPayload);
    const baseGraph = diff?.baseVersionId
      ? normalizeGraphDoc(
          (await actions.recipe.getVersion(diff.baseVersionId)).graph,
        )
      : emptyGraphDoc();
    const proposedGraph = normalizeGraphDoc(asGraph(proposal.graph));
    return { diff, changes: diffGraphNodes(baseGraph, proposedGraph) };
  }, [actions.recipe, branchId, proposal.graph]);

  const diffQuery = useSafeQuery(
    ["pipelines", "proposal-diff", proposal.id],
    loadDiff,
    { enabled: Boolean(branchId) },
  );

  if (diffQuery.isLoading) return <LoadingState rowCount={3} />;
  if (diffQuery.error) {
    return (
      <ErrorState
        error={diffQuery.error}
        onRetry={() => void diffQuery.reload()}
      />
    );
  }

  const diff = diffQuery.data?.diff ?? null;
  const changes = diffQuery.data?.changes ?? [];
  const selectedChange =
    changes.find((change) => change.nodeId === selectedNodeId) ??
    changes[0] ??
    null;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="section-label">브랜치 diff</span>
        {diff?.graph ? (
          diff.graph.changed ? (
            <StatusPill intent="warning">MODIFIED</StatusPill>
          ) : (
            <StatusPill intent="neutral">변경 없음</StatusPill>
          )
        ) : null}
        {diff?.baseStale ? (
          <StatusPill intent="warning">base 뒤처짐</StatusPill>
        ) : null}
        <span className="truncate font-mono text-[10px] text-muted-foreground">
          {asText(proposal.title) ?? proposal.id}
        </span>
      </div>

      <div className="space-y-1 rounded border bg-muted/30 p-2 font-mono text-[11px]">
        <DiffEvidenceRow
          label="fingerprint"
          value={
            diff?.graph
              ? `${shortFingerprint(diff.graph.baseFingerprint)} → ${shortFingerprint(diff.graph.graphFingerprint)}`
              : "-"
          }
        />
        <DiffEvidenceRow label="base 버전" value={diff?.baseVersionId ?? "-"} />
        <DiffEvidenceRow
          label="최신 버전"
          value={diff?.latestVersionId ?? "-"}
        />
      </div>

      <span className="section-label">노드 변경 {changes.length}건</span>
      {changes.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          base 그래프와 노드 차이가 없습니다.
        </p>
      ) : (
        <div className="max-h-24 space-y-0.5 overflow-y-auto">
          {changes.map((change) => (
            <ChangeRow
              key={`${change.kind}:${change.nodeId}`}
              change={change}
              isSelected={selectedChange?.nodeId === change.nodeId}
              onSelect={() => setSelectedNodeId(change.nodeId)}
            />
          ))}
        </div>
      )}

      {selectedChange ? <SchemaBeforeAfter change={selectedChange} /> : null}
    </div>
  );
}

function ChangeRow({
  change,
  isSelected,
  onSelect,
}: {
  change: GraphNodeChange;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const badge = CHANGE_BADGE[change.kind];
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "flex h-8 w-full items-center gap-2 rounded border px-2 text-left text-[12px]",
        isSelected ? "border-primary/40 bg-accent" : "hover:bg-muted/60",
      )}
    >
      <StatusPill intent={badge.intent} className="font-mono text-[10px]">
        {badge.label}
      </StatusPill>
      <span className="truncate font-medium">{change.label}</span>
      <span className="ml-auto truncate font-mono text-[10px] text-muted-foreground">
        {change.nodeId} · {change.nodeType}
      </span>
    </button>
  );
}

function SchemaBeforeAfter({ change }: { change: GraphNodeChange }) {
  const rows = diffSchemaColumns(change.schemaBefore, change.schemaAfter);
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <span className="section-label">스키마 Before / After</span>
        <span className="flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
          <GitCompareArrows className="size-3" />
          {change.nodeId}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          이 노드에는 기록된 스키마가 없습니다.
        </p>
      ) : (
        <div className="max-h-28 overflow-y-auto rounded border bg-card">
          <table className="w-full border-collapse text-[11px]">
            <thead>
              <tr className="border-b">
                <th className="section-label h-7 px-2 text-left">컬럼</th>
                <th className="section-label h-7 px-2 text-left">Before</th>
                <th className="section-label h-7 px-2 text-left">After</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name} className="border-b last:border-b-0">
                  <td className="h-7 px-2 font-mono">{row.name}</td>
                  <td
                    className={cn(
                      "h-7 px-2 font-mono",
                      row.before === null && "text-muted-foreground",
                      row.before !== null &&
                        row.after === null &&
                        "text-destructive line-through",
                    )}
                  >
                    {row.before ?? "—"}
                  </td>
                  <td
                    className={cn(
                      "h-7 px-2 font-mono",
                      row.after === null && "text-muted-foreground",
                      row.before === null &&
                        row.after !== null &&
                        "text-success",
                      row.before !== null &&
                        row.after !== null &&
                        row.before !== row.after &&
                        "text-warning",
                    )}
                  >
                    {row.after ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DiffEvidenceRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate">{value}</span>
    </div>
  );
}
