import type {
  ActionEffectReceipt,
  ActionEffectReconcileRequest,
  ActionLogListResult,
  ActionRevertEligibility,
  ActionRun,
  ActionRunListResult,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient, useFoundryLiteMutation } from "@foundry-lite/sdk/react";
import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { useActionRunObserver } from "../use-action-run-observer";
import { ActionLogPanel } from "./ActionLogPanel";
import { ActionRunDetail } from "./ActionRunDetail";

type RuntimeData = {
  runs: ActionRunListResult;
  logs: ActionLogListResult;
};

export function ActionRuntimePanel() {
  const client = useFoundryLiteClient();
  const navigate = useNavigate();
  const [data, setData] = useState<RuntimeData | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [eligibility, setEligibility] = useState<ActionRevertEligibility | null>(null);
  const [eligibilityError, setEligibilityError] = useState<unknown>(null);
  const loadGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    try {
      const [runs, logs] = await Promise.all([
        client.actions.runs.list({ limit: 50 }),
        client.actions.logs({ limit: 50 }),
      ]);
      if (generation !== loadGeneration.current) return;
      setData({ runs, logs });
      setLoadError(null);
      setSelectedRunId((current) => current ?? runs.items[0]?.actionRunId ?? null);
    } catch (error) {
      if (generation === loadGeneration.current) setLoadError(error);
    }
  }, [client]);

  useEffect(() => {
    void load();
    return () => {
      loadGeneration.current += 1;
    };
  }, [load]);

  const initialRun = useMemo(
    () => data?.runs.items.find((run) => run.actionRunId === selectedRunId) ?? null,
    [data, selectedRunId],
  );
  const observer = useActionRunObserver(client.actions.runs, selectedRunId, initialRun);

  useEffect(() => {
    setEligibility(null);
    setEligibilityError(null);
    if (!selectedRunId || !observer.snapshot || !["succeeded", "reconciled"].includes(observer.snapshot.status)) return;
    let isCurrent = true;
    void client.actions.runs
      .revertEligibility(selectedRunId)
      .then((result) => {
        if (isCurrent) setEligibility(result);
      })
      .catch((error) => {
        if (isCurrent) setEligibilityError(error);
      });
    return () => {
      isCurrent = false;
    };
  }, [client, observer.snapshot?.status, selectedRunId]);

  const cancel = useFoundryLiteMutation(
    (runId: string) =>
      client.actions.runs.cancel(runId, { reason: "operator_cancelled_from_action_runtime" }, {
        idempotencyKey: `action-runtime-cancel:${runId}`,
      }),
    { onSuccess: () => void observer.refresh() },
  );
  const revert = useFoundryLiteMutation(
    (runId: string) =>
      client.actions.runs.revert(runId, { idempotencyKey: `action-runtime-revert:${runId}` }),
    { onSuccess: () => void load() },
  );
  const effectOperation = useFoundryLiteMutation(
    (command: EffectOperationCommand) => {
      if (command.kind === "cancel") {
        return client.actions.effects.cancel(
          command.effect.receiptId,
          { reason: "operator_cancelled_from_action_runtime" },
          { idempotencyKey: `action-effect-cancel:${command.effect.receiptId}:${command.effect.attemptCount}` },
        );
      }
      if (command.kind === "retry") {
        return client.actions.effects.retry(command.effect.receiptId, {
          idempotencyKey: `action-effect-retry:${command.effect.receiptId}:${command.effect.attemptCount}`,
        });
      }
      return client.actions.effects.reconcile(command.effect.receiptId, command.payload, {
        idempotencyKey: `action-effect-reconcile:${command.effect.receiptId}:${crypto.randomUUID()}`,
      });
    },
    { onSuccess: () => void observer.refresh() },
  );

  return (
    <div className="grid items-start gap-3 xl:grid-cols-[280px_minmax(0,1fr)]">
      <div className="space-y-3">
        <RunList
          runs={data?.runs.items ?? []}
          selectedRunId={selectedRunId}
          onSelect={setSelectedRunId}
          onRefresh={() => void load()}
        />
        <ActionLogPanel
          result={data?.logs ?? null}
          onOpenObject={(objectType, objectId) =>
            navigate(
              `/objects?objectType=${encodeURIComponent(objectType)}&objectId=${encodeURIComponent(objectId)}`,
            )
          }
        />
      </div>
      <div className="space-y-3">
        {loadError ? <ErrorState error={loadError} onRetry={() => void load()} /> : null}
        {cancel.error ? <ErrorState error={cancel.error} onRetry={() => selectedRunId && void cancel.execute(selectedRunId)} /> : null}
        {revert.error ? <ErrorState error={revert.error} /> : null}
        {effectOperation.error ? <ErrorState error={effectOperation.error} onRetry={() => void observer.refresh()} /> : null}
        <ActionRunDetail
          run={observer.snapshot}
          isConnected={observer.isConnected}
          streamError={observer.error}
          eligibility={eligibility}
          eligibilityError={eligibilityError}
          isCancelling={cancel.isRunning}
          isReverting={revert.isRunning}
          isEffectMutating={effectOperation.isRunning}
          onRefresh={() => void observer.refresh()}
          onCancel={(runId) => void cancel.execute(runId)}
          onRevert={(runId) => void revert.execute(runId)}
          onCancelEffect={(effect) => void effectOperation.execute({ kind: "cancel", effect })}
          onRetryEffect={(effect) => void effectOperation.execute({ kind: "retry", effect })}
          onReconcileEffect={(effect, payload) => void effectOperation.execute({ kind: "reconcile", effect, payload })}
        />
      </div>
    </div>
  );
}

type EffectOperationCommand =
  | { kind: "cancel"; effect: ActionEffectReceipt }
  | { kind: "retry"; effect: ActionEffectReceipt }
  | { kind: "reconcile"; effect: ActionEffectReceipt; payload: ActionEffectReconcileRequest };

function RunList({
  runs,
  selectedRunId,
  onSelect,
  onRefresh,
}: {
  runs: ActionRun[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  onRefresh: () => void;
}) {
  return (
    <section aria-label="Action 실행 이력" className="space-y-2 rounded border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="section-label">Durable 실행 이력</div>
          <div className="text-[10px] text-muted-foreground">run · step · attempt · fencing</div>
        </div>
        <Button size="sm" variant="ghost" aria-label="Action 실행 이력 새로고침" onClick={onRefresh}>
          <RefreshCw className="size-3" />
        </Button>
      </div>
      <div className="max-h-64 space-y-1 overflow-y-auto">
        {runs.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">아직 durable Action 실행이 없습니다.</p>
        ) : (
          runs.map((run) => (
            <button
              key={run.actionRunId}
              type="button"
              className={`w-full space-y-1 rounded border px-2 py-1.5 text-left text-[11px] ${
                selectedRunId === run.actionRunId ? "border-primary bg-primary/5" : "border-border"
              }`}
              onClick={() => onSelect(run.actionRunId)}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{run.actionApiName}</span>
                <StatusPill intent={run.status === "succeeded" ? "success" : "neutral"}>{run.status}</StatusPill>
              </div>
              <div className="truncate font-mono text-muted-foreground">{run.actionRunId}</div>
            </button>
          ))
        )}
      </div>
    </section>
  );
}
