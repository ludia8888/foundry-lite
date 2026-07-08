import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

import type { ProposalStepView } from "../lib/ontology-view";

interface ProposalStepperProps {
  steps: readonly ProposalStepView[];
  className?: string;
}

/** 번호 원형 스테퍼: 준비 → 리뷰 중 → 머지. 활성 단계는 연파랑 배경. */
export function ProposalStepper({ steps, className }: ProposalStepperProps) {
  return (
    <div className={cn("rounded border bg-card p-1.5", className)}>
      {steps.map((step, index) => (
        <div
          key={step.id}
          className={cn(
            "flex items-start gap-2 rounded p-2",
            step.state === "active" && "bg-primary/10",
          )}
        >
          <div
            className={cn(
              "flex size-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold",
              step.state === "done" &&
                "border-primary bg-primary text-primary-foreground",
              step.state === "active" &&
                "border-primary bg-primary text-primary-foreground",
              step.state === "pending" &&
                "border-muted-foreground/40 text-muted-foreground",
            )}
          >
            {step.state === "done" ? <Check className="size-3" /> : index + 1}
          </div>
          <div className="min-w-0">
            <div
              className={cn(
                "text-xs font-medium",
                step.state === "active" && "text-primary",
                step.state === "pending" && "text-muted-foreground",
              )}
            >
              {step.label}
            </div>
            {step.description ? (
              <div className="text-[11px] text-muted-foreground">
                {step.description}
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
