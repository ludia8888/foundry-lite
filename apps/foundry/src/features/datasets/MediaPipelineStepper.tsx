import type { FoundryLiteMediaPipelineState } from "@foundry-lite/sdk/react";
import { foundryLiteMediaProcessingFailure } from "@foundry-lite/sdk/react";
import type { LucideIcon } from "lucide-react";
import {
  CheckCircle2,
  Circle,
  Cog,
  GitCommitHorizontal,
  Loader2,
  Search,
  Upload,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";

type StepStatus = "pending" | "active" | "done" | "failed";

interface StepDefinition {
  id: "upload" | "commit" | "process" | "search";
  label: string;
  icon: LucideIcon;
}

const STEPS: readonly StepDefinition[] = [
  { id: "upload", label: "업로드", icon: Upload },
  { id: "commit", label: "커밋", icon: GitCommitHorizontal },
  { id: "process", label: "처리", icon: Cog },
  { id: "search", label: "인덱스·검색", icon: Search },
];

const ACTIVE_STEP_BY_PHASE: Record<string, number> = {
  opening: 0,
  uploading: 0,
  committing: 1,
  processing: 2,
  indexing: 3,
  searching: 3,
};

function stepStatuses(pipeline: FoundryLiteMediaPipelineState): StepStatus[] {
  const { phase } = pipeline;
  if (phase === "idle") return ["pending", "pending", "pending", "pending"];
  if (phase === "succeeded") return ["done", "done", "done", "done"];
  if (phase === "failed") {
    // 완료 evidence 기준으로 실패 지점을 판정한다.
    // 처리 outcome이 있어도 FAILED면 '처리' 단계(index 2)가 실패 지점이다.
    const isProcessingDone =
      pipeline.processing !== null &&
      foundryLiteMediaProcessingFailure(pipeline.processing) === null;
    const doneCount = isProcessingDone ? 3 : pipeline.uploadResult ? 2 : 0;
    return STEPS.map((_, index) => {
      if (index < doneCount) return "done";
      if (index === doneCount) return "failed";
      return "pending";
    });
  }
  const activeIndex = ACTIVE_STEP_BY_PHASE[phase] ?? 0;
  return STEPS.map((_, index) => {
    if (index < activeIndex) return "done";
    if (index === activeIndex) return "active";
    return "pending";
  });
}

function stepEvidence(
  step: StepDefinition,
  pipeline: FoundryLiteMediaPipelineState,
): string | null {
  if (step.id === "upload" && pipeline.uploadResult) {
    return `item_version=${pipeline.uploadResult.mediaItemVersionId}`;
  }
  if (step.id === "commit" && pipeline.uploadResult) {
    return `committed=${pipeline.committedVersionIds.length}건`;
  }
  if (step.id === "process" && pipeline.processing) {
    const failure = foundryLiteMediaProcessingFailure(pipeline.processing);
    if (failure) {
      return `failure_kind=${failure.kind} · failure_reason=${failure.reason}`;
    }
    return `derivative=${pipeline.processing.media_derivative_id}`;
  }
  if (step.id === "search" && pipeline.indexing) {
    return `indexed=${pipeline.indexing.indexed} · hits=${pipeline.hits.length}`;
  }
  return null;
}

const STATUS_ICON: Record<StepStatus, LucideIcon> = {
  pending: Circle,
  active: Loader2,
  done: CheckCircle2,
  failed: XCircle,
};

const STATUS_CLASSES: Record<StepStatus, string> = {
  pending: "border text-muted-foreground",
  active: "border-primary/50 bg-primary/5 text-primary",
  done: "border-success/40 bg-success/5 text-success",
  failed: "border-destructive/50 bg-destructive/5 text-destructive",
};

/** upload → commit → process → index·search 상태를 구분해 보여주는 스테퍼 (acceptance 필수). */
export function MediaPipelineStepper({
  pipeline,
}: {
  pipeline: FoundryLiteMediaPipelineState;
}) {
  const statuses = stepStatuses(pipeline);

  return (
    <div className="grid grid-cols-4 gap-2">
      {STEPS.map((step, index) => {
        const status = statuses[index];
        const StatusIcon = STATUS_ICON[status];
        const evidence = stepEvidence(step, pipeline);
        return (
          <div
            key={step.id}
            className={cn("rounded border p-2", STATUS_CLASSES[status])}
          >
            <div className="flex items-center gap-1.5 text-xs font-medium">
              <StatusIcon
                className={cn(
                  "size-3.5",
                  status === "active" && "animate-spin",
                )}
              />
              <step.icon className="size-3.5" />
              {step.label}
            </div>
            <div
              className="mt-1 truncate font-mono text-[10px] opacity-80"
              title={evidence ?? undefined}
            >
              {evidence ?? "대기"}
            </div>
          </div>
        );
      })}
    </div>
  );
}
