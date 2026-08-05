import type {
  ActionEffectReceipt,
  ActionEffectReconcileRequest,
  ActionRevertEligibility,
  ActionRun,
  ActionRunAttempt,
  ActionRunStep,
} from "@foundry-lite/sdk";
import { Ban, RefreshCw, RotateCcw, ServerCog } from "lucide-react";
import { type ReactNode, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { isTerminalActionRun } from "../use-action-run-observer";

type ActionRunDetailProps = {
  run: ActionRun | null;
  isConnected: boolean;
  streamError: Error | null;
  eligibility: ActionRevertEligibility | null;
  eligibilityError: unknown;
  isCancelling: boolean;
  isReverting: boolean;
  isEffectMutating: boolean;
  onRefresh: () => void;
  onCancel: (runId: string) => void;
  onRevert: (runId: string) => void;
  onCancelEffect: (effect: ActionEffectReceipt) => void;
  onRetryEffect: (effect: ActionEffectReceipt) => void;
  onReconcileEffect: (effect: ActionEffectReceipt, payload: ActionEffectReconcileRequest) => void;
};

export function ActionRunDetail({
  run,
  isConnected,
  streamError,
  eligibility,
  eligibilityError,
  isCancelling,
  isReverting,
  isEffectMutating,
  onRefresh,
  onCancel,
  onRevert,
  onCancelEffect,
  onRetryEffect,
  onReconcileEffect,
}: ActionRunDetailProps) {
  if (!run) {
    return (
      <div className="rounded border bg-card p-4 text-xs text-muted-foreground">
        실행을 선택하면 step, attempt, worker와 외부효과 증거가 표시됩니다.
      </div>
    );
  }
  const isTerminal = isTerminalActionRun(run);
  const hasTakeover = takeoverEvidence(run.attempts);
  return (
    <section aria-label="Action 실행 상세" className="space-y-3 rounded border bg-card p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusPill intent={statusIntent(run.status)}>{run.status}</StatusPill>
            <StatusPill intent={isConnected ? "success" : "neutral"}>
              {isConnected ? "SSE 연결" : isTerminal ? "terminal snapshot" : "재연결 대기"}
            </StatusPill>
            {hasTakeover ? (
              <StatusPill intent="warning">
                <ServerCog className="size-3" /> worker takeover
              </StatusPill>
            ) : null}
          </div>
          <div className="truncate font-mono text-[11px]">{run.actionRunId}</div>
          <div className="text-[11px] text-muted-foreground">
            {run.actionApiName} · {run.target.objectType}/{run.target.objectId} · event #{run.eventSequence}
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" variant="ghost" onClick={onRefresh}>
            <RefreshCw className="size-3" /> 새로고침
          </Button>
          {!isTerminal ? (
            <Button
              size="sm"
              variant="outline"
              disabled={isCancelling}
              onClick={() => onCancel(run.actionRunId)}
            >
              <Ban className="size-3" /> {isCancelling ? "취소 요청 중" : "취소"}
            </Button>
          ) : null}
          {eligibility?.isEligible ? (
            <Button
              size="sm"
              variant="outline"
              disabled={isReverting}
              onClick={() => onRevert(run.actionRunId)}
            >
              <RotateCcw className="size-3" /> {isReverting ? "되돌리는 중" : "되돌리기"}
            </Button>
          ) : null}
        </div>
      </div>

      {streamError ? <ErrorState error={streamError} onRetry={onRefresh} /> : null}
      {eligibilityError ? <ErrorState error={eligibilityError} /> : null}
      <RunSummary run={run} eligibility={eligibility} />
      <RunSteps steps={run.steps} />
      <RunAttempts attempts={run.attempts} />
      <RunEffects
        effects={run.effects}
        isMutating={isEffectMutating}
        onCancel={onCancelEffect}
        onRetry={onRetryEffect}
        onReconcile={onReconcileEffect}
      />
    </section>
  );
}

function RunSummary({ run, eligibility }: { run: ActionRun; eligibility: ActionRevertEligibility | null }) {
  const orchestration = run.orchestration;
  return (
    <div className="grid gap-2 text-[11px] sm:grid-cols-2 xl:grid-cols-4">
      <EvidenceCell label="workflow" value={text(orchestration.workflowRunId)} />
      <EvidenceCell label="dispatch" value={text(orchestration.dispatchStatus)} />
      <EvidenceCell label="plan hash" value={shortHash(run.planHash)} />
      <EvidenceCell
        label="revert"
        value={eligibility ? (eligibility.isEligible ? "eligible" : eligibility.reason ?? "blocked") : "—"}
      />
      <EvidenceCell
        label="external-effect compensation"
        value={eligibility?.hasPreservedExternalEffects
          ? eligibility.compensationAction ?? "operator reconciliation required"
          : "not required"}
      />
    </div>
  );
}

function RunSteps({ steps }: { steps: ActionRunStep[] }) {
  return (
    <EvidenceGroup title="Steps" empty={steps.length === 0}>
      {steps.map((step) => (
        <div key={step.stepKey} className="grid grid-cols-[1fr_auto] gap-2 border-b py-1.5 last:border-0">
          <span className="font-mono">{step.stepKey} · {step.kind}</span>
          <span className="text-muted-foreground">{step.status} · attempts {step.attemptCount}</span>
        </div>
      ))}
    </EvidenceGroup>
  );
}

function RunAttempts({ attempts }: { attempts: ActionRunAttempt[] }) {
  return (
    <EvidenceGroup title="Attempts · fencing" empty={attempts.length === 0}>
      {attempts.map((attempt) => (
        <div
          key={`${attempt.stepId}:${attempt.attemptNumber}`}
          className="grid gap-1 border-b py-1.5 last:border-0 sm:grid-cols-[1fr_auto]"
        >
          <span className="font-mono">
            #{attempt.attemptNumber} · {attempt.workerId} · fence {attempt.fencingToken}
          </span>
          <span className="text-muted-foreground">
            {attempt.status}{attempt.retryAt ? ` · retry ${formatTime(attempt.retryAt)}` : ""}
          </span>
        </div>
      ))}
    </EvidenceGroup>
  );
}

function RunEffects({
  effects,
  isMutating,
  onCancel,
  onRetry,
  onReconcile,
}: {
  effects: ActionEffectReceipt[];
  isMutating: boolean;
  onCancel: (effect: ActionEffectReceipt) => void;
  onRetry: (effect: ActionEffectReceipt) => void;
  onReconcile: (effect: ActionEffectReceipt, payload: ActionEffectReconcileRequest) => void;
}) {
  return (
    <EvidenceGroup title="Effect receipts" empty={effects.length === 0}>
      {effects.map((effect) => (
        <EffectRow
          key={effect.receiptId}
          effect={effect}
          isMutating={isMutating}
          onCancel={onCancel}
          onRetry={onRetry}
          onReconcile={onReconcile}
        />
      ))}
    </EvidenceGroup>
  );
}

function EffectRow({
  effect,
  isMutating,
  onCancel,
  onRetry,
  onReconcile,
}: {
  effect: ActionEffectReceipt;
  isMutating: boolean;
  onCancel: (effect: ActionEffectReceipt) => void;
  onRetry: (effect: ActionEffectReceipt) => void;
  onReconcile: (effect: ActionEffectReceipt, payload: ActionEffectReconcileRequest) => void;
}) {
  const [method, setMethod] = useState<ActionEffectReconcileRequest["evidence"]["verificationMethod"]>(
    "provider_query",
  );
  const [reference, setReference] = useState("");
  const [externalId, setExternalId] = useState("");
  const canCancel = effect.phase === "after_commit" && ["pending", "retry_wait", "delivering"].includes(effect.status);
  return (
    <div className="space-y-2 border-b py-2 last:border-0">
      <div className="grid gap-1 sm:grid-cols-[1fr_auto]">
        <span><span className="font-mono">{effect.effectId}</span> · {effect.phase} · {effect.targetRef}</span>
        <span className="text-muted-foreground">{effect.status} · {effect.attemptCount}/{effect.maxAttempts}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
        <span>worker {effect.workerId ?? "—"}</span><span>fence {effect.fencingToken}</span>
        {effect.dispatchStartedAt ? <span>외부 전송 시작됨</span> : null}
        {effect.cancelRequestedAt ? <span>취소 경합 · {effect.cancellationDisposition ?? "확인 중"}</span> : null}
        {effect.notificationRendering ? <span>알림 문구 · 편집 전 v{String(effect.notificationRendering.sourceObjectVersion ?? "—")} 고정</span> : null}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {canCancel ? <Button size="sm" variant="outline" disabled={isMutating} onClick={() => onCancel(effect)}>효과 취소</Button> : null}
        {effect.status === "dead_letter" && effect.phase === "after_commit" ? <Button size="sm" variant="outline" disabled={isMutating} onClick={() => onRetry(effect)}>동일 키로 1회 재시도</Button> : null}
      </div>
      {effect.status === "outcome_unknown" ? (
        <div className="grid gap-2 rounded border bg-background p-2 sm:grid-cols-2">
          <div className="space-y-1"><Label htmlFor={`${effect.receiptId}-method`}>확인 방법</Label><Select value={method} onValueChange={(value) => setMethod(value as typeof method)}><SelectTrigger id={`${effect.receiptId}-method`} aria-label="효과 조정 확인 방법"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="provider_query">Provider API 조회</SelectItem><SelectItem value="provider_dashboard">Provider 대시보드</SelectItem><SelectItem value="support_confirmation">Provider 지원 확인</SelectItem></SelectContent></Select></div>
          <div className="space-y-1"><Label htmlFor={`${effect.receiptId}-reference`}>Provider 근거 번호</Label><Input id={`${effect.receiptId}-reference`} value={reference} onChange={(event) => setReference(event.target.value)} placeholder="case / receipt 번호" /></div>
          <div className="space-y-1"><Label htmlFor={`${effect.receiptId}-external`}>외부 실행 ID</Label><Input id={`${effect.receiptId}-external`} value={externalId} onChange={(event) => setExternalId(event.target.value)} placeholder="전달 확인 시 필수" /></div>
          <div className="flex items-end gap-1.5"><Button size="sm" disabled={isMutating || !reference.trim() || !externalId.trim()} onClick={() => onReconcile(effect, reconciliationPayload("confirmed_delivered", method, reference, externalId))}>전달 확인</Button><Button size="sm" variant="outline" disabled={isMutating || !reference.trim()} onClick={() => onReconcile(effect, reconciliationPayload("confirmed_not_delivered", method, reference, ""))}>미전달 확인</Button></div>
        </div>
      ) : null}
    </div>
  );
}

function reconciliationPayload(
  resolution: ActionEffectReconcileRequest["resolution"],
  verificationMethod: ActionEffectReconcileRequest["evidence"]["verificationMethod"],
  providerReference: string,
  externalExecutionId: string,
): ActionEffectReconcileRequest {
  return {
    resolution,
    evidence: {
      verificationMethod,
      providerReference: providerReference.trim(),
      verifiedAt: new Date().toISOString(),
      ...(externalExecutionId.trim() ? { externalExecutionId: externalExecutionId.trim() } : {}),
    },
  };
}

function EvidenceGroup({
  title,
  empty,
  children,
}: {
  title: string;
  empty: boolean;
  children: ReactNode;
}) {
  return (
    <div className="rounded border bg-muted/20 p-2 text-[11px]">
      <div className="section-label mb-1">{title}</div>
      {empty ? <p className="text-muted-foreground">기록 없음</p> : children}
    </div>
  );
}

function EvidenceCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded border bg-muted/20 p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="truncate font-mono">{value}</div>
    </div>
  );
}

function takeoverEvidence(attempts: ActionRunAttempt[]): boolean {
  return attempts.some((attempt) => attempt.fencingToken > 1) || new Set(attempts.map((item) => item.workerId)).size > 1;
}

function statusIntent(status: string): StatusIntent {
  if (status === "succeeded" || status === "reconciled") return "success";
  if (status === "failed" || status === "conflict" || status === "outcome_unknown") return "danger";
  if (status === "cancelling" || status === "cancelled" || status === "compensation_required") return "warning";
  return "info";
}

function shortHash(value: string | null): string {
  return value ? `${value.slice(0, 18)}…` : "—";
}

function text(value: unknown): string {
  return typeof value === "string" && value ? value : "—";
}

function formatTime(value: string): string {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
}
