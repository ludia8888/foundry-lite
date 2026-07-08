import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Palantir 위저드 폼 필드: 굵은 라벨 + 회색 헬퍼 텍스트 + 입력 + 필드 에러. */
export function WizardField({
  label,
  helper,
  error,
  children,
  className,
}: {
  label: string;
  helper?: string;
  error?: string | null;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1", className)}>
      <div className="text-xs font-semibold">{label}</div>
      {helper ? (
        <div className="text-[11px] text-muted-foreground">{helper}</div>
      ) : null}
      {children}
      {error ? (
        <div className="text-[11px] text-destructive">{error}</div>
      ) : null}
    </div>
  );
}

/** 위저드 스텝 하단 네비게이션 (좌: 이전, 우: 다음/실행). */
export function WizardStepFooter({
  left,
  right,
}: {
  left?: ReactNode;
  right: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between border-t pt-4">
      <div>{left}</div>
      <div className="flex items-center gap-2">{right}</div>
    </div>
  );
}
