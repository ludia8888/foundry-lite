import { ArrowLeft, DatabaseZap } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface WizardStepMeta {
  id: string;
  title: string;
}

/**
 * 위저드 공통 레이아웃: 상단 소스 헤더(뒤로/취소) + 좌측 번호 스테퍼 레일 +
 * 우측 스텝 콘텐츠 (Palantir network policy 위저드 구조).
 */
export function WizardStepLayout({
  title,
  subtitle,
  steps,
  activeIndex,
  onBack,
  onCancel,
  children,
}: {
  title: string;
  subtitle: string;
  steps: readonly WizardStepMeta[];
  activeIndex: number;
  onBack?: () => void;
  onCancel: () => void;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b px-4 py-2">
        {onBack ? (
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={onBack}
            aria-label="이전 단계로"
          >
            <ArrowLeft className="size-4" />
          </Button>
        ) : null}
        <span className="flex size-9 items-center justify-center rounded bg-primary/10">
          <DatabaseZap className="size-4 text-primary" />
        </span>
        <div className="min-w-0">
          <div className="truncate text-[13px] font-semibold">{title}</div>
          <div className="truncate text-[11px] text-muted-foreground">
            {subtitle}
          </div>
        </div>
        <div className="ml-auto">
          <Button variant="outline" size="sm" onClick={onCancel}>
            취소
          </Button>
        </div>
      </div>
      <div className="flex min-h-0 flex-1">
        <nav className="w-52 shrink-0 border-r bg-muted/30">
          <ol>
            {steps.map((step, index) => (
              <li key={step.id}>
                <div
                  className={cn(
                    "flex items-center gap-2.5 px-4 py-3",
                    index === activeIndex && "bg-background",
                  )}
                >
                  <span
                    className={cn(
                      "flex size-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                      index < activeIndex && "bg-success/15 text-success",
                      index === activeIndex &&
                        "bg-primary text-primary-foreground",
                      index > activeIndex && "bg-muted text-muted-foreground",
                    )}
                  >
                    {index + 1}
                  </span>
                  <span
                    className={cn(
                      "text-xs",
                      index === activeIndex
                        ? "font-medium text-primary"
                        : "text-muted-foreground",
                    )}
                  >
                    {step.title}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </nav>
        <div className="min-w-0 flex-1 overflow-y-auto">
          <div className="max-w-3xl space-y-4 p-6">{children}</div>
        </div>
      </div>
    </div>
  );
}
