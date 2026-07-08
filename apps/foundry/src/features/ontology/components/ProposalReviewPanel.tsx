import type { OntologyProposalPayload } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyProposalReviewState } from "@foundry-lite/sdk/react";
import {
  AlertTriangle,
  CircleCheck,
  CircleX,
  GitMerge,
  ShieldAlert,
  Undo2,
} from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";

import { formatDateTime, truncateMiddle } from "../lib/ontology-view";

function readString(value: unknown, key: string): string | null {
  if (!value || typeof value !== "object") return null;
  const field = (value as Record<string, unknown>)[key];
  return typeof field === "string" ? field : null;
}

interface ProposalReviewPanelProps {
  proposal: OntologyProposalPayload;
  review: FoundryLiteOntologyProposalReviewState;
}

/** 리뷰어 작업: 승인/반려 결정 + 실행(머지) + 철회. 직무 분리 거부를 노출한다. */
export function ProposalReviewPanel({
  proposal,
  review,
}: ProposalReviewPanelProps) {
  const [comment, setComment] = useState("");
  const [withdrawReason, setWithdrawReason] = useState("");

  const canDecide =
    proposal.status === "submitted" || proposal.status === "in_review";
  const canExecute = proposal.status === "approved";
  const canWithdraw = canDecide;
  const decisionValue = readString(proposal.decision, "decision");
  const decisionComment = readString(proposal.decision, "comment");

  // 백엔드 허용 enum은 approve/reject다 (approved/rejected는 결정 후 status 값).
  const handleDecide = (decision: "approve" | "reject") => {
    void review.decide.execute({
      proposalId: proposal.id,
      decision,
      expectedFingerprint: proposal.fingerprint,
      comment: comment.trim().length > 0 ? comment.trim() : null,
    });
  };

  return (
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1 space-y-3">
        <div className="rounded border bg-card">
          <div className="flex items-center justify-between p-3">
            <span className="text-xs font-semibold">리뷰어 작업</span>
            <span className="font-mono text-[10px] text-muted-foreground">
              expected_fingerprint={truncateMiddle(proposal.fingerprint, 18)}
            </span>
          </div>
          <Separator />
          <div className="space-y-2 p-3">
            <div className="section-label">일반 정보</div>
            <div className="space-y-1 text-[11px]">
              <div>
                <span className="text-muted-foreground">제출자 </span>
                <span className="font-mono">{proposal.submittedByUserId}</span>
              </div>
              <div>
                <span className="text-muted-foreground">리뷰어 </span>
                <span className="font-mono">
                  {proposal.assigneeUserId ?? "미지정"}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">리비전 </span>
                <span className="font-mono">{proposal.revisionCount}</span>
              </div>
            </div>
            {proposal.hasBlockedChangesAtSubmit ? (
              <div className="flex items-start gap-1.5 rounded bg-warning/10 p-2 text-[11px] text-warning">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                제출 시점에 차단 변경이 포함되어 있었습니다. 승인 전에 영향
                범위를 반드시 확인하세요.
              </div>
            ) : null}
            {canDecide ? (
              <div className="space-y-2 border-t pt-2">
                <Textarea
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                  placeholder="결정 코멘트 (선택)"
                  className="min-h-16 text-xs"
                />
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    className="bg-success text-success-foreground hover:bg-success/90"
                    disabled={review.decide.isRunning}
                    onClick={() => handleDecide("approve")}
                  >
                    <CircleCheck />
                    승인
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="text-destructive hover:text-destructive"
                    disabled={review.decide.isRunning}
                    onClick={() => handleDecide("reject")}
                  >
                    <CircleX />
                    반려
                  </Button>
                </div>
              </div>
            ) : (
              <p className="border-t pt-2 text-[11px] text-muted-foreground">
                이 제안은 현재 결정을 받을 수 없는 상태입니다.
              </p>
            )}
            {review.separationOfDuties.isDenied ? (
              <div className="flex items-start gap-1.5 rounded border border-destructive/30 bg-destructive/5 p-2 text-[11px] text-destructive">
                <ShieldAlert className="mt-0.5 size-3.5 shrink-0" />
                <span>
                  직무 분리 정책: 제출자는 본인 제안을{" "}
                  {review.separationOfDuties.deniedOperation === "execute"
                    ? "실행"
                    : "결정"}
                  할 수 없습니다. 다른 리뷰어 계정으로 진행하세요.
                  {review.separationOfDuties.requestId ? (
                    <span className="ml-1 font-mono text-[10px]">
                      req={review.separationOfDuties.requestId}
                    </span>
                  ) : null}
                </span>
              </div>
            ) : review.decide.error ? (
              <ErrorState error={review.decide.error} />
            ) : null}
          </div>
        </div>

        <div className="rounded border bg-card p-3">
          <div className="section-label mb-2">실행 (머지)</div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={!canExecute || review.execute.isRunning}
              onClick={() =>
                void review.execute.execute({
                  proposalId: proposal.id,
                  expectedFingerprint: proposal.fingerprint,
                })
              }
            >
              <GitMerge />
              {review.execute.isRunning ? "실행 중…" : "제안 실행"}
            </Button>
            {proposal.executionStatus ? (
              <StatusPill
                intent={
                  proposal.executionStatus === "executed"
                    ? "success"
                    : proposal.executionStatus === "failed"
                      ? "danger"
                      : "info"
                }
              >
                실행 상태: {proposal.executionStatus}
              </StatusPill>
            ) : null}
          </div>
          {!canExecute && proposal.status !== "applied" ? (
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              승인된 제안만 실행할 수 있습니다.
            </p>
          ) : null}
          {proposal.appliedOntologyVersion ? (
            <p className="mt-1.5 font-mono text-[10px] text-success">
              적용 버전:{" "}
              {readString(proposal.appliedOntologyVersion, "versionNumber") ??
                JSON.stringify(proposal.appliedOntologyVersion).slice(0, 60)}
            </p>
          ) : null}
          {!review.separationOfDuties.isDenied && review.execute.error ? (
            <ErrorState error={review.execute.error} className="mt-2" />
          ) : null}
        </div>

        {canWithdraw ? (
          <div className="rounded border bg-card p-3">
            <div className="section-label mb-2">제안 철회</div>
            <div className="flex items-center gap-2">
              <Input
                value={withdrawReason}
                onChange={(event) => setWithdrawReason(event.target.value)}
                placeholder="철회 사유 (선택)"
                className="h-7 w-56 text-xs"
              />
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive hover:text-destructive"
                disabled={review.withdraw.isRunning}
                onClick={() =>
                  void review.withdraw.execute({
                    proposalId: proposal.id,
                    reason:
                      withdrawReason.trim().length > 0
                        ? withdrawReason.trim()
                        : null,
                  })
                }
              >
                <Undo2 />
                {review.withdraw.isRunning ? "철회 중…" : "철회"}
              </Button>
            </div>
            {review.withdraw.error ? (
              <ErrorState error={review.withdraw.error} className="mt-2" />
            ) : null}
          </div>
        ) : null}
      </div>

      <aside className="w-64 shrink-0 space-y-2">
        <div className="section-label">결정 기록</div>
        {proposal.decision ? (
          <div
            className={
              decisionValue === "rejected"
                ? "rounded border border-destructive/30 bg-destructive/5 p-2"
                : "rounded border border-success/40 bg-success/10 p-2"
            }
          >
            <div className="flex items-center gap-1.5 text-xs font-medium">
              {decisionValue === "rejected" ? (
                <CircleX className="size-3.5 text-destructive" />
              ) : (
                <CircleCheck className="size-3.5 text-success" />
              )}
              {decisionValue === "rejected" ? "반려됨" : "승인됨"}
            </div>
            {decisionComment ? (
              <p className="mt-1 text-[11px] text-muted-foreground">
                {decisionComment}
              </p>
            ) : null}
            <p className="mt-1 text-[10px] text-muted-foreground">
              {formatDateTime(proposal.updatedAt)}
            </p>
          </div>
        ) : (
          <p className="rounded border border-dashed p-3 text-[11px] text-muted-foreground">
            아직 결정 기록이 없습니다. 승인 또는 반려가 기록되면 여기에
            표시됩니다.
          </p>
        )}
      </aside>
    </div>
  );
}
