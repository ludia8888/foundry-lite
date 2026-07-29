import type { PipelineNodeAttempt, PipelineNodeRun, PipelineRun } from "@foundry-lite/sdk";
import { RefreshCw, Square } from "lucide-react";

import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { asText, formatTimestamp } from "../pipeline-model";
import {
  pipelineRunOutputErrorLabel,
  pipelineRunOutputRefLabel,
  pipelineRunOutputs,
  summarizePipelineRunOutputs,
  type PipelineRunOutputEvidence,
} from "../pipeline-run-model";
import type { PipelineActions } from "../use-pipeline-actions";

const RUN_STATUS_INTENT: Record<string, StatusIntent> = {
  queued: "neutral",
  succeeded: "success",
  partial: "warning",
  running: "info",
  cancelling: "warning",
  failed: "danger",
  cancelled: "warning",
};

type RunExecutionEvidenceProps = {
  actions: PipelineActions;
  run: PipelineRun | null;
  streamError: Error | null;
  isConnected: boolean;
  onRefresh: () => void;
};

export function RunExecutionEvidence({
  actions,
  run,
  streamError,
  isConnected,
  onRefresh,
}: RunExecutionEvidenceProps) {
  if (!run) return <EmptyRunEvidence />;
  const outputs = pipelineRunOutputs(run);
  const summary = summarizePipelineRunOutputs(outputs);
  const events = timelineEvents(run.timeline);
  return (
    <section className="space-y-2" aria-label="분산 DAG 실행 evidence">
      <RunStatusHeader actions={actions} run={run} summary={summary} />
      <RunCoordinates run={run} isConnected={isConnected} />
      {streamError ? (
        <p className="text-[11px] text-amber-700">
          SSE 재연결 중: {streamError.message}
        </p>
      ) : null}
      <RunNodeEvidence nodes={run.nodeRuns} />
      <RunOutputsEvidence outputs={outputs} />
      <Timeline events={events} onRefresh={onRefresh} />
    </section>
  );
}

function RunStatusHeader({
  actions,
  run,
  summary,
}: {
  actions: PipelineActions;
  run: PipelineRun;
  summary: ReturnType<typeof summarizePipelineRunOutputs>;
}) {
  const status = String(run.status);
  const canCancel = ["queued", "running", "cancelling"].includes(status);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="section-label">실행 evidence</span>
      <StatusPill intent={RUN_STATUS_INTENT[status] ?? "neutral"}>{status}</StatusPill>
      {summary.committed > 0 ? <StatusPill intent="success">committed {summary.committed}</StatusPill> : null}
      {summary.failed > 0 ? <StatusPill intent="danger">failed {summary.failed}</StatusPill> : null}
      {canCancel && status !== "cancelling" ? (
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-[11px] text-destructive"
          disabled={actions.cancelRun.isRunning}
          onClick={() => void actions.cancelRun.execute({ runId: run.id })}
        >
          <Square className="size-3" />
          취소
        </Button>
      ) : null}
    </div>
  );
}

function RunCoordinates({ run, isConnected }: { run: PipelineRun; isConnected: boolean }) {
  return (
    <div className="space-y-1 rounded border bg-muted/30 p-2 font-mono text-[11px]">
      <EvidenceRow label="run id" value={run.id} />
      <EvidenceRow label="workflow" value={run.workflowRunId ?? "-"} />
      <EvidenceRow label="dispatch" value={run.orchestration.dispatchStatus} />
      <EvidenceRow label="event seq" value={String(run.orchestration.lastEventSequence)} />
      <EvidenceRow label="stream" value={isConnected ? "SSE" : "snapshot polling"} />
      <EvidenceRow label="완료" value={formatTimestamp(run.completedAt)} />
      {run.cancelReason ? <EvidenceRow label="취소 사유" value={run.cancelReason} /> : null}
      {run.error ? <EvidenceRow label="오류" value={JSON.stringify(run.error)} /> : null}
    </div>
  );
}

function RunNodeEvidence({ nodes }: { nodes: PipelineNodeRun[] }) {
  return (
    <section className="space-y-1.5">
      <span className="section-label">DAG node / attempt</span>
      {nodes.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">node evidence를 기다리는 중입니다.</p>
      ) : (
        <div className="space-y-1.5">
          {nodes.map((node) => <NodeCard key={node.id} node={node} />)}
        </div>
      )}
    </section>
  );
}

