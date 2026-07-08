import type { FoundryLiteOntologyActionView } from "@foundry-lite/sdk/react";
import { Zap } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

interface ActionTypeListProps {
  actionViews: FoundryLiteOntologyActionView[];
  selectedApiName: string | null;
  onSelect: (actionView: FoundryLiteOntologyActionView) => void;
}

/** 좌측 액션 타입 목록 (온톨로지 카탈로그 actionTypes). */
export function ActionTypeList({
  actionViews,
  selectedApiName,
  onSelect,
}: ActionTypeListProps) {
  return (
    <div className="space-y-2">
      <div className="section-label px-1">액션 타입 ({actionViews.length})</div>
      <div className="space-y-1">
        {actionViews.map((actionView) => {
          const isSelected = actionView.apiName === selectedApiName;
          return (
            <button
              key={actionView.apiName}
              type="button"
              onClick={() => onSelect(actionView)}
              className={cn(
                "flex w-full items-start gap-2 rounded border px-2 py-1.5 text-left transition-colors",
                isSelected
                  ? "border-primary/40 bg-accent"
                  : "border-transparent hover:bg-muted/60",
              )}
            >
              <Zap
                className={cn(
                  "mt-0.5 size-3.5 shrink-0",
                  isSelected ? "text-primary" : "text-muted-foreground",
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">
                  {actionView.displayName}
                </div>
                <div className="truncate font-mono text-[10px] text-muted-foreground">
                  {actionView.apiName} → {actionView.targetObjectApiName}
                </div>
              </div>
              {!actionView.isEnabled ? (
                <StatusPill intent="neutral">비활성</StatusPill>
              ) : !actionView.isGeneratedActionTypeAvailable ? (
                <StatusPill intent="warning">dynamic</StatusPill>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
