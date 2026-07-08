import { Inbox } from "lucide-react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { cn } from "@/lib/utils";

import type { ApprovalIntent, ApprovalQueueItem } from "../lib/approvals-view";
import { APPROVAL_INTENT_LABELS } from "../lib/approvals-view";
import { QueueRow } from "./QueueRow";

const INTENTS: ApprovalIntent[] = ["pending", "decided", "all"];

interface QueueListProps {
  items: ApprovalQueueItem[];
  intent: ApprovalIntent;
  intentCounts: Record<ApprovalIntent, number>;
  selectedId: string | null;
  isLoading: boolean;
  error: unknown;
  onChangeIntent: (intent: ApprovalIntent) => void;
  onSelect: (id: string) => void;
  onRetry: () => void;
}

/** 통합 큐 리스트: intent 필터 세그먼트 + 스크롤 리스트. */
export function QueueList({
  items,
  intent,
  intentCounts,
  selectedId,
  isLoading,
  error,
  onChangeIntent,
  onSelect,
  onRetry,
}: QueueListProps) {
  return (
    <div className="flex min-h-0 flex-col rounded border bg-card">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
        <span className="section-label">리뷰 큐 ({items.length})</span>
        <div className="flex items-center gap-0.5 rounded border bg-muted/40 p-0.5">
          {INTENTS.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => onChangeIntent(value)}
              className={cn(
                "rounded px-2 py-1 text-[11px] font-medium",
                intent === value
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {APPROVAL_INTENT_LABELS[value]}
              <span className="ml-1 tabular-nums text-muted-foreground">
                {intentCounts[value]}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading ? (
          <div className="p-3">
            <LoadingState rowCount={6} />
          </div>
        ) : error ? (
          <div className="p-3">
            <ErrorState error={error} onRetry={onRetry} />
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={Inbox}
            title="리뷰할 항목이 없습니다"
            description="선택한 소스와 필터에 해당하는 제안이 없습니다. 다른 소스 탭이나 필터를 확인하세요."
            className="m-3"
          />
        ) : (
          items.map((item) => (
            <QueueRow
              key={item.id}
              item={item}
              isSelected={item.id === selectedId}
              onSelect={onSelect}
            />
          ))
        )}
      </div>
    </div>
  );
}
