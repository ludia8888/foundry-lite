import type {
  ApprovalExecutionResult,
  FoundryLiteApiError,
  InsightReviewPayload,
} from "@foundry-lite/sdk";
import { createRequestId } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteInsightReviewAssignment,
  useFoundryLiteInsightReviewDecision,
  useFoundryLiteSession,
} from "@foundry-lite/sdk/react";
import { Check, Play, UserPlus, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { statusLabel } from "../lib/approvals-view";
import { EvidenceRow } from "./EvidenceRow";
import { MutationEvidence } from "./MutationEvidence";
import type { MutationEvidenceData } from "./MutationEvidence";
import { PermissionDeniedCard } from "./PermissionDeniedCard";
import { ReviewEvidencePanel } from "./ReviewEvidencePanel";

interface InsightReviewDetailProps {
  review: InsightReviewPayload;
  currentUserId: string;
  onChanged: () => void;
  onDecided: () => void;
}

function permissionDenied(error: FoundryLiteApiError | null): boolean {
  return error?.code === "PERMISSION_DENIED";
}

/** 인사이트 액션 리뷰 상세: assign → decide → executeApprovedAction 플로우. */
export function InsightReviewDetail({
  review,
  currentUserId,
  onChanged,
  onDecided,
}: InsightReviewDetailProps) {
  const client = useFoundryLiteClient();
  const session = useFoundryLiteSession();
  const [comment, setComment] = useState("");
  const [evidence, setEvidence] = useState<MutationEvidenceData | null>(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [executeError, setExecuteError] = useState<FoundryLiteApiError | null>(
    null,
  );

  const assignment = useFoundryLiteInsightReviewAssignment(client, {
    onSuccess: () => {
      toast.success("리뷰를 나에게 배정했습니다");
      onChanged();
    },
    onError: (error: FoundryLiteApiError) =>
      toast.error(`배정 실패: ${error.code}`),
  });
  const decision = useFoundryLiteInsightReviewDecision(client, {
    onSuccess: (result: InsightReviewPayload) => {
      toast.success(
        result.status === "approved"
          ? "리뷰를 승인했습니다"
          : "리뷰를 거절했습니다",
      );
      onDecided();
      onChanged();
    },
    onError: (error: FoundryLiteApiError) =>
      toast.error(`결정 실패: ${error.code}`),
  });

  const isPending = review.status === "pending";
  const isAssignedToMe = review.assigneeUserId === currentUserId;
  const hasProposalFingerprint = Boolean(review.proposalFingerprint);
  const isExecuted = review.executionStatus === "executed";
  const canExecute =
    review.status === "approved" &&
    hasProposalFingerprint &&
    !isExecuted &&
    (review.executionStatus === null ||
      review.executionStatus === "pending_review");

  const handleAssign = () => {
    const idempotencyKey = createRequestId("assign");
    void assignment
      .execute({
        reviewId: review.id,
        assigneeUserId: currentUserId,
        idempotencyKey,
      })
      .then((result) => {
        if (result) {
          setEvidence({
            label: "리뷰 배정 완료",
            idempotencyKey,
            requestId: assignment.requestId,
            extra: [{ key: "assignee", value: result.assigneeUserId ?? "-" }],
          });
        }
      });
  };

  const handleDecide = (decisionValue: "approved" | "rejected") => {
    const idempotencyKey = createRequestId("decide");
    void decision
      .execute({
        reviewId: review.id,
        decision: decisionValue,
        comment: comment.trim() || null,
        idempotencyKey,
      })
      .then((result) => {
        if (result) {
          setComment("");
          setEvidence({
            label: `리뷰 ${statusLabel(result.status)}`,
            idempotencyKey,
            requestId: decision.requestId,
            extra: [{ key: "status", value: result.status }],
          });
        }
      });
  };

  const handleExecute = () => {
    if (!review.proposalFingerprint) return;
    const idempotencyKey = createRequestId("execute");
    setIsExecuting(true);
    setExecuteError(null);
    client.insights.reviews
      .executeApprovedAction(
        review.id,
        { expectedProposalFingerprint: review.proposalFingerprint },
        { idempotencyKey },
      )
      .then((result: ApprovalExecutionResult) => {
        toast.success("승인된 액션을 실행했습니다");
        setEvidence({
          label: "승인 액션 실행 완료",
          idempotencyKey,
          requestId: session.lastRequestId,
          extra: [
            { key: "action_run_id", value: result.action_run_id },
            { key: "proposal_fingerprint", value: result.proposal_fingerprint },
          ],
        });
        onChanged();
      })
      .catch((error: FoundryLiteApiError) => {
        setExecuteError(error);
        toast.error(`실행 실패: ${error.code}`);
      })
      .finally(() => setIsExecuting(false));
  };

  return (
    <div className="space-y-3">
      <ReviewEvidencePanel review={review} />

      {isPending ? (
        <div className="space-y-2 rounded border p-2.5">
          <div className="section-label">결정 코멘트 (선택)</div>
          <Textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="승인/거절 사유를 남기세요"
            className="min-h-[60px] text-[12px]"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={assignment.isRunning || isAssignedToMe}
              onClick={handleAssign}
            >
              <UserPlus />
              {isAssignedToMe ? "나에게 배정됨" : "나에게 배정"}
            </Button>
            <div className="ml-auto flex items-center gap-2">
              <Button
                size="sm"
                variant="destructive"
                disabled={decision.isRunning}
                onClick={() => handleDecide("rejected")}
              >
                <X />
                거절
              </Button>
              <Button
                size="sm"
                disabled={decision.isRunning}
                onClick={() => handleDecide("approved")}
                className="bg-success text-success-foreground hover:bg-success/90"
              >
                <Check />
                승인
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {review.status === "approved" ? (
        <div className="space-y-2 rounded border p-2.5">
          <div className="flex items-center justify-between">
            <span className="section-label">승인 액션 실행</span>
            {review.executionStatus ? (
              <StatusPill intent="neutral">{review.executionStatus}</StatusPill>
            ) : null}
          </div>
          {isExecuted ? (
            <div className="rounded border border-success/30 bg-success/5 px-2.5 py-2 text-[11px] text-success">
              실행 완료
              {review.approvedActionRunId ? (
                <span className="ml-1 font-mono">
                  action_run_id={review.approvedActionRunId}
                </span>
              ) : null}
            </div>
          ) : canExecute ? (
            <Button size="sm" disabled={isExecuting} onClick={handleExecute}>
              <Play />
              executeApprovedAction 실행
            </Button>
          ) : !hasProposalFingerprint ? (
            <p className="rounded border border-dashed px-2.5 py-2 text-[11px] text-muted-foreground">
              이 리뷰는 AIP proposal fingerprint가 없어 실행 대상 액션을 확정할
              수 없습니다. executeApprovedAction은 AIP가 발행한(fingerprint
              포함) 액션 제안에만 적용됩니다{" "}
              <StatusPill intent="warning">future</StatusPill>
            </p>
          ) : (
            <p className="rounded border border-dashed px-2.5 py-2 text-[11px] text-muted-foreground">
              현재 실행 상태가 {review.executionStatus ?? "알 수 없음"}라서
              브라우저에서 다시 실행할 수 없습니다.
            </p>
          )}
          {executeError ? <ErrorState error={executeError} /> : null}
        </div>
      ) : null}

      {evidence ? <MutationEvidence evidence={evidence} /> : null}

      {permissionDenied(decision.error) ? (
        <PermissionDeniedCard
          operation="결정"
          requestId={decision.requestId}
          message={decision.error?.message}
        />
      ) : null}

      <div className="rounded border px-2.5">
        <EvidenceRow label="review_id">{review.id}</EvidenceRow>
        <EvidenceRow label="proposal_fingerprint">
          {review.proposalFingerprint ?? "없음"}
        </EvidenceRow>
      </div>
    </div>
  );
}
