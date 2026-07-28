import type { PipelineRun, PipelineVersion } from "@foundry-lite/sdk";
import { History, Play, Rocket, Square } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { asText, formatTimestamp, shortFingerprint } from "../pipeline-model";
import {
  pipelineRunOutputErrorLabel,
  pipelineRunOutputRefLabel,
  pipelineRunOutputs,
  summarizePipelineRunOutputs,
  type PipelineRunOutputEvidence,
} from "../pipeline-run-model";
import type { PipelineActions } from "../use-pipeline-actions";
import { useSafeQuery } from "../use-safe-query";
import { ScheduleCard } from "./ScheduleCard";

interface RunsPanelProps {
  pipelineId: string | null;
  actions: PipelineActions;
  lastRun: PipelineRun | null;
}

const RUN_STATUS_INTENT: Record<string, StatusIntent> = {
  succeeded: "success",
  partial: "warning",
  running: "info",
  executing: "info",
  failed: "danger",
  cancelled: "warning",
};

/** 하단 실행 탭: 버전 목록 → 배포 → 실행 → run id/timeline evidence. */
export function RunsPanel({ pipelineId, actions, lastRun }: RunsPanelProps) {
  const loadVersions = useCallback(
    () => actions.recipe.listVersions(pipelineId ?? "", { limit: 20 }),
    [actions.recipe, pipelineId],
  );
  const versionsQuery = useSafeQuery(
    ["pipelines", "versions", pipelineId],
    loadVersions,
    { enabled: Boolean(pipelineId) },
  );

  const versions = useMemo(
    () => versionsQuery.data?.items ?? [],
    [versionsQuery.data],
  );

  const columns = useMemo<DataTableColumn<PipelineVersion>[]>(
    () => [
      {
        key: "version",
        header: "버전",
        isMono: true,
        render: (row) => `v${String(row.versionNumber ?? "?")}`,
      },
      {
        key: "id",
        header: "버전 ID",
        isMono: true,
        render: (row) => row.id,
      },
      {
        key: "fingerprint",
        header: "그래프 지문",
        isMono: true,
        render: (row) => shortFingerprint(asText(row.graphFingerprint)),
      },
      {
        key: "createdAt",
        header: "생성",
        render: (row) => formatTimestamp(row.createdAt),
      },
      {
        key: "deployedAt",
        header: "배포",
        render: (row) =>
          row.deployedAt ? (
            <StatusPill intent="success">
              {formatTimestamp(row.deployedAt)}
            </StatusPill>
          ) : (
            <StatusPill intent="neutral">미배포</StatusPill>
          ),
      },
      {
        key: "actions",
        header: "동작",
        render: (row) => (
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[11px]"
              disabled={actions.deployVersion.isRunning || !pipelineId}
              onClick={() =>
                void actions.deployVersion
                  .execute({
                    pipelineId: pipelineId ?? "",
                    versionId: row.id,
                  })
                  .then((deployed) => {
                    // 배포 evidence(deployedAt)가 즉시 보이도록 버전 목록을 재조회한다.
                    if (deployed) void versionsQuery.reload();
                  })
              }
            >
              <Rocket className="size-3" />
              배포
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[11px]"
              disabled={
                actions.startRun.isRunning || !pipelineId || !row.deployedAt
              }
              title={
                !row.deployedAt ? "배포된 버전만 실행할 수 있습니다" : undefined
              }
              onClick={() =>
                void actions.startRun.execute({
                  pipelineId: pipelineId ?? "",
                  versionId: row.id,
                })
              }
            >
              <Play className="size-3" />
              실행
            </Button>
          </div>
        ),
      },
    ],
    [actions, pipelineId, versionsQuery],
  );

  if (!pipelineId) {
    return (
      <EmptyState
        icon={History}
        title="브랜치를 먼저 선택하세요"
        className="m-3 border-0"
      />
    );
  }
  if (versionsQuery.isLoading)
    return <LoadingState rowCount={3} className="p-3" />;
  if (versionsQuery.error) {
    return (
      <ErrorState
        error={versionsQuery.error}
        onRetry={() => void versionsQuery.reload()}
        className="m-3"
      />
    );
  }

  return (
    <div className="grid gap-3 p-3 lg:grid-cols-2">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="section-label">파이프라인 버전</span>
          {actions.deployVersion.error ? (
            <StatusPill intent="danger">
              배포 실패 · {actions.deployVersion.error.code}
            </StatusPill>
          ) : null}
          {actions.startRun.error ? (
            <StatusPill intent="danger">
              실행 실패 · {actions.startRun.error.code}
            </StatusPill>
          ) : null}
        </div>
        <DataTable
          columns={columns}
          rows={versions}
          rowKey={(row) => row.id}
          emptyMessage="아직 버전이 없습니다. 제안을 승인·적용하면 버전이 생성됩니다."
        />
        <ScheduleCard
          pipelineId={pipelineId}
          versions={versions}
          actions={actions}
        />
      </div>
      <RunEvidence actions={actions} lastRun={lastRun} />
    </div>
  );
}

