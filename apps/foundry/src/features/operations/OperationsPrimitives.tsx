import type { ReactNode } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

/** 라벨 + mono 값 evidence 행. value가 없으면 em dash. */
export function EvidenceRow({
  label,
  value,
  mono = true,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="section-label shrink-0">{label}</span>
      <span
        className={cn(
          "min-w-0 truncate text-right text-[12px] text-foreground",
          mono && "font-mono text-[11px]",
        )}
      >
        {value ?? "—"}
      </span>
    </div>
  );
}

/** 카드 상단 섹션 헤더 + 옵션 액션. */
export function SectionHeader({
  label,
  hint,
  actions,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-2", className)}>
      <div className="flex min-w-0 items-center gap-2">
        <span className="section-label">{label}</span>
        {hint ? (
          <span className="truncate text-[11px] text-muted-foreground">
            {hint}
          </span>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-1.5">{actions}</div>
      ) : null}
    </div>
  );
}

/** 숫자 요약 통계 셀 (readiness overview / board 배지). */
export function StatTile({
  label,
  value,
  intent = "neutral",
}: {
  label: string;
  value: ReactNode;
  intent?: "neutral" | "danger" | "warning" | "success" | "info";
}) {
  const valueColor: Record<string, string> = {
    neutral: "text-foreground",
    danger: "text-destructive",
    warning: "text-warning",
    success: "text-success",
    info: "text-primary",
  };
  return (
    <div className="rounded border bg-card px-3 py-2">
      <div className="section-label">{label}</div>
      <div
        className={cn(
          "mt-0.5 text-xl font-semibold tabular-nums",
          valueColor[intent],
        )}
      >
        {value}
      </div>
    </div>
  );
}

/** 백엔드 미구현 영역을 정직하게 표시하는 future 배지. */
export function FutureBadge({ children }: { children?: ReactNode }) {
  return (
    <StatusPill intent="neutral" className="border border-dashed">
      future{children ? ` · ${children}` : ""}
    </StatusPill>
  );
}

/** mutation 결과 evidence 라인: idempotency key / request id / 결과 상태. */
export function MutationEvidence({
  idempotencyKey,
  requestId,
  children,
  className,
}: {
  idempotencyKey?: string | null;
  requestId?: string | null;
  children?: ReactNode;
  className?: string;
}) {
  if (!idempotencyKey && !requestId && !children) return null;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded border bg-muted/40 px-2.5 py-1.5 font-mono text-[11px] text-muted-foreground",
        className,
      )}
    >
      {idempotencyKey ? <span>idempotency-key={idempotencyKey}</span> : null}
      {requestId ? <span>request_id={requestId}</span> : null}
      {children}
    </div>
  );
}
