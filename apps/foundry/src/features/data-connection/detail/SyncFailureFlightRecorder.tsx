import type {
  SourceManagedSyncRun,
  SourceSchedulerDecision,
} from "@foundry-lite/sdk";
import {
  AlertTriangle,
  ArrowRight,
  FileSearch,
  KeyRound,
  Network,
  RotateCcw,
  ShieldAlert,
} from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { formatTimestamp } from "../source-model";

interface SyncFailureFlightRecorderProps {
  sourceType: string;
  decision: SourceSchedulerDecision | null;
  latestFailure: SourceManagedSyncRun | null;
  isRecoveryRunning: boolean;
  isRecoveryDisabled?: boolean;
  onInspectFailure: (runId: string) => void;
  onStartRecovery: () => void;
}

/** 실패의 원인·보호 조치·복구 순서를 한 번에 읽는 운영용 flight recorder. */
export function SyncFailureFlightRecorder({
  sourceType,
  decision,
  latestFailure,
  isRecoveryRunning,
  isRecoveryDisabled = false,
  onInspectFailure,
  onStartRecovery,
}: SyncFailureFlightRecorderProps) {
  const failureCount = decision?.consecutiveFailureCount ?? 0;
  if (!latestFailure && failureCount === 0) return null;
  const threshold = decision?.autoPauseAfterFailures ?? 3;
  const isAutoPaused = decision?.autoPaused === true;
  const error = latestFailure?.error ?? decision?.lastFailureError ?? null;
  const message = errorText(error, "message") ?? "커넥터 실행이 완료되지 않았습니다.";
  const requestId = errorText(error, "requestId") ?? nestedErrorText(error, "request_id");
  const checks = troubleshootingChecks(sourceType);

  return (
    <section
      aria-label="동기화 실패 진단"
      data-testid="sync-failure-flight-recorder"
      className="overflow-hidden rounded border border-destructive/35 bg-card"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-destructive/25 bg-destructive/5 px-3 py-2.5">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <AlertTriangle className="size-4 text-destructive" />
            <h3 className="text-[13px] font-semibold">실패 flight recorder</h3>
            <StatusPill intent={isAutoPaused ? "danger" : "warning"}>
              {isAutoPaused ? "자동 일시정지" : `연속 실패 ${failureCount}/${threshold}`}
            </StatusPill>
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {isAutoPaused
              ? "반복 실패로 다음 예약 실행을 막았습니다. 원인을 수정한 뒤 복구 빌드를 통과해야 자동 실행이 재개됩니다."
              : "실패 증거를 확인하고 다음 예약 실행 전에 연결 상태를 점검하세요."}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {latestFailure ? (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-[11px]"
              onClick={() => onInspectFailure(latestFailure.runId)}
            >
              <FileSearch className="size-3" /> 증거 열기
            </Button>
          ) : null}
          {isAutoPaused ? (
            <Button
              size="sm"
              className="h-7 text-[11px]"
              disabled={isRecoveryRunning || isRecoveryDisabled}
              onClick={onStartRecovery}
            >
              <RotateCcw className="size-3" />
              {isRecoveryRunning ? "복구 확인 중…" : "복구 빌드"}
            </Button>
          ) : null}
        </div>
      </header>

      <div className="grid gap-0 lg:grid-cols-[1.15fr_1fr]">
        <div className="space-y-3 border-b p-3 lg:border-r lg:border-b-0">
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <span className="rounded border bg-background px-1.5 py-0.5">실행 실패</span>
            <ArrowRight className="size-3" />
            <span className="rounded border bg-background px-1.5 py-0.5">자동 보호</span>
            <ArrowRight className="size-3" />
            <span className="rounded border bg-background px-1.5 py-0.5">복구 검증</span>
          </div>
          <div className="rounded border border-destructive/20 bg-destructive/5 p-2.5">
            <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium text-destructive">
              <ShieldAlert className="size-3" /> 마지막 실패
              {latestFailure ? ` · ${formatTimestamp(latestFailure.completedAt)}` : ""}
            </div>
            <p className="break-words font-mono text-[11px] leading-4">{message}</p>
            {requestId ? (
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                request_id={requestId}
              </p>
            ) : null}
          </div>
        </div>

        <div className="p-3">
          <div className="section-label mb-2">권장 점검 순서</div>
          <ol className="space-y-2 text-[11px]">
            {checks.map(({ icon: Icon, title, description }, index) => (
              <li key={title} className="flex items-start gap-2">
                <span className="flex size-5 shrink-0 items-center justify-center rounded border bg-muted/50 font-mono text-[9px]">
                  {index + 1}
                </span>
                <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                <span>
                  <span className="font-medium">{title}</span>
                  <span className="block text-[10px] text-muted-foreground">{description}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

function troubleshootingChecks(sourceType: string) {
  const isDatabase = sourceType === "postgres_jdbc";
  return [
    {
      icon: Network,
      title: isDatabase ? "DB 주소·에이전트 경로" : "엔드포인트·네트워크 경로",
      description: "소스 탐색 또는 미리보기에서 같은 경로에 다시 연결되는지 확인합니다.",
    },
    {
      icon: KeyRound,
      title: "자격 증명·권한",
      description: isDatabase
        ? "비밀 참조, 테이블 읽기 권한, 인증서 만료를 확인합니다."
        : "토큰·헤더·OAuth 상태와 원격 리소스 접근 권한을 확인합니다.",
    },
    {
      icon: RotateCcw,
      title: isDatabase ? "테이블·체크포인트 검증" : "응답·페이지네이션 검증",
      description: "설정을 고친 뒤 복구 빌드가 실제 데이터 커밋까지 성공해야 재개됩니다.",
    },
  ];
}

function errorText(error: Record<string, unknown> | null, field: string): string | null {
  const value = error?.[field];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function nestedErrorText(error: Record<string, unknown> | null, field: string): string | null {
  const details = error?.details;
  return details && typeof details === "object"
    ? errorText(details as Record<string, unknown>, field)
    : null;
}