function RunEvidence({
  actions,
  lastRun,
}: {
  actions: PipelineActions;
  lastRun: PipelineRun | null;
}) {
  const [timeline, setTimeline] = useState<Record<string, unknown> | null>(
    null,
  );

  if (!lastRun) {
    return (
      <div className="space-y-2">
        <span className="section-label">실행 evidence</span>
        <p className="text-[11px] text-muted-foreground">
          이 세션에서 실행한 run의 id와 timeline이 여기 표시됩니다. 버전을
          배포한 뒤 실행 버튼을 누르세요.
        </p>
      </div>
    );
  }

  const status = String(lastRun.status);
  const outputs = pipelineRunOutputs(lastRun);
  const outputSummary = summarizePipelineRunOutputs(outputs);
  const events = timelineEvents(timeline ?? { timeline: lastRun.timeline });

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="section-label">실행 evidence</span>
        <StatusPill intent={RUN_STATUS_INTENT[status] ?? "neutral"}>
          {status}
        </StatusPill>
        {outputSummary.committed > 0 ? (
          <StatusPill intent="success">
            committed {outputSummary.committed}
          </StatusPill>
        ) : null}
        {outputSummary.failed > 0 ? (
          <StatusPill intent="danger">
            failed {outputSummary.failed}
          </StatusPill>
        ) : null}
        {status === "running" ? (
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px] text-destructive"
            disabled={actions.cancelRun.isRunning}
            onClick={() =>
              void actions.cancelRun.execute({ runId: lastRun.id })
            }
          >
            <Square className="size-3" />
            취소
          </Button>
        ) : null}
      </div>
      <div className="space-y-1 rounded border bg-muted/30 p-2 font-mono text-[11px]">
        <EvidenceRow label="run id" value={lastRun.id} />
        <EvidenceRow label="version" value={asText(lastRun.versionId) ?? "-"} />
        <EvidenceRow
          label="출력 수"
          value={String(outputSummary.total)}
        />
        <EvidenceRow
          label="완료"
          value={formatTimestamp(lastRun.completedAt)}
        />
        {lastRun.error ? (
          <EvidenceRow label="오류" value={JSON.stringify(lastRun.error)} />
        ) : null}
      </div>
      <RunOutputsEvidence outputs={outputs} />
      <div className="flex items-center gap-2">
        <span className="section-label">timeline</span>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-[11px]"
          onClick={() => {
            void actions.loadRunTimeline(lastRun.id).then(setTimeline);
          }}
        >
          새로고침
        </Button>
      </div>
      <div className="max-h-32 space-y-0.5 overflow-y-auto">
        {events.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            timeline 이벤트가 없습니다.
          </p>
        ) : (
          events.map((event, index) => (
            <div
              key={index}
              className="flex justify-between font-mono text-[11px]"
            >
              <span>{asText(event.event) ?? JSON.stringify(event)}</span>
              <span className="text-muted-foreground">
                {formatTimestamp(event.at)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function RunOutputsEvidence({
  outputs,
}: {
  outputs: readonly PipelineRunOutputEvidence[];
}) {
  return (
    <section className="space-y-1.5" aria-label="실행 출력 evidence">
      <span className="section-label">outputs</span>
      {outputs.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          이 run에는 committed 또는 failed output evidence가 없습니다.
        </p>
      ) : (
        <div className="space-y-1.5">
          {outputs.map((output) => {
            const error = pipelineRunOutputErrorLabel(output);
            return (
              <article
                key={`${output.nodeId}:${output.artifactKind}`}
                className="space-y-1 border border-[#D7DCE2] bg-card px-2.5 py-2 text-[11px]"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate font-mono font-semibold">
                    {output.nodeId}
                  </span>
                  <StatusPill intent={outputStatusIntent(output.status)}>
                    {output.status}
                  </StatusPill>
                  {output.isLegacyFallback ? (
                    <StatusPill intent="neutral">legacy fallback</StatusPill>
                  ) : null}
                </div>
                <div className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-2 gap-y-0.5 font-mono">
                  <span className="text-muted-foreground">artifact</span>
                  <span className="truncate">{output.artifactKind}</span>
                  <span className="text-muted-foreground">plane</span>
                  <span className="truncate">{output.plane}</span>
                  <span className="text-muted-foreground">ref</span>
                  <span className="truncate">
                    {pipelineRunOutputRefLabel(output)}
                  </span>
                  {error ? (
                    <>
                      <span className="text-destructive">error</span>
                      <span className="break-words text-destructive">
                        {error}
                      </span>
                    </>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function outputStatusIntent(status: string): StatusIntent {
  if (status.toUpperCase() === "COMMITTED") return "success";
  if (status.toUpperCase() === "FAILED") return "danger";
  return "warning";
}

function EvidenceRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate">{value}</span>
    </div>
  );
}

function timelineEvents(
  payload: Record<string, unknown> | null,
): Record<string, unknown>[] {
  const timeline = payload?.timeline;
  if (!Array.isArray(timeline)) return [];
  return timeline.filter(
    (item): item is Record<string, unknown> =>
      typeof item === "object" && item !== null,
  );
}
