import type { PipelineVersion } from "@foundry-lite/sdk";
import { CalendarClock, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useState } from "react";

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

/** 실행·배포 탭 스케줄 카드: 조회/생성(cron·interval)/삭제 + previewDue evidence. */
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
  const [versionId, setVersionId] = useState<string | null>(null);

  const schedule = scheduleQuery.data ?? null;
  const defaultVersionId =
    versions.find((version) => version.deployedAt)?.id ??
    versions[0]?.id ??
    null;
  const effectiveVersionId = versionId ?? defaultVersionId;
  const dueItems = dueQuery.data?.items ?? [];
  const isDue = dueItems.some((item) => item.pipelineId === pipelineId);

  const intervalValue = Number(intervalMinutes);
  const isFormValid =
    Boolean(effectiveVersionId) &&
    (mode === "cron"
      ? cron.trim().length > 0
      : Number.isFinite(intervalValue) && intervalValue > 0);

  const handleUpsert = async () => {
    if (!effectiveVersionId || !isFormValid) return;
    const definition =
      mode === "cron"
        ? { type: "cron", cron: cron.trim() }
        : { type: "interval", intervalMinutes: intervalValue };
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
        {schedule ? (
          <StatusPill
            intent={schedule.enabled === true ? "success" : "neutral"}
          >
            <CalendarClock className="size-3" />
            {schedule.enabled === true ? "활성" : "비활성"}
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
      </div>

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
            <ScheduleEvidenceRow
              label="갱신"
              value={`${asText(schedule.updatedBy) ?? "-"} · ${formatTimestamp(schedule.updatedAt)}`}
            />
            <ScheduleEvidenceRow
              label="마지막 tick"
              value={formatTimestamp(schedule.lastTickAt)}
            />
          </div>
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
        </>
      ) : (
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
            <Select
              value={effectiveVersionId ?? undefined}
              onValueChange={setVersionId}
            >
              <SelectTrigger size="sm" className="h-7 w-44 text-[11px]">
                <SelectValue placeholder="버전 선택" />
              </SelectTrigger>
              <SelectContent>
                {versions.map((version) => (
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
              {actions.upsertSchedule.isRunning ? "생성 중..." : "스케줄 생성"}
            </Button>
          </div>
          {versions.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              스케줄은 버전에 연결됩니다. 제안을 적용해 버전을 먼저 만드세요.
            </p>
          ) : null}
        </div>
      )}

      <div className="flex items-center gap-2">
        <span className="section-label">다음 실행 예정</span>
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
              <StatusPill intent="success">다음 tick에 실행 예정</StatusPill>
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
