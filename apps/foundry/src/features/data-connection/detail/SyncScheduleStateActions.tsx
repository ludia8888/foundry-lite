import type {
  SourceManagedSync,
  SourceSchedulerDecision,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { Pause, Play } from "lucide-react";
import { useCallback, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";

import { readSchedule } from "./sync-config";

type ScheduleStateAction = "pause" | "resume";

interface ScheduleStateCommand {
  action: ScheduleStateAction;
  idempotencyKey: string;
}

interface SyncScheduleStateActionsProps {
  sync: SourceManagedSync;
  decision: SourceSchedulerDecision | null;
  onUpdated: (sync: SourceManagedSync) => void;
}

/** 반복 일정의 값을 보존한 채 자동 트리거만 일시정지하거나 재개한다. */
export function SyncScheduleStateActions({
  sync,
  decision,
  onUpdated,
}: SyncScheduleStateActionsProps) {
  const client = useFoundryLiteClient();
  const [lastCommand, setLastCommand] =
    useState<ScheduleStateCommand | null>(null);
  const stateMutation = useFoundryLiteMutation(
    useCallback(
      (command: ScheduleStateCommand) => {
        const payload = {
          expectedConfigFingerprint: sync.configFingerprint,
        };
        const options = { idempotencyKey: command.idempotencyKey };
        return command.action === "pause"
          ? client.sources.managedSyncs.pauseSchedule(
              sync.syncName,
              payload,
              options,
            )
          : client.sources.managedSyncs.resumeSchedule(
              sync.syncName,
              payload,
              options,
            );
      },
      [client, sync.configFingerprint, sync.syncName],
    ),
    { onSuccess: onUpdated },
  );
  const schedule = readSchedule(sync);
  const isRecurring = schedule.mode === "interval" || schedule.mode === "cron";
  const isPaused = sync.status === "paused";
  const isAutoPaused = decision?.autoPaused === true;

  if (!isRecurring) return null;

  const execute = (action: ScheduleStateAction) => {
    const command = {
      action,
      idempotencyKey: idempotencyKey(
        `sync-schedule-${action}`,
        crypto.randomUUID(),
      ),
    } satisfies ScheduleStateCommand;
    setLastCommand(command);
    void stateMutation.execute(command);
  };

  return (
    <div
      className={
        isPaused
          ? "border-b border-warning/30 bg-warning/5 px-3 py-2"
          : "border-b border-success/20 bg-success/5 px-3 py-2"
      }
      data-testid="sync-schedule-state"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[11px] font-medium">
            {isPaused ? (
              <Pause className="size-3.5 text-warning" />
            ) : (
              <Play className="size-3.5 text-success" />
            )}
            {isPaused ? "스케줄 일시정지됨" : "자동 실행 활성"}
          </div>
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            {isAutoPaused
              ? "연속 실패 보호 상태입니다. 아래 실패 진단에서 복구 빌드를 실행하세요."
              : isPaused
              ? "주기 설정은 보존됩니다. 수동 빌드는 계속 실행할 수 있습니다."
              : "일시정지하면 다음 자동 실행부터 트리거되지 않습니다."}
          </p>
        </div>
        <Button
          variant={isPaused ? "default" : "outline"}
          size="sm"
          className="h-7 shrink-0 text-[11px]"
          disabled={stateMutation.isRunning || isAutoPaused}
          onClick={() => execute(isPaused ? "resume" : "pause")}
        >
          {isPaused ? <Play className="size-3" /> : <Pause className="size-3" />}
          {isAutoPaused
            ? "복구 필요"
            : stateMutation.isRunning
            ? "처리 중…"
            : isPaused
              ? "재개"
              : "일시정지"}
        </Button>
      </div>
      {stateMutation.error && lastCommand ? (
        <div className="mt-2">
          <ErrorState
            error={stateMutation.error}
            onRetry={() => void stateMutation.execute(lastCommand)}
          />
        </div>
      ) : null}
    </div>
  );
}
