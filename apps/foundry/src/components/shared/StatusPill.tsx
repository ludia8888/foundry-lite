import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type StatusIntent =
  "success" | "warning" | "danger" | "info" | "neutral";

const INTENT_CLASSES: Record<StatusIntent, string> = {
  success: "bg-success/10 text-success",
  warning: "bg-warning/10 text-warning",
  danger: "bg-destructive/10 text-destructive",
  info: "bg-primary/10 text-primary",
  neutral: "bg-muted text-muted-foreground",
};

interface StatusPillProps {
  intent: StatusIntent;
  children: ReactNode;
  className?: string;
}

/** Palantir식 intent 필: 연한 배경 + intent 컬러 텍스트. */
export function StatusPill({ intent, children, className }: StatusPillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap",
        INTENT_CLASSES[intent],
        className,
      )}
    >
      {children}
    </span>
  );
}