function NodeCard({ node }: { node: PipelineNodeRun }) {
  const hasTakeover = node.attempts.length > 1 || node.attempts.some((attempt) => attempt.status === "lost");
  return (
    <article className="border border-border bg-card px-2.5 py-2 text-[11px]">
      <div className="flex items-center gap-2">
        <span className="font-mono font-semibold">{node.nodeId}</span>
        <StatusPill intent={nodeStatusIntent(node.status)}>{node.status}</StatusPill>
        {hasTakeover ? <StatusPill intent="warning">worker takeover</StatusPill> : null}
        {node.status === "retry_wait" ? <span>{retryCountdown(node.attempts)}</span> : null}
      </div>
      <div className="mt-1 space-y-0.5 font-mono text-muted-foreground">
        {node.attempts.map((attempt) => <AttemptRow key={attempt.id} attempt={attempt} />)}
      </div>
    </article>
  );
}

function AttemptRow({ attempt }: { attempt: PipelineNodeAttempt }) {
  return (
    <div className="flex flex-wrap gap-x-2">
      <span>#{attempt.attemptNumber}</span>
      <span>{attempt.status}</span>
      <span>worker {attempt.workerId ?? "-"}</span>
      <span>fence {attempt.fencingToken}</span>
      {attempt.errorKind ? <span className="text-destructive">{attempt.errorKind}</span> : null}
    </div>
  );
}

function RunOutputsEvidence({ outputs }: { outputs: readonly PipelineRunOutputEvidence[] }) {
  return (
    <section className="space-y-1.5" aria-label="실행 출력 evidence">
      <span className="section-label">committed outputs</span>
      {outputs.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">커밋된 출력 evidence가 없습니다.</p>
      ) : outputs.map((output) => <OutputCard key={`${output.nodeId}:${output.artifactKind}`} output={output} />)}
    </section>
  );
}

function OutputCard({ output }: { output: PipelineRunOutputEvidence }) {
  const error = pipelineRunOutputErrorLabel(output);
  return (
    <article className="border border-border bg-card px-2.5 py-2 text-[11px]">
      <div className="flex items-center gap-2">
        <span className="font-mono font-semibold">{output.nodeId}</span>
        <StatusPill intent={outputStatusIntent(output.status)}>{output.status}</StatusPill>
      </div>
      <p className="truncate font-mono">{output.plane} · {pipelineRunOutputRefLabel(output)}</p>
      {error ? <p className="text-destructive">{error}</p> : null}
    </article>
  );
}

function Timeline({ events, onRefresh }: { events: Record<string, unknown>[]; onRefresh: () => void }) {
  return (
    <section className="space-y-1">
      <div className="flex items-center gap-2">
        <span className="section-label">durable events</span>
        <Button size="sm" variant="ghost" className="h-6 px-2" onClick={onRefresh}>
          <RefreshCw className="size-3" />
          snapshot
        </Button>
      </div>
      {events.map((event, index) => (
        <div key={`${String(event.sequence ?? index)}`} className="flex justify-between font-mono text-[11px]">
          <span>{asText(event.event) ?? JSON.stringify(event)}</span>
          <span className="text-muted-foreground">{formatTimestamp(event.at)}</span>
        </div>
      ))}
    </section>
  );
}

function EmptyRunEvidence() {
  return <p className="text-[11px] text-muted-foreground">실행을 선택하면 DAG evidence가 표시됩니다.</p>;
}

function EvidenceRow({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between gap-2"><span className="text-muted-foreground">{label}</span><span className="truncate">{value}</span></div>;
}

function timelineEvents(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    : [];
}

function retryCountdown(attempts: PipelineNodeAttempt[]): string {
  const retryAt = attempts.at(-1)?.retryAt;
  if (!retryAt) return "retry 예정";
  const seconds = Math.max(0, Math.ceil((Date.parse(retryAt) - Date.now()) / 1000));
  return `${seconds}s 후 retry`;
}

function nodeStatusIntent(status: string): StatusIntent {
  if (status === "succeeded") return "success";
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "retry_wait") return "warning";
  if (status === "running") return "info";
  return "neutral";
}

function outputStatusIntent(status: string): StatusIntent {
  if (status.toUpperCase() === "COMMITTED") return "success";
  if (status.toUpperCase() === "FAILED") return "danger";
  return "warning";
}
