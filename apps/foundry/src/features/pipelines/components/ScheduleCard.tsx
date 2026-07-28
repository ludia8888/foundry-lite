import type { PipelineScheduleSpec, PipelineVersion } from "@foundry-lite/sdk";
import {
  CalendarClock,
  CirclePause,
  CirclePlay,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { asText, formatTimestamp, scheduleLabel } from "../pipeline-model";
import type { PipelineActions } from "../use-pipeline-actions";
import { useSafeQuery } from "../use-safe-query";

interface ScheduleCardProps {
  pipelineId: string;
  versions: readonly PipelineVersion[];
  actions: PipelineActions;
}

type ScheduleMode = "cron" | "interval";

/** Cron/interval timing, state, lease/fencing evidence를 함께 보여주는 bounded scheduler 카드. */
export function ScheduleCard({
  pipelineId,
  versions,
  actions,
}: ScheduleCardProps) {
  const loadSchedule = useCallback(
    () => actions.recipe.getSchedule(pipelineId),
    [actions.recipe, pipelineId],
  );
  const scheduleQuery = useSafeQuery(
    ["pipelines", "schedule", pipelineId],
    loadSchedule,
  );
  const loadDue = useCallback(
    () => actions.recipe.previewDue({ maxRuns: 50 }),
    [actions.recipe],
  );
  const dueQuery = useSafeQuery(
    ["pipelines", "schedule-due", pipelineId],
    loadDue,
  );

  const [mode, setMode] = useState<ScheduleMode>("cron");
  const [cron, setCron] = useState("0 * * * *");
  const [intervalMinutes, setIntervalMinutes] = useState("60");
  const [timezone, setTimezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [autoPauseFailures, setAutoPauseFailures] = useState("3");
  const [versionId, setVersionId] = useState<string | null>(null);
  const hydratedScheduleId = useRef<string | null>(null);

  const schedule = scheduleQuery.data ?? null;
  const deployedVersions = versions.filter((version) => version.deployedAt);
  const defaultVersionId = deployedVersions[0]?.id ?? null;
  const effectiveVersionId = versionId ?? defaultVersionId;
  const dueItems = dueQuery.data?.items ?? [];
  const isDue = dueItems.some((item) => item.pipelineId === pipelineId);

  useEffect(() => {
    if (!schedule) {
      hydratedScheduleId.current = null;
      return;
    }
    if (hydratedScheduleId.current === schedule.id) return;
    const definition = schedule.schedule;
    setMode(definition.triggerType);
    setTimezone(definition.timezone);
    setVersionId(schedule.versionId);
    setAutoPauseFailures(String(definition.autoPauseAfterFailures ?? 3));
    if (definition.triggerType === "cron") {
      setCron(definition.cronExpression);
    } else {
      setIntervalMinutes(String(definition.intervalSeconds / 60));
    }
    hydratedScheduleId.current = schedule.id;
  }, [schedule]);

  const intervalValue = Number(intervalMinutes);
  const intervalSeconds = Math.round(intervalValue * 60);
  const failureThreshold = Number(autoPauseFailures);
  const isFormValid =
    Boolean(effectiveVersionId) &&
    timezone.trim().length > 0 &&
    Number.isInteger(failureThreshold) &&
    failureThreshold > 0 &&
    (mode === "cron"
      ? cron.trim().length > 0
      : Number.isFinite(intervalValue) && intervalSeconds > 0);

  const handleUpsert = async () => {
    if (!effectiveVersionId || !isFormValid) return;
    const definition: PipelineScheduleSpec =
      mode === "cron"
        ? {
            triggerType: "cron",
            timezone: timezone.trim(),
            cronExpression: cron.trim(),
            autoPauseAfterFailures: failureThreshold,
          }
        : {
            triggerType: "interval",
            timezone: timezone.trim(),
            intervalSeconds,
            autoPauseAfterFailures: failureThreshold,
          };
    const created = await actions.upsertSchedule.execute({
      pipelineId,
      versionId: effectiveVersionId,
      schedule: definition,
    });
    if (created) {
      void scheduleQuery.reload();
      void dueQuery.reload();
    }
  };

  const handlePause = async () => {
    const paused = await actions.pauseSchedule.execute({ pipelineId });
    if (paused) {
      void scheduleQuery.reload();
      void dueQuery.reload();
    }
  };

  const handleResume = async () => {
    const resumed = await actions.resumeSchedule.execute({ pipelineId });
    if (resumed) {
      void scheduleQuery.reload();
      void dueQuery.reload();
    }
  };

  const handleDelete = async () => {
    const deleted = await actions.deleteSchedule.execute({ pipelineId });
    if (deleted) {
      void scheduleQuery.reload();
      void dueQuery.reload();
    }
  };

  if (scheduleQuery.isLoading)
    return <LoadingState rowCount={2} className="p-1" />;
  if (scheduleQuery.error) {
    return (
      <ErrorState
        error={scheduleQuery.error}
        onRetry={() => void scheduleQuery.reload()}
      />
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="section-label">실행 스케줄</span>
        <StatusPill intent="neutral">bounded scheduler</StatusPill>
        {schedule ? (
          <StatusPill
            intent={schedule.status === "active" ? "success" : "warning"}
          >
            <CalendarClock className="size-3" />
            {schedule.status === "active" ? "활성" : "일시정지"}
          </StatusPill>
        ) : (
          <StatusPill intent="neutral">스케줄 없음</StatusPill>
        )}
        {actions.upsertSchedule.error ? (
          <StatusPill intent="danger">
            생성 실패 · {actions.upsertSchedule.error.code}
          </StatusPill>
        ) : null}
        {actions.deleteSchedule.error ? (
          <StatusPill intent="danger">
            삭제 실패 · {actions.deleteSchedule.error.code}
          </StatusPill>
        ) : null}
        {actions.pauseSchedule.error ? (
          <StatusPill intent="danger">
            일시정지 실패 · {actions.pauseSchedule.error.code}
          </StatusPill>
        ) : null}
        {actions.resumeSchedule.error ? (
          <StatusPill intent="danger">
            재개 실패 · {actions.resumeSchedule.error.code}
          </StatusPill>
        ) : null}
      </div>

      <p className="rounded border border-warning/30 bg-warning/5 px-2.5 py-2 text-[11px] text-muted-foreground">
        Cron/간격과 IANA timezone을 계산하고, 다음 실행 시각·lease·fencing·실패
        증거를 영구 저장합니다. 현재 화면은 bounded preview/tick 제어이며 상시
        worker 배포와 장기 장애 복구 운영은 별도 범위입니다.
      </p>

      {schedule ? (
        <>
          <div className="space-y-1 rounded border bg-muted/30 p-2 font-mono text-[11px]">
            <ScheduleEvidenceRow label="schedule id" value={schedule.id} />
            <ScheduleEvidenceRow
              label="버전"
              value={asText(schedule.versionId) ?? "-"}
            />
            <ScheduleEvidenceRow
              label="정의"
              value={scheduleLabel(schedule.schedule)}
            />
            <ScheduleEvidenceRow label="timezone" value={schedule.timezone} />
            <ScheduleEvidenceRow
              label="다음 실행"
              value={formatTimestamp(schedule.nextDueAt)}
            />
            <ScheduleEvidenceRow
              label="갱신"
              value={`${asText(schedule.updatedBy) ?? "-"} · ${formatTimestamp(schedule.updatedAt)}`}
            />
            <ScheduleEvidenceRow
              label="마지막 tick"
              value={formatTimestamp(schedule.lastTickAt)}
            />
            <ScheduleEvidenceRow
              label="fencing / failures"
              value={`${String(schedule.fencingToken)} / ${String(schedule.failureCount)}`}
            />
            <ScheduleEvidenceRow
              label="lease 만료"
              value={formatTimestamp(schedule.leaseExpiresAt)}
            />
            <ScheduleEvidenceRow
              label="마지막 오류"
              value={asText(schedule.lastError?.code) ?? "-"}
            />
          </div>
          <div className="flex items-center gap-1.5">
            {schedule.status === "active" ? (
              <Button
                size="sm"
                variant="outline"
                className="h-6 px-2 text-[11px]"
                disabled={actions.pauseSchedule.isRunning}
                onClick={() => void handlePause()}
              >
                <CirclePause className="size-3" />
                일시정지
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                className="h-6 px-2 text-[11px]"
                disabled={actions.resumeSchedule.isRunning}
                onClick={() => void handleResume()}
              >
                <CirclePlay className="size-3" />
                재개
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[11px] text-destructive"
              disabled={actions.deleteSchedule.isRunning}
              onClick={() => void handleDelete()}
            >
              <Trash2 className="size-3" />
              스케줄 삭제
            </Button>
          </div>
        </>
      ) : null}

      <div className="space-y-2 rounded border bg-muted/30 p-2">
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant={mode === "cron" ? "secondary" : "ghost"}
            className="h-6 px-2 text-[11px]"
            onClick={() => setMode("cron")}
          >
            Cron
          </Button>
          <Button
            size="sm"
            variant={mode === "interval" ? "secondary" : "ghost"}
            className="h-6 px-2 text-[11px]"
            onClick={() => setMode("interval")}
          >
            간격(분)
          </Button>
          {mode === "cron" ? (
            <Input
              className="h-7 w-32 font-mono text-[11px]"
              value={cron}
              onChange={(event) => setCron(event.target.value)}
              placeholder="0 * * * *"
            />
          ) : (
            <Input
              className="h-7 w-20 font-mono text-[11px]"
              value={intervalMinutes}
              onChange={(event) => setIntervalMinutes(event.target.value)}
              placeholder="60"
              inputMode="numeric"
            />
          )}
        </div>
        <div className="flex items-center gap-2">
          <Input
            className="h-7 w-40 font-mono text-[11px]"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
            placeholder="Asia/Seoul"
            aria-label="IANA timezone"
          />
          <Input
            className="h-7 w-28 font-mono text-[11px]"
            value={autoPauseFailures}
            onChange={(event) => setAutoPauseFailures(event.target.value)}
            placeholder="실패 3회"
            inputMode="numeric"
            aria-label="연속 실패 자동 일시정지 기준"
          />
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={effectiveVersionId ?? undefined}
            onValueChange={setVersionId}
          >
            <SelectTrigger size="sm" className="h-7 w-44 text-[11px]">
              <SelectValue placeholder="버전 선택" />
            </SelectTrigger>
            <SelectContent>
              {deployedVersions.map((version) => (
                <SelectItem
                  key={version.id}
                  value={version.id}
                  className="text-[12px]"
                >
                  v{String(version.versionNumber ?? "?")} · {version.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            className="h-7 px-3 text-[12px]"
            disabled={!isFormValid || actions.upsertSchedule.isRunning}
            onClick={() => void handleUpsert()}
          >
            {actions.upsertSchedule.isRunning
              ? "저장 중..."
              : schedule
                ? "설정 업데이트"
                : "스케줄 저장"}
          </Button>
        </div>
        {deployedVersions.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            스케줄은 배포된 버전에 연결됩니다. 버전을 먼저 배포하세요.
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-2">
        <span className="section-label">다음 due 평가</span>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-[11px]"
          disabled={dueQuery.isRefreshing}
          onClick={() => void dueQuery.reload()}
        >
          <RefreshCw className="size-3" />
          새로고침
        </Button>
      </div>
      {dueQuery.error ? (
        <p className="text-[11px] text-destructive">
          previewDue 실패 · {dueQuery.error.code}
        </p>
      ) : (
        <div className="space-y-1 rounded border bg-muted/30 p-2 font-mono text-[11px]">
          <ScheduleEvidenceRow
            label="previewDue"
            value={`due ${dueItems.length}건`}
          />
          <div className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">이 파이프라인</span>
            {isDue ? (
              <StatusPill intent="warning">다음 tick의 실행 후보</StatusPill>
            ) : (
              <StatusPill intent="neutral">예정 없음</StatusPill>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ScheduleEvidenceRow({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate">{value}</span>
    </div>
  );
}
