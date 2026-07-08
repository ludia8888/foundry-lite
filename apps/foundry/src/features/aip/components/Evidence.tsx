import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** section-label + 헤어라인: Palantir 카드 섹션 헤더. */
export function SectionLabel({
  children,
  right,
  className,
}: {
  children: ReactNode;
  right?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-2", className)}>
      <span className="section-label">{children}</span>
      {right ? <div className="flex items-center gap-1.5">{right}</div> : null}
    </div>
  );
}

/** 좌측 라벨 / 우측 값 key-value 행 (Palantir Details grid). */
export function EvidenceRow({
  label,
  value,
  mono = false,
  title,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
  title?: string;
}) {
  return (
    <div className="flex items-baseline gap-3 py-0.5">
      <span className="w-32 shrink-0 text-[11px] text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "min-w-0 flex-1 break-words text-[12px] text-foreground",
          mono && "font-mono text-[11px]",
        )}
        title={title}
      >
        {value}
      </span>
    </div>
  );
}

/** mono evidence chip (request id / hash / run id). */
export function MonoChip({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <code
      className={cn(
        "inline-block max-w-full truncate rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground/80",
        className,
      )}
    >
      {children}
    </code>
  );
}
