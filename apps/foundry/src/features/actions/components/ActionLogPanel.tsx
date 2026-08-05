import type { ActionLogListResult } from "@foundry-lite/sdk";
import { ExternalLink } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";

export function ActionLogPanel({
  result,
  onOpenObject,
}: {
  result: ActionLogListResult | null;
  onOpenObject: (objectType: string, objectId: string) => void;
}) {
  if (!result) {
    return <div className="rounded border bg-card p-3 text-xs text-muted-foreground">Action Log 로딩 중…</div>;
  }
  return (
    <section aria-label="Action Log" className="space-y-3 rounded border bg-card p-3">
      <div>
        <div className="section-label">Action Log · 운영 지표</div>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          제출 한 번당 로그 한 건과 모든 편집 객체를 연결합니다.
        </p>
      </div>
      <MonitoringCards result={result} />
      <MonitoringAlerts result={result} />
      <div className="max-h-[32rem] space-y-2 overflow-y-auto">
        {result.items.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">기록된 Action Log가 없습니다.</p>
        ) : (
          result.items.map((entry) => (
            <article key={entry.logEntryId} className="space-y-1.5 rounded border p-2 text-[11px]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{entry.actionApiName}</span>
                <StatusPill intent={entry.status === "succeeded" ? "success" : "neutral"}>{entry.status}</StatusPill>
              </div>
              <div className="truncate font-mono text-muted-foreground">{entry.actionRunId}</div>
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
                <span>actor={entry.actorUserId}</span>
                <span>edits={entry.editedObjects.length}</span>
                <span>effects={entry.effectReceiptCount}</span>
                <span>revert={entry.revert.status}</span>
              </div>
              {entry.editedObjects.length > 0 ? (
                <div className="space-y-0.5 border-t pt-1.5">
                  {entry.editedObjects.map((edited) => (
                    <button
                      key={edited.objectEditId}
                      type="button"
                      className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left font-mono text-primary hover:bg-muted"
                      aria-label={`객체 열기: ${edited.objectType}/${edited.objectId}`}
                      onClick={() => onOpenObject(edited.objectType, edited.objectId)}
                    >
                      {edited.ordinal + 1}. {edited.operation} · {edited.objectType}/{edited.objectId}
                      <ExternalLink className="ml-auto size-3 shrink-0" />
                    </button>
                  ))}
                </div>
              ) : null}
            </article>
          ))
        )}
      </div>
    </section>
  );
}

function MonitoringAlerts({ result }: { result: ActionLogListResult }) {
  const alerts = result.monitoring.alerts.active;
  if (alerts.length === 0) {
    return (
      <p className="rounded border border-emerald-500/30 bg-emerald-500/5 px-2 py-1.5 text-[11px] text-emerald-700">
        최근 {result.monitoring.window.days}일 Action 경보 없음
      </p>
    );
  }
  return (
    <div role="alert" className="space-y-1 rounded border border-destructive/30 bg-destructive/5 p-2 text-[11px]">
      <div className="font-medium text-destructive">활성 Action 경보 {alerts.length}건</div>
      {alerts.map((alert) => (
        <div key={alert.policyId} className="font-mono text-muted-foreground">
          {alert.policyId}: {alert.value} &gt; {alert.threshold}
        </div>
      ))}
    </div>
  );
}

function MonitoringCards({ result }: { result: ActionLogListResult }) {
  const monitoring = result.monitoring;
  const cards = [
    ["p95 duration", monitoring.durationMs.p95 === null ? "—" : `${Math.round(monitoring.durationMs.p95)} ms`],
    ["failure rate", `${(monitoring.failure.rate * 100).toFixed(1)}%`],
    ["effect backlog", String(monitoring.effects.deliveryBacklog)],
    ["DLQ / unknown", `${monitoring.effects.deadLetter} / ${monitoring.effects.outcomeUnknown}`],
  ];
  return (
    <div className="grid grid-cols-2 gap-2 text-[11px] xl:grid-cols-4">
      {cards.map(([label, value]) => (
        <div key={label} className="rounded border bg-muted/20 p-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
          <div className="mt-0.5 font-mono font-medium">{value}</div>
        </div>
      ))}
    </div>
  );
}
