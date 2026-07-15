import type {
  SourceManagedSync,
  SourceSchedulerDecision,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { CalendarClock, Pencil, ShieldCheck } from "lucide-react";
import { useCallback, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

import { formatTimestamp } from "../source-model";
import { readSchedule, scheduleSummary } from "./sync-config";
import { SyncScheduleStateActions } from "./SyncScheduleStateActions";

type ScheduleMode = "manual" | "disabled" | "interval" | "cron";

interface SyncSchedulePanelProps {
  sync: SourceManagedSync;
  decision: SourceSchedulerDecision | null;
  onUpdated: (sync: SourceManagedSync) => void;
}

/** config fingerprint 충돌 방지와 감사 추적이 적용된 sync 일정 편집기. */
export function SyncSchedulePanel({
  sync,
  decision,
  onUpdated,
}: SyncSchedulePanelProps) {
  const client = useFoundryLiteClient();
  const current = readSchedule(sync);
  const [isOpen, setIsOpen] = useState(false);
  const [mode, setMode] = useState<ScheduleMode>(current.mode);
  const [everySeconds, setEverySeconds] = useState(
    String(current.everySeconds ?? 3600),
  );
  const [cron, setCron] = useState(current.cron ?? "0 * * * *");
  const [batchLimit, setBatchLimit] = useState(
    current.batchLimit ? String(current.batchLimit) : "",
  );
  const [autoPauseAfterFailures, setAutoPauseAfterFailures] = useState(
    String(current.autoPauseAfterFailures),
  );
  const [validationError, setValidationError] = useState<string | null>(null);

  const updateSchedule = useFoundryLiteMutation(
    useCallback(
      (schedule: Record<string, unknown>) =>
        client.sources.managedSyncs.updateSchedule(
          sync.syncName,
          {
            schedule,
            expectedConfigFingerprint: sync.configFingerprint,
          },
          {
            idempotencyKey: idempotencyKey(
              "sync-schedule",
              crypto.randomUUID(),
            ),
          },
        ),
      [client, sync.configFingerprint, sync.syncName],
    ),
    {
      onSuccess: (updated) => {
        onUpdated(updated);
        setIsOpen(false);
      },
    },
  );

  const handleSave = () => {
    const schedule = buildSchedule(
      mode,
      everySeconds,
      cron,
      batchLimit,
      autoPauseAfterFailures,
    );
    if (typeof schedule === "string") {
      setValidationError(schedule);
      return;
    }
    setValidationError(null);
    void updateSchedule.execute(schedule);
  };

  return (
    <div className="rounded border bg-card" data-testid="sync-schedule-panel">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="flex items-center gap-1.5 text-[13px] font-semibold">
          <CalendarClock className="size-3.5 text-muted-foreground" /> 일정
        </span>
        <Sheet open={isOpen} onOpenChange={setIsOpen}>
          <SheetTrigger asChild>
            <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px]">
              <Pencil className="size-3" /> 편집
            </Button>
          </SheetTrigger>
          <SheetContent className="sm:max-w-md">
            <SheetHeader className="border-b">
              <SheetTitle>빌드 일정 편집</SheetTitle>
              <SheetDescription>
                저장 시 구성 지문을 비교해 다른 사용자의 변경을 덮어쓰지 않습니다.
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-4 overflow-y-auto px-4">
              <div className="space-y-1.5">
                <Label htmlFor="sync-schedule-mode">실행 방식</Label>
                <Select
                  value={mode}
                  onValueChange={(value) => setMode(value as ScheduleMode)}
                >
                  <SelectTrigger id="sync-schedule-mode" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="manual">수동 실행만</SelectItem>
                    <SelectItem value="interval">일정 간격</SelectItem>
                    <SelectItem value="cron">Cron 표현식</SelectItem>
                    <SelectItem value="disabled">스케줄 사용 안 함</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {mode === "interval" ? (
                <div className="space-y-1.5">
                  <Label htmlFor="sync-schedule-interval">실행 간격(초)</Label>
                  <Input
                    id="sync-schedule-interval"
                    data-testid="sync-schedule-interval"
                    value={everySeconds}
                    onChange={(event) => setEverySeconds(event.target.value)}
                    inputMode="numeric"
                    className="font-mono"
                  />
                  <p className="text-[11px] text-muted-foreground">
                    예: 3600은 매시간, 86400은 매일입니다.
                  </p>
                </div>
              ) : null}
              {mode === "cron" ? (
                <div className="space-y-1.5">
                  <Label htmlFor="sync-schedule-cron">Cron (분 시 일 월 요일)</Label>
                  <Input
                    id="sync-schedule-cron"
                    data-testid="sync-schedule-cron"
                    value={cron}
                    onChange={(event) => setCron(event.target.value)}
                    className="font-mono"
                  />
                </div>
              ) : null}
              {mode !== "disabled" ? (
                <div className="space-y-1.5">
                  <Label htmlFor="sync-schedule-batch-limit">
                    실행당 최대 행 수 (선택)
                  </Label>
                  <Input
                    id="sync-schedule-batch-limit"
                    data-testid="sync-schedule-batch-limit"
                    value={batchLimit}
                    onChange={(event) => setBatchLimit(event.target.value)}
                    inputMode="numeric"
                    placeholder="제한 없음"
                    className="font-mono"
                  />
                </div>
              ) : null}
              {mode === "interval" || mode === "cron" ? (
                <div className="space-y-1.5">
                  <Label htmlFor="sync-schedule-auto-pause">
                    연속 실패 자동 일시정지 횟수
                  </Label>
                  <Input
                    id="sync-schedule-auto-pause"
                    data-testid="sync-schedule-auto-pause"
                    value={autoPauseAfterFailures}
                    onChange={(event) => setAutoPauseAfterFailures(event.target.value)}
                    inputMode="numeric"
                    className="font-mono"
                  />
                  <p className="text-[11px] text-muted-foreground">
                    1~10회. 기본값 3회이며 복구 빌드 성공 전까지 자동 실행을 보호합니다.
                  </p>
                </div>
              ) : null}
              <div className="rounded border border-primary/20 bg-primary/5 p-3 text-[11px]">
                <div className="flex items-center gap-1.5 font-medium">
                  <ShieldCheck className="size-3.5 text-primary" /> 안전한 저장
                </div>
                <p className="mt-1 text-muted-foreground">
                  설정 변경 전후와 요청 추적 키가 감사 기록에 남습니다.
                </p>
              </div>
              {validationError ? (
                <p role="alert" className="text-xs text-destructive">
                  {validationError}
                </p>
              ) : null}
              {updateSchedule.error ? (
                <ErrorState error={updateSchedule.error} onRetry={handleSave} />
              ) : null}
            </div>
            <SheetFooter className="border-t sm:flex-row sm:justify-end">
              <Button variant="outline" onClick={() => setIsOpen(false)}>
                취소
              </Button>
              <Button onClick={handleSave} disabled={updateSchedule.isRunning}>
                {updateSchedule.isRunning ? "저장 중…" : "일정 저장"}
              </Button>
            </SheetFooter>
          </SheetContent>
        </Sheet>
      </div>
      <SyncScheduleStateActions sync={sync} decision={decision} onUpdated={onUpdated} />
      <div className="space-y-2 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px]">{scheduleSummary(current)}</span>
          <StatusPill
            intent={
              sync.status === "paused"
                ? "warning"
                : decision?.due
                  ? "warning"
                  : decision?.enabled
                    ? "success"
                    : "neutral"
            }
          >
            {sync.status === "paused"
              ? "일시정지"
              : decision?.due
              ? "실행 대기"
              : current.mode === "disabled"
                ? "사용 안 함"
                : decision?.enabled
                  ? "활성"
                  : "수동"}
          </StatusPill>
        </div>
        <div className="grid grid-cols-[72px_1fr] gap-y-1 text-[10px]">
          <span className="text-muted-foreground">다음 실행</span>
          <span className="font-mono">
            {sync.status === "paused"
              ? "재개 전까지 자동 실행 안 함"
              : decision?.due
              ? "실행 대기"
              : decision?.nextDueAt
              ? formatTimestamp(decision.nextDueAt)
              : current.mode === "disabled"
                ? "중지됨"
                : "빌드 버튼으로 실행"}
          </span>
          <span className="text-muted-foreground">판단</span>
          <span className="font-mono">{decision?.reason ?? "manual_schedule"}</span>
          <span className="text-muted-foreground">실패 보호</span>
          <span className="font-mono">
            {decision?.consecutiveFailureCount ?? 0}/{current.autoPauseAfterFailures}회
          </span>
        </div>
      </div>
    </div>
  );
}

function buildSchedule(
  mode: ScheduleMode,
  everySecondsText: string,
  cronText: string,
  batchLimitText: string,
  autoPauseAfterFailuresText: string,
): Record<string, unknown> | string {
  const schedule: Record<string, unknown> = { mode };
  if (mode === "interval") {
    const everySeconds = Number.parseInt(everySecondsText, 10);
    if (!Number.isInteger(everySeconds) || everySeconds <= 0) {
      return "실행 간격은 1 이상의 정수여야 합니다.";
    }
    schedule.everySeconds = everySeconds;
  }
  if (mode === "cron") {
    const cron = cronText.trim();
    if (cron.split(/\s+/).length !== 5) {
      return "Cron 표현식은 ‘분 시 일 월 요일’의 다섯 필드여야 합니다.";
    }
    schedule.cron = cron;
  }
  if (batchLimitText.trim()) {
    const batchLimit = Number.parseInt(batchLimitText, 10);
    if (!Number.isInteger(batchLimit) || batchLimit <= 0) {
      return "최대 행 수는 1 이상의 정수여야 합니다.";
    }
    schedule.batchLimit = batchLimit;
  }
  if (mode === "interval" || mode === "cron") {
    const threshold = Number.parseInt(autoPauseAfterFailuresText, 10);
    if (!Number.isInteger(threshold) || threshold < 1 || threshold > 10) {
      return "자동 일시정지 횟수는 1~10 사이의 정수여야 합니다.";
    }
    schedule.autoPauseAfterFailures = threshold;
  }
  return schedule;
}
