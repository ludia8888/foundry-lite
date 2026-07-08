import { cn } from "@/lib/utils";

import type { ApprovalSource } from "../lib/approvals-view";
import { APPROVAL_SOURCE_LABELS } from "../lib/approvals-view";

const SOURCES: ApprovalSource[] = ["insights", "ontology", "pipelines"];

interface SourceTabsProps {
  source: ApprovalSource;
  pendingCounts: Record<ApprovalSource, number>;
  onChange: (source: ApprovalSource) => void;
}

/** 3개 소스 탭: 인사이트 리뷰 / 온톨로지 제안 / 파이프라인 제안 + 대기 카운트. */
export function SourceTabs({
  source,
  pendingCounts,
  onChange,
}: SourceTabsProps) {
  return (
    <div className="flex items-center gap-1 border-b">
      {SOURCES.map((value) => {
        const isActive = source === value;
        return (
          <button
            key={value}
            type="button"
            onClick={() => onChange(value)}
            className={cn(
              "relative flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium",
              isActive
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {APPROVAL_SOURCE_LABELS[value]}
            {pendingCounts[value] > 0 ? (
              <span className="rounded bg-warning/15 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-warning">
                {pendingCounts[value]}
              </span>
            ) : null}
            {isActive ? (
              <span className="absolute inset-x-0 -bottom-px h-0.5 bg-primary" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
