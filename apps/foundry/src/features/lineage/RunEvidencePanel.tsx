import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteProvidedMaintenanceControls,
  useFoundryLiteProvidedRecordDlqControls,
  type FoundryLiteOperationsRunListState,
  type FoundryLiteOperationsRunRow,
} from "@foundry-lite/sdk/react";
import { ExternalLink, MousePointerClick, RotateCw } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import type { LineageGraphNode } from "@/features/lineage/lineage-model";
import { isRunRelatedToNode } from "@/features/lineage/lineage-model";
import type { SelectedRunRef } from "@/features/lineage/lineage-selection";
import { useLineageRunDetail } from "@/features/lineage/use-lineage-runs";
import { cn } from "@/lib/utils";

export const RUN_PHASE_INTENTS: Record<string, StatusIntent> = {
  succeeded: "success",
  failed: "danger",
  running: "info",
  pending: "warning",
  unknown: "neutral",
};

export const RUN_PHASE_LABELS: Record<string, string> = {
  succeeded: "성공",
  failed: "실패",
  running: "실행 중",
  pending: "대기",
  unknown: "미확인",
};

interface RunEvidencePanelProps {
  node: LineageGraphNode | null;
  runList: FoundryLiteOperationsRunListState;
  selectedRun: SelectedRunRef | null;
  onSelectRun: (run: SelectedRunRef | null) => void;
  onMutated: () => void;
}

function RunListItem({
  run,
  isSelected,
  onSelect,
}: {
  run: FoundryLiteOperationsRunRow;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-2 rounded border px-2 py-1 text-left hover:bg-muted/60",
        isSelected && "border-primary/40 bg-accent",
      )}
    >
      <StatusPill intent={RUN_PHASE_INTENTS[run.phase] ?? "neutral"}>
        {RUN_PHASE_LABELS[run.phase] ?? run.phase}
      </StatusPill>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-[11px]">
          {run.runId}
        </span>
        <span className="block text-[10px] text-muted-foreground">
          {run.runType}
        </span>
      </span>
    </button>
  );
}

