import { useFoundryLiteProvidedOperatorWorkspaceShell } from "@foundry-lite/sdk/react";
import { AlertTriangle, Loader2, Terminal } from "lucide-react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Badge } from "@/components/ui/badge";

import { FutureBadge, SectionHeader, StatTile } from "./OperationsPrimitives";

/**
 * 운영 홈: readiness overview + run 요약 + admin launchpad(browser action/operator command)
 * + attention 배지. 백엔드 admin overview/task-plan/runs를 한 번에 묶는 workspace shell 훅 사용.
 */
export function OperationsHome({
  onOpenRuns,
  onOpenDlq,
  onOpenRecovery,
}: {
  onOpenRuns: () => void;
  onOpenDlq: () => void;
  onOpenRecovery: () => void;
}) {
  const shell = useFoundryLiteProvidedOperatorWorkspaceShell();

  if (shell.isLoading && !shell.data) {
    return <LoadingState rowCount={8} />;
  }

  if (shell.error && !shell.data) {
    return (
      <ErrorState error={shell.error} onRetry={() => void shell.reload()} />
    );
  }

  const home = shell.home;
  const launchpad = home.adminLaunchpad;

  return (
    <div className="space-y-4">
      {/* 준비도 요약 */}
      <section className="space-y-2">
        <SectionHeader
          label="운영 준비도"
          hint="admin readiness overview 기반 실행 경로 분포"
        />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <StatTile
            label="실패 run"
            value={home.failedRunCount}
            intent={home.hasFailedRuns ? "danger" : "neutral"}
          />
          <StatTile
            label="실행 중 run"
            value={home.runningRunCount}
            intent={home.hasRunningRuns ? "info" : "neutral"}
          />
          <StatTile label="총 run" value={home.runCount} />
          <StatTile label="데이터셋" value={home.datasetCount} />
          <StatTile label="객체 타입" value={home.objectTypeCount} />
          <StatTile label="액션 타입" value={home.actionTypeCount} />
        </div>
      </section>

      {/* attention 배지 */}
      <section className="flex flex-wrap items-center gap-2">
        <span className="section-label">주의</span>
        {home.hasFailedRuns ? (
          <button
            type="button"
            onClick={onOpenRuns}
            className="inline-flex items-center gap-1 rounded bg-destructive/10 px-2 py-1 text-[11px] font-medium text-destructive hover:bg-destructive/15"
          >
            <AlertTriangle className="size-3" />
            실패 run {home.failedRunCount}건 조사
          </button>
        ) : null}
        {home.hasRunningRuns ? (
          <button
            type="button"
            onClick={onOpenRuns}
            className="inline-flex items-center gap-1 rounded bg-primary/10 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/15"
          >
            <Loader2 className="size-3" />
            진행 중 run {home.runningRunCount}건
          </button>
        ) : null}
        <button
          type="button"
          onClick={onOpenDlq}
          className="inline-flex items-center gap-1 rounded bg-warning/10 px-2 py-1 text-[11px] font-medium text-warning hover:bg-warning/15"
        >
          Record DLQ 큐 열기
        </button>
        {home.hasRecoveryActions ? (
          <button
            type="button"
            onClick={onOpenRecovery}
            className="inline-flex items-center gap-1 rounded bg-warning/10 px-2 py-1 text-[11px] font-medium text-warning hover:bg-warning/15"
          >
            복구 조치 필요
          </button>
        ) : null}
        {!home.hasFailedRuns &&
        !home.hasRunningRuns &&
        !home.hasRecoveryActions ? (
          <StatusPill intent="success">현재 긴급 항목 없음</StatusPill>
        ) : null}
      </section>

      {/* launchpad — browser action */}
      <section className="space-y-2">
        <SectionHeader
          label="브라우저 실행 가능 작업"
          hint="SDK로 이 화면에서 직접 실행"
        />
        {launchpad.browserActions.length > 0 ? (
          <div className="grid gap-2 md:grid-cols-2">
            {launchpad.browserActions.map((action) => (
              <div key={action.id} className="rounded border bg-card p-3">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-[13px] font-medium">
                    {action.title}
                  </span>
                  {action.requiresApproval ? (
                    <StatusPill intent="warning">승인 필요</StatusPill>
                  ) : (
                    <StatusPill intent="info">{action.riskLabel}</StatusPill>
                  )}
                </div>
                {action.apiRoute ? (
                  <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                    {action.apiRoute}
                  </p>
                ) : null}
                {action.sdkSurface ? (
                  <div className="mt-1.5 font-mono text-[11px] text-primary">
                    {action.sdkSurface}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            title="브라우저 실행 작업이 없습니다"
            description="현재 준비도 상태에서 SDK로 바로 실행 가능한 작업이 없습니다."
          />
        )}
      </section>

      {/* launchpad — operator command (worker/cli/runbook) */}
      {launchpad.operatorCommandCards.length > 0 ? (
        <section className="space-y-2">
          <SectionHeader
            label="운영자 실행 명령"
            hint="worker entrypoint / CLI / runbook — 브라우저에서 실행 불가"
          />
          <div className="space-y-1.5">
            {launchpad.operatorCommandCards.map((card) => (
              <div key={card.id} className="rounded border bg-card px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[12px] font-medium">{card.title}</span>
                  <Badge variant="outline" className="text-[10px]">
                    {card.executionSurface}
                  </Badge>
                </div>
                {card.command ? (
                  <div className="mt-1 flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
                    <Terminal className="size-3 shrink-0" />
                    <span className="truncate">{card.command}</span>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {/* future surfaces */}
      {launchpad.futureSurfaceActions.length > 0 ? (
        <section className="space-y-2">
          <SectionHeader
            label="예정 영역"
            hint="백엔드 미구현 — 정직하게 표기"
          />
          <div className="flex flex-wrap gap-2">
            {launchpad.futureSurfaceActions.map((action) => (
              <FutureBadge key={action.id}>{action.title}</FutureBadge>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
