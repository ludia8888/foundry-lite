import { Check, CircleDashed, Loader2, MinusCircle, X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type PhaseStatus = "done" | "active" | "failed" | "pending" | "skipped";

export interface PhaseItem {
  id: string;
  label: string;
  status: PhaseStatus;
  detail?: string;
}

export interface PhaseFlag {
  id: string;
  label: string;
  isDone: boolean;
  isSkipped?: boolean;
  detail?: string;
}

/**
 * recipe 상태의 artifact 존재 여부로 단계 상태를 계산한다.
 * 완료되지 않은 첫 단계는 에러 시 failed, 실행 중이면 active가 된다.
 */
export function resolvePhaseItems(
  flags: readonly PhaseFlag[],
  context: { hasError: boolean; isRunning: boolean },
): PhaseItem[] {
  let hasMarkedCurrent = false;
  return flags.map((flag) => {
    if (flag.isSkipped) {
      return { id: flag.id, label: flag.label, status: "skipped" as const };
    }
    if (flag.isDone) {
      return {
        id: flag.id,
        label: flag.label,
        status: "done" as const,
        detail: flag.detail,
      };
    }
    if (!hasMarkedCurrent) {
      hasMarkedCurrent = true;
      const status: PhaseStatus = context.hasError
        ? "failed"
        : context.isRunning
          ? "active"
          : "pending";
      return { id: flag.id, label: flag.label, status, detail: flag.detail };
    }
    return { id: flag.id, label: flag.label, status: "pending" as const };
  });
}

const PHASE_ICON_CLASSES: Record<PhaseStatus, string> = {
  done: "bg-success/10 text-success",
  active: "bg-primary/10 text-primary",
  failed: "bg-destructive/10 text-destructive",
  pending: "bg-muted text-muted-foreground",
  skipped: "bg-muted text-muted-foreground/60",
};

function PhaseIcon({ status }: { status: PhaseStatus }) {
  if (status === "done") return <Check className="size-3" />;
  if (status === "active") return <Loader2 className="size-3 animate-spin" />;
  if (status === "failed") return <X className="size-3" />;
  if (status === "skipped") return <MinusCircle className="size-3" />;
  return <CircleDashed className="size-3" />;
}

/** run 진행 증거 타임라인: recipe phase/artifact 기반 단계 표시. */
export function PhaseTimeline({ items }: { items: readonly PhaseItem[] }) {
  return (
    <ol className="space-y-0">
      {items.map((item, index) => (
        <li key={item.id} className="relative flex gap-3 pb-3 last:pb-0">
          {index < items.length - 1 ? (
            <span
              aria-hidden
              className="absolute top-6 left-[11px] h-[calc(100%-1.25rem)] w-px bg-border"
            />
          ) : null}
          <span
            className={cn(
              "z-10 mt-0.5 flex size-[23px] shrink-0 items-center justify-center rounded-full",
              PHASE_ICON_CLASSES[item.status],
            )}
          >
            <PhaseIcon status={item.status} />
          </span>
          <div className="min-w-0 pt-1">
            <div
              className={cn(
                "text-xs font-medium",
                item.status === "pending" && "text-muted-foreground",
                item.status === "skipped" && "text-muted-foreground/60",
                item.status === "failed" && "text-destructive",
              )}
            >
              {item.label}
              {item.status === "skipped" ? (
                <span className="ml-1.5 font-normal text-muted-foreground/60">
                  건너뜀
                </span>
              ) : null}
            </div>
            {item.detail ? (
              <div className="truncate font-mono text-[11px] text-muted-foreground">
                {item.detail}
              </div>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** 증거 key-value 행: 라벨 + 모노 값(+옵션 노드). */
export function EvidenceRow({
  label,
  value,
  children,
}: {
  label: string;
  value?: string | number | null;
  children?: ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-3 py-1">
      <dt className="w-32 shrink-0 text-[11px] text-muted-foreground">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 truncate font-mono text-[11px]">
        {children ??
          (value !== null && value !== undefined ? String(value) : "—")}
      </dd>
    </div>
  );
}

export function EvidenceList({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded border bg-card p-3", className)}>
      <div className="section-label mb-2">{title}</div>
      <dl className="divide-y divide-border/60">{children}</dl>
    </div>
  );
}
