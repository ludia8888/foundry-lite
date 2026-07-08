import type {
  FoundryLiteApiError,
  ObservabilitySeverity,
  StoredObservabilityIncident,
} from "@foundry-lite/sdk";
import {
  normalizeFoundryLiteError,
  retryWithBackoff,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { AlertTriangle, Bell, CheckCircle2, RotateCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { formatTimestamp, severityMeta } from "@/features/operations/operations-format";

function severityRank(severity: ObservabilitySeverity): number {
  if (severity === "critical") return 0;
  if (severity === "warning") return 1;
  return 2;
}

function sortIncidents(
  incidents: StoredObservabilityIncident[],
): StoredObservabilityIncident[] {
  return [...incidents].sort((left, right) => {
    const severityDiff =
      severityRank(left.severity) - severityRank(right.severity);
    if (severityDiff !== 0) return severityDiff;
    return right.lastObservedAt.localeCompare(left.lastObservedAt);
  });
}

interface NotificationsButtonProps {
  variant?: "topbar" | "sidebar";
}

/** 알림 진입점 — Operations observability incidents를 서버에서 읽어 표시한다. */
export function NotificationsButton({
  variant = "topbar",
}: NotificationsButtonProps) {
  const client = useFoundryLiteClient();
  const [incidents, setIncidents] = useState<StoredObservabilityIncident[]>([]);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    setIsLoading(true);
    try {
      const openIncidents = await retryWithBackoff(() =>
        client.operations.observability.listIncidents({
          status: "open",
          limit: 10,
        }),
      );
      if (!isMountedRef.current) return;
      setIncidents(sortIncidents(openIncidents));
      setError(null);
    } catch (caught) {
      if (isMountedRef.current) setError(normalizeFoundryLiteError(caught));
    } finally {
      if (isMountedRef.current) setIsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const incidentCount = incidents.length;
  const topSeverity = useMemo(
    () => incidents[0]?.severity ?? null,
    [incidents],
  );
  const topMeta = topSeverity ? severityMeta(topSeverity) : null;
  const isSidebar = variant === "sidebar";
  const ariaLabel =
    incidentCount > 0 ? `운영 알림 ${incidentCount}건` : "운영 알림";

  return (
    <Popover>
      <PopoverTrigger asChild>
        {isSidebar ? (
          <button
            type="button"
            aria-label={ariaLabel}
            className="flex w-full items-center gap-2.5 rounded px-2 py-1.5 text-[13px] text-sidebar-foreground/85 hover:bg-sidebar-accent hover:text-sidebar-foreground"
          >
            <span className="flex size-4 shrink-0 items-center justify-center">
              <Bell className="size-4" />
            </span>
            <span className="min-w-0 flex-1 truncate text-left">
              Notifications
            </span>
            {incidentCount > 0 ? (
              <span
                className={cn(
                  "shrink-0 rounded px-1 font-mono text-[10px] leading-4 font-semibold",
                  topSeverity === "critical"
                    ? "bg-destructive text-destructive-foreground"
                    : "bg-warning text-warning-foreground",
                )}
              >
                {incidentCount}
              </span>
            ) : null}
          </button>
        ) : (
          <button
            type="button"
            aria-label={ariaLabel}
            className="relative flex size-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <Bell className="size-3.5" />
            {incidentCount > 0 ? (
              <span
                className={cn(
                  "absolute -top-0.5 -right-0.5 rounded px-1 text-[8px] leading-3 font-semibold",
                  topSeverity === "critical"
                    ? "bg-destructive text-destructive-foreground"
                    : "bg-warning text-warning-foreground",
                )}
              >
                {incidentCount}
              </span>
            ) : null}
          </button>
        )}
      </PopoverTrigger>
      <PopoverContent
        align={isSidebar ? "start" : "end"}
        side={isSidebar ? "right" : "bottom"}
        className="w-80 p-3"
      >
        <div className="flex items-center gap-2">
          <div className="text-[13px] font-semibold">운영 알림</div>
          {isLoading ? (
            <StatusPill intent="neutral">동기화 중</StatusPill>
          ) : topMeta ? (
            <StatusPill intent={topMeta.intent}>{topMeta.label}</StatusPill>
          ) : (
            <StatusPill intent="success">정상</StatusPill>
          )}
          <button
            type="button"
            aria-label="운영 알림 새로고침"
            onClick={() => void reload()}
            className="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <RotateCw className={cn("size-3.5", isLoading && "animate-spin")} />
          </button>
        </div>
        <div className="mt-2 text-[11px] text-muted-foreground">
          Operations observability open incidents
        </div>
        {error ? (
          <div className="mt-3 rounded border border-destructive/30 bg-destructive/5 p-2">
            <div className="flex items-center gap-1.5 text-[12px] font-semibold text-destructive">
              <AlertTriangle className="size-3.5" />
              알림 동기화 실패
            </div>
            <div className="mt-1 font-mono text-[10px] break-words text-muted-foreground">
              code={error.code}
              {error.requestId ? ` · request_id=${error.requestId}` : ""}
            </div>
          </div>
        ) : incidentCount > 0 ? (
          <div className="mt-3 space-y-2">
            {incidents.slice(0, 5).map((incident) => {
              const meta = severityMeta(incident.severity);
              return (
                <div
                  key={incident.id}
                  className="rounded border bg-canvas px-2.5 py-2"
                >
                  <div className="flex items-center gap-1.5">
                    <StatusPill intent={meta.intent}>{meta.label}</StatusPill>
                    <span className="truncate text-[12px] font-medium">
                      {incident.detectorId}
                    </span>
                    <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                      x{incident.occurrenceCount}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[11px] text-foreground/80">
                    {incident.message}
                  </p>
                  <div className="mt-1 flex items-center justify-between gap-2 font-mono text-[10px] text-muted-foreground">
                    <span className="truncate">{incident.owner}</span>
                    <span>{formatTimestamp(incident.lastObservedAt)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="mt-3 flex items-start gap-2 rounded border bg-canvas p-2">
            <CheckCircle2 className="mt-0.5 size-3.5 text-success" />
            <div>
              <div className="text-[12px] font-medium">open incident 없음</div>
              <div className="mt-0.5 text-[11px] text-muted-foreground">
                현재 테넌트의 운영 인시던트 큐가 비어 있습니다.
              </div>
            </div>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
