import type { LucideIcon } from "lucide-react";
import { Plus } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface PathBoardProps {
  icon: LucideIcon;
  label: string;
  /** header 우측 액션/뱃지 슬롯. */
  actions?: ReactNode;
  /** 활성(선택) board는 blue accent border를 갖는다 (Contour 문법). */
  isActive?: boolean;
  children: ReactNode;
  className?: string;
}

/**
 * Contour식 analysis path board 프레임.
 * [아이콘 + 대문자 board 라벨(좌)] · [액션(우)] header 행 +
 * hairline 구분 + 본문. 활성 board는 blue accent border.
 * 참고: contour boards-editing-paths / board-descriptions-distribution 스크린샷.
 */
export function PathBoard({
  icon: Icon,
  label,
  actions,
  isActive = false,
  children,
  className,
}: PathBoardProps) {
  return (
    <section
      className={cn(
        "overflow-hidden rounded border bg-card",
        isActive && "border-primary ring-1 ring-primary/30",
        className,
      )}
    >
      <header className="flex h-8 items-center justify-between gap-2 border-b bg-muted/40 px-2.5">
        <div className="flex min-w-0 items-center gap-1.5">
          <Icon
            className={cn(
              "size-3.5 shrink-0",
              isActive ? "text-primary" : "text-muted-foreground",
            )}
          />
          <span className="section-label truncate text-[10.5px] text-foreground/70">
            {label}
          </span>
        </div>
        {actions ? (
          <div className="flex shrink-0 items-center gap-1.5">{actions}</div>
        ) : null}
      </header>
      {children}
    </section>
  );
}

/** board 사이 "+" 연결 노드 (Contour path connector). */
export function PathConnector() {
  return (
    <div className="relative flex h-6 items-center justify-center">
      <div className="absolute top-0 bottom-0 left-1/2 w-px -translate-x-1/2 bg-border" />
      <span className="relative flex size-4 items-center justify-center rounded-full border bg-card text-muted-foreground">
        <Plus className="size-2.5" />
      </span>
    </div>
  );
}
