import {
  useFoundryLiteBackupRestorePreflight,
  useFoundryLiteClient,
  useFoundryLiteRecoveryOverview,
} from "@foundry-lite/sdk/react";
import { ShieldCheck, Siren } from "lucide-react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import {
  EvidenceRow,
  MutationEvidence,
  SectionHeader,
  StatTile,
} from "./OperationsPrimitives";
import { formatTimestamp, recoveryPhaseMeta } from "./operations-format";

/**
 * 복구 탭: recovery overview(현재 복원 위상/필요 조치) + preflight(위험 작업 사전 검사).
 * 위험 작업은 preflight 상태(ready/blocked)와 operator 조치를 명시적으로 노출한다.
 */
export function RecoveryPanel() {
  const client = useFoundryLiteClient();
  const overview = useFoundryLiteRecoveryOverview(client);
  const preflight = useFoundryLiteBackupRestorePreflight(client);

  if (overview.isLoading && !overview.data) {
    return <LoadingState rowCount={6} />;
  }

  if (overview.error && !overview.data) {
    return (
      <ErrorState
        error={overview.error}
        onRetry={() => void overview.reload()}
      />
    );
  }

  const phaseMeta = recoveryPhaseMeta(overview.phase);

  const handlePreflight = () => {
    void preflight.execute(undefined);
  };

  return (
    <div className="space-y-4">
      <section className="rounded border bg-card p-3">
        <SectionHeader
          label="복구 개요"
          hint="백업/복원 위상 및 트래픽 게이트"
          actions={
            <Button
              size="sm"
              variant="outline"
              onClick={() => void overview.reload()}
              disabled={overview.isRefreshing}
            >
              새로고침
            </Button>
          }
        />
        <div className="mt-3 flex items-center gap-2">
          <span className="text-[13px] font-medium">현재 위상</span>
          <StatusPill intent={phaseMeta.intent}>{phaseMeta.label}</StatusPill>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatTile
            label="쓰기 트래픽"
            value={overview.isWriteTrafficPaused ? "일시정지" : "정상"}
            intent={overview.isWriteTrafficPaused ? "warning" : "success"}
          />
          <StatTile
            label="Outbox 발행"
            value={overview.isOutboxPublisherPaused ? "일시정지" : "정상"}
            intent={overview.isOutboxPublisherPaused ? "warning" : "success"}
          />
          <StatTile
            label="활성 복원"
            value={overview.activeRestoreId ? "있음" : "없음"}
            intent={overview.activeRestoreId ? "info" : "neutral"}
          />
          <StatTile
            label="재개 승인"
            value={overview.canApproveResume ? "대기" : "—"}
            intent={overview.canApproveResume ? "warning" : "neutral"}
          />
        </div>
        <dl className="mt-3 divide-y rounded border bg-canvas px-3">
          <EvidenceRow
            label="활성 restore id"
            value={overview.activeRestoreId ?? "—"}
          />
          <EvidenceRow
            label="최근 restore id"
            value={overview.latestRestoreId ?? "—"}
          />
          <EvidenceRow
            label="최근 backup id"
            value={overview.latestBackupId ?? "—"}
          />
          <EvidenceRow
            label="생성 시각"
            value={formatTimestamp(overview.overview?.generatedAt)}
          />
        </dl>

        {overview.hasRequiredOperatorActions ? (
          <div className="mt-3 rounded border border-warning/40 bg-warning/5 p-3">
            <div className="flex items-center gap-1.5">
              <Siren className="size-4 text-warning" />
              <span className="section-label">필요한 운영자 조치</span>
            </div>
            <ul className="mt-1.5 space-y-1">
              {overview.requiredOperatorActions.map((action) => (
                <li
                  key={action}
                  className="flex items-center gap-1.5 font-mono text-[11px] text-foreground/80"
                >
                  <span className="text-warning">•</span>
                  {action}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section className="rounded border bg-card p-3">
        <SectionHeader
          label="복원 Preflight"
          hint="위험 작업 사전 검사 — 실제 복원 전 매니페스트/인덱스 검증"
          actions={
            <Button
              size="sm"
              onClick={handlePreflight}
              disabled={preflight.isRunning}
            >
              <ShieldCheck className="size-3.5" /> preflight 실행
            </Button>
          }
        />
        {preflight.report ? (
          <div className="mt-3 space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-medium">preflight 상태</span>
              <StatusPill intent={preflight.isReady ? "success" : "danger"}>
                {preflight.isReady
                  ? "ready"
                  : preflight.isBlocked
                    ? "blocked"
                    : preflight.report.status}
              </StatusPill>
              {preflight.issueCount > 0 ? (
                <StatusPill intent="warning">
                  이슈 {preflight.issueCount}건
                </StatusPill>
              ) : null}
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              <StatTile
                label="dataset 버전"
                value={preflight.report.datasetVersions.length}
              />
              <StatTile
                label="이슈"
                value={preflight.report.issues.length}
                intent={
                  preflight.report.issues.length > 0 ? "danger" : "neutral"
                }
              />
              <StatTile
                label="활성 인덱스 포인터"
                value={preflight.report.activeIndexPointers.length}
              />
            </div>
            <MutationEvidence>
              <span>backup_id={preflight.report.backupId}</span>
              <span>tenant={preflight.report.tenantId}</span>
              <span>
                generated_at={formatTimestamp(preflight.report.generatedAt)}
              </span>
            </MutationEvidence>
            {preflight.report.issues.length > 0 ? (
              <div className="space-y-1.5">
                {preflight.report.issues.map((issue, index) => (
                  <div
                    key={index}
                    className="rounded border border-destructive/30 bg-destructive/5 px-3 py-2 font-mono text-[11px] text-foreground/90"
                  >
                    {JSON.stringify(issue)}
                  </div>
                ))}
              </div>
            ) : (
              <StatusPill intent="success">차단 이슈 없음</StatusPill>
            )}
          </div>
        ) : preflight.error ? (
          <ErrorState
            error={preflight.error}
            onRetry={handlePreflight}
            className="mt-3"
          />
        ) : (
          <p className="mt-3 text-xs text-muted-foreground">
            preflight는 실제 복원을 수행하지 않습니다. 매니페스트·인덱스
            포인터를 검증해 복원 가능 여부(ready/blocked)만 판단합니다.
          </p>
        )}
      </section>
    </div>
  );
}
