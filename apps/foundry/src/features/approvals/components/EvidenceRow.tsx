import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface EvidenceRowProps {
  label: string;
  children: ReactNode;
  isMono?: boolean;
  className?: string;
}

/** 증거성 key/value 행: 좌측 라벨 + 우측 값(모노). */
export function EvidenceRow({
  label,
  children,
  isMono = true,
  className,
}: EvidenceRowProps) {
  return (
    <div
      className={cn(
        "flex items-baseline justify-between gap-3 border-b py-1.5 last:border-b-0",
        className,
      )}
    >
      <span className="shrink-0 text-[11px] text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "min-w-0 truncate text-right text-[12px] text-foreground",
          isMono && "font-mono text-[11px]",
        )}
      >
        {children}
      </span>
    </div>
  );
}