function TransformRetrySection({
  runId,
  onMutated,
}: {
  runId: string;
  onMutated: () => void;
}) {
  const maintenance = useFoundryLiteProvidedMaintenanceControls();
  const retry = maintenance.retryTransformRun;

  const handleRetry = async () => {
    const result = await retry.execute({ runId });
    if (result) {
      toast.success(
        `트랜스폼 재시도 완료 — 새 run ${result.transform_run_id} (v${result.version_number}, ${result.row_count.toLocaleString()}행)`,
      );
      onMutated();
    }
  };

  return (
    <div className="space-y-2 rounded border border-warning/40 bg-warning/5 p-2">
      <div className="section-label">재시도 (트랜스폼)</div>
      <Button
        size="sm"
        onClick={() => void handleRetry()}
        disabled={retry.isRunning}
      >
        <RotateCw
          className={cn("size-3.5", retry.isRunning && "animate-spin")}
        />
        {retry.isRunning ? "재시도 중…" : "이 run 재시도"}
      </Button>
      <div className="font-mono text-[10px] text-muted-foreground">
        중복 실행 방지: in-flight lock (transform:retry:{runId})
      </div>
      {retry.error ? (
        <ErrorState error={retry.error} onRetry={() => void handleRetry()} />
      ) : null}
      {retry.result ? (
        <div className="space-y-0.5 rounded border border-success/40 bg-success/5 p-2 font-mono text-[10px]">
          <div>new_run={retry.result.transform_run_id}</div>
          <div>
            version=v{retry.result.version_number} · rows=
            {retry.result.row_count.toLocaleString()}
          </div>
          <div>transaction={retry.result.transaction_id}</div>
          {retry.requestId ? <div>request_id={retry.requestId}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

function DeadLetterRetrySection({
  runId,
  onMutated,
}: {
  runId: string;
  onMutated: () => void;
}) {
  const dlq = useFoundryLiteProvidedRecordDlqControls();
  // 재시도 간 동일 키 재사용을 위해 선택된 레코드 기준으로 1회만 생성한다 (SDK 규약).
  const retryIdempotencyKey = useMemo(
    () => idempotencyKey("dead-letter-retry", runId),
    [runId],
  );

  const handleRetry = async () => {
    const result = await dlq.retry.execute({
      id: runId,
      idempotencyKey: retryIdempotencyKey,
    });
    if (result) {
      toast.success("DLQ 레코드 재시도를 요청했습니다");
      onMutated();
    }
  };

  return (
    <div className="space-y-2 rounded border border-warning/40 bg-warning/5 p-2">
      <div className="section-label">재시도 (DLQ)</div>
      <Button
        size="sm"
        onClick={() => void handleRetry()}
        disabled={dlq.retry.isRunning}
      >
        <RotateCw
          className={cn("size-3.5", dlq.retry.isRunning && "animate-spin")}
        />
        {dlq.retry.isRunning ? "재시도 중…" : "DLQ 재시도"}
      </Button>
      <div className="truncate font-mono text-[10px] text-muted-foreground">
        Idempotency-Key: {retryIdempotencyKey}
      </div>
      {dlq.retry.error ? (
        <ErrorState
          error={dlq.retry.error}
          onRetry={() => void handleRetry()}
        />
      ) : null}
      {dlq.retry.requestId && dlq.retry.result ? (
        <div className="font-mono text-[10px] text-muted-foreground">
          request_id={dlq.retry.requestId}
        </div>
      ) : null}
    </div>
  );
}

function RunDetailSection({
  selectedRun,
  onMutated,
}: {
  selectedRun: SelectedRunRef;
  onMutated: () => void;
}) {
  const detail = useLineageRunDetail(selectedRun);

  if (detail.isLoading) return <LoadingState rowCount={4} />;
  if (detail.error) {
    return (
      <ErrorState error={detail.error} onRetry={() => void detail.reload()} />
    );
  }
  if (!detail.detail || !detail.runRow) return null;

  const runRow = detail.runRow;
  const counts: { label: string; value: number }[] = [
    { label: "lineage edge", value: detail.lineageEdgeCount },
    { label: "run 연결", value: detail.runRelationCount },
    { label: "outbox", value: detail.relatedOutboxEventCount },
    { label: "감사 이벤트", value: detail.relatedAuditEventCount },
  ];

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <StatusPill intent={RUN_PHASE_INTENTS[runRow.phase] ?? "neutral"}>
          {RUN_PHASE_LABELS[runRow.phase] ?? runRow.phase}
        </StatusPill>
        <span className="truncate font-mono text-[11px]">{runRow.runId}</span>
      </div>
      {detail.detail.correlationId ? (
        <div className="font-mono text-[10px] text-muted-foreground">
          correlation_id={detail.detail.correlationId}
        </div>
      ) : null}

      {detail.hasError ? (
        <div className="space-y-1 rounded border border-destructive/40 bg-destructive/5 p-2">
          <div className="text-[11px] font-semibold text-destructive">
            실행 실패
          </div>
          <div className="font-mono text-[10px] break-all">
            {detail.detail.errorMessage ?? "오류 상세가 기록되지 않았습니다."}
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-1.5">
        {counts.map((count) => (
          <div key={count.label} className="rounded border px-2 py-1">
            <div className="text-[10px] text-muted-foreground">
              {count.label}
            </div>
            <div className="font-mono text-[12px]">{count.value}</div>
          </div>
        ))}
      </div>

      {detail.hasQualityEvidence ? (
        <div className="rounded border border-warning/40 bg-warning/5 p-2 text-[11px]">
          이 run에는 품질 evidence가 있습니다. 품질 실패는 재시도로 해결되지
          않습니다 — <span className="font-semibold">품질 체크</span> 탭에서
          체크 결과를 확인하세요.
        </div>
      ) : null}

      {runRow.isFailure && selectedRun.runType === "transform" ? (
        <TransformRetrySection
          runId={selectedRun.runId}
          onMutated={onMutated}
        />
      ) : null}
      {runRow.isFailure && selectedRun.runType === "dead_letter" ? (
        <DeadLetterRetrySection
          runId={selectedRun.runId}
          onMutated={onMutated}
        />
      ) : null}
      {runRow.isFailure &&
      selectedRun.runType !== "transform" &&
      selectedRun.runType !== "dead_letter" ? (
        <div className="text-[11px] text-muted-foreground">
          이 run 유형은 이 화면에서 재시도할 수 없습니다. 운영 콘솔에서
          처리하세요.
        </div>
      ) : null}

      <Link
        to="/operations"
        className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
      >
        <ExternalLink className="size-3" />
        운영 콘솔에서 이 run 조사
      </Link>
    </div>
  );
}

/** 선택 노드의 run evidence + 실패 run 재시도 컨트롤 (acceptance: 그래프와 같은 화면). */
export function RunEvidencePanel({
  node,
  runList,
  selectedRun,
  onSelectRun,
  onMutated,
}: RunEvidencePanelProps) {
  const relatedRuns = useMemo(() => {
    if (!node) return [];
    return runList.runRows.filter((run) => isRunRelatedToNode(run, node));
  }, [node, runList.runRows]);

  if (runList.isLoading) {
    return (
      <div className="p-3">
        <LoadingState rowCount={5} />
      </div>
    );
  }
  if (runList.error) {
    return (
      <div className="p-3">
        <ErrorState
          error={runList.error}
          onRetry={() => void runList.reload()}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4 p-3">
      <div className="space-y-1.5">
        <div className="section-label">
          관련 run {node ? `— ${node.label}` : ""}
        </div>
        {!node ? (
          <EmptyState
            icon={MousePointerClick}
            title="노드를 선택하세요"
            description="그래프에서 노드를 클릭하면 해당 리소스의 run evidence가 여기에 연결됩니다."
          />
        ) : relatedRuns.length === 0 ? (
          <EmptyState
            title="관련 run이 없습니다"
            description="이 리소스에 기록된 sync/transform/index run이 아직 없습니다. 파이프라인에서 빌드를 실행하세요."
          />
        ) : (
          relatedRuns.map((run) => (
            <RunListItem
              key={`${run.runType}-${run.runId}`}
              run={run}
              isSelected={selectedRun?.runId === run.runId}
              onSelect={() =>
                run.runId
                  ? onSelectRun({
                      runType: String(run.runType),
                      runId: run.runId,
                    })
                  : undefined
              }
            />
          ))
        )}
      </div>

      {selectedRun ? (
        <div className="space-y-1.5 border-t pt-3">
          <div className="section-label">run 상세</div>
          <RunDetailSection selectedRun={selectedRun} onMutated={onMutated} />
        </div>
      ) : null}
    </div>
  );
}
