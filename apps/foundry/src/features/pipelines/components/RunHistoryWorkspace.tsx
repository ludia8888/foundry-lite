import type { PipelineRun } from "@foundry-lite/sdk";
import { Radio, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { formatTimestamp } from "../pipeline-model";
import type { PipelineActions } from "../use-pipeline-actions";
import { usePipelineRunObserver } from "../use-pipeline-run-observer";
import { useSafeQuery } from "../use-safe-query";
import { RunExecutionEvidence } from "./RunExecutionEvidence";

type RunHistoryWorkspaceProps = {
  pipelineId: string;
  actions: PipelineActions;
  lastRun: PipelineRun | null;
};

export function RunHistoryWorkspace({
  pipelineId,
  actions,
  lastRun,
}: RunHistoryWorkspaceProps) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(
    lastRun?.id ?? null,
  );
  const loadRuns = useCallback(
    () => actions.recipe.listRuns(pipelineId, { limit: 30 }),
    [actions.recipe, pipelineId],
  );
  const runsQuery = useSafeQuery(["pipelines", "runs", pipelineId], loadRuns);
  const runs = useMemo(() => runsQuery.data?.items ?? [], [runsQuery.data]);

  useEffect(() => {
    if (!lastRun) return;
    setSelectedRunId(lastRun.id);
    void runsQuery.reload();
  }, [lastRun?.id, lastRun?.status]);

  useEffect(() => {
    if (!selectedRunId && runs[0]) setSelectedRunId(runs[0].id);
  }, [runs, selectedRunId]);

  const initialRun =
    runs.find((run) => run.id === selectedRunId) ??
    (lastRun?.id === selectedRunId ? lastRun : null);
  const observer = usePipelineRunObserver(
    actions.recipe,
    selectedRunId,
    initialRun,
  );

  return (
    <div className="space-y-3">
      <RunHistoryHeader
        isConnected={observer.isConnected}
        onRefresh={() => void runsQuery.reload()}
      />
      {runsQuery.error ? (
        <ErrorState
          error={runsQuery.error}
          onRetry={() => void runsQuery.reload()}
        />
      ) : (
        <RunHistoryList
          runs={runs}
          selectedRunId={selectedRunId}
          onSelect={setSelectedRunId}
        />
      )}
      <RunExecutionEvidence
        actions={actions}
        run={observer.snapshot}
        streamError={observer.error}
        isConnected={observer.isConnected}
        onRefresh={() => void observer.refresh()}
      />
    </div>
  );
}

function RunHistoryHeader({
  isConnected,
  onRefresh,
}: {
  isConnected: boolean;
  onRefresh: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="section-label">실행 이력</span>
      <StatusPill intent={isConnected ? "success" : "neutral"}>
        <Radio className="size-3" />
        {isConnected ? "SSE 연결됨" : "snapshot"}
      </StatusPill>
      <Button size="sm" variant="ghost" className="h-6 px-2" onClick={onRefresh}>
        <RefreshCw className="size-3" />
        새로고침
      </Button>
    </div>
  );
}

function RunHistoryList({
  runs,
  selectedRunId,
  onSelect,
}: {
  runs: PipelineRun[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}) {
  if (runs.length === 0) {
    return (
      <p className="text-[11px] text-muted-foreground">
        아직 enqueue된 실행이 없습니다.
      </p>
    );
  }
  return (
    <div className="max-h-36 space-y-1 overflow-y-auto">
      {runs.map((run) => (
        <button
          key={run.id}
          type="button"
          className={`grid w-full grid-cols-[1fr_auto] gap-2 border px-2 py-1.5 text-left text-[11px] ${
            run.id === selectedRunId ? "border-primary bg-primary/5" : "border-border"
          }`}
          onClick={() => onSelect(run.id)}
        >
          <span className="truncate font-mono">{run.id}</span>
          <span className="text-muted-foreground">
            {String(run.status)} · {formatTimestamp(run.startedAt)}
          </span>
        </button>
      ))}
    </div>
  );
}
