import { GitPullRequestArrow } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import type { ApprovalQueueItem } from "../lib/approvals-view";
import { formatDate } from "../lib/approvals-view";

interface QueueRowProps {
  item: ApprovalQueueItem;
  isSelected: boolean;
  onSelect: (id: string) => void;
}

/** Palantir Approvals inbox 행: 제목 링크 + 상태 점 + 리소스 경로 + 상태 필. */
export function QueueRow({ item, isSelected, onSelect }: QueueRowProps) {
  return (
    <button
      type="button"
      onClick={() => onSelect(item.id)}
      className={cn(
        "flex w-full items-start gap-2.5 border-b px-3 py-2.5 text-left last:border-b-0",
        "hover:bg-muted/50",
        isSelected && "bg-accent hover:bg-accent",
      )}
    >
      <GitPullRequestArrow className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13px] font-medium text-primary">
            {item.title}
          </span>
          {item.isPending ? (
            <span
              className="size-1.5 shrink-0 rounded-full bg-warning"
              aria-label="리뷰 대기"
            />
          ) : null}
          {item.priority === "high" ? (
            <StatusPill intent="warning" className="shrink-0">
              높음
            </StatusPill>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
          <span>제출자 {item.submittedBy}</span>
          <span aria-hidden>·</span>
          <span>{formatDate(item.createdAt)}</span>
          {item.assignee ? (
            <>
              <span aria-hidden>·</span>
              <span>리뷰어 {item.assignee}</span>
            </>
          ) : null}
        </div>
        {item.resourcePath ? (
          <div className="truncate font-mono text-[11px] text-muted-foreground/80">
            {item.resourcePath}
          </div>
        ) : null}
      </div>
      <StatusPill intent={item.statusIntent} className="mt-0.5 shrink-0">
        {item.statusLabel}
      </StatusPill>
    </button>
  );
}
