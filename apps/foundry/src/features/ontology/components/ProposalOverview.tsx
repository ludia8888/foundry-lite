import type { OntologyProposalPayload } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyProposalReviewState } from "@foundry-lite/sdk/react";
import { Share2, UserPlus } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";

import {
  buildProposalSteps,
  formatDateTime,
  proposalStatusIntent,
  proposalStatusLabel,
  truncateMiddle,
} from "../lib/ontology-view";
import { ProposalStepper } from "./ProposalStepper";

function readCount(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  return typeof value === "number" ? value : 0;
}

interface ProposalOverviewProps {
  proposal: OntologyProposalPayload;
  review: FoundryLiteOntologyProposalReviewState;
  onGoToReview: () => void;
}

/** 제안 개요: 타이틀 카드 + 번호 스테퍼 + 작업/리뷰어 카드 + 변경 요약. */
export function ProposalOverview({
  proposal,
  review,
  onGoToReview,
}: ProposalOverviewProps) {
  const steps = buildProposalSteps(proposal);
  const isTerminalNegative =
    proposal.status === "rejected" || proposal.status === "withdrawn";
  const canRequestReview = proposal.status === "submitted";

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 rounded border bg-card p-4">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[15px] font-semibold">
              {proposal.title}
            </h2>
            <StatusPill intent={proposalStatusIntent(proposal.status)}>
              {proposalStatusLabel(proposal.status)}
            </StatusPill>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {proposal.description ?? "아직 설명이 없습니다"}
          </p>
          <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="flex size-5 items-center justify-center rounded-full bg-muted font-mono text-[10px]">
              {proposal.submittedByUserId.slice(0, 2)}
            </span>
            작성자 {proposal.submittedByUserId} ·{" "}
            {formatDateTime(proposal.createdAt)}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <Button
              size="sm"
              disabled={!canRequestReview && proposal.status !== "in_review"}
              onClick={onGoToReview}
            >
              {canRequestReview ? "리뷰 요청" : "리뷰로 이동"}
            </Button>
            <Button size="sm" variant="outline">
              <Share2 />
              공유
            </Button>
          </div>
        </div>
        <div className="w-60 shrink-0 space-y-2">
          <ProposalStepper steps={steps} />
          {isTerminalNegative ? (
            <div className="rounded border bg-card p-2 text-center">
              <StatusPill intent={proposalStatusIntent(proposal.status)}>
                {proposalStatusLabel(proposal.status)}로 종료됨
              </StatusPill>
            </div>
          ) : null}
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded border bg-card">
          <div className="p-3">
            <div className="text-xs font-semibold">
              작업 {proposal.decision ? 1 : 0} / 1
            </div>
            <div className="text-[11px] text-muted-foreground">
              결정이 필요한 리뷰 작업
            </div>
          </div>
          <Separator />
          <div className="p-3">
            {proposal.decision ? (
              <div className="text-xs">
                <StatusPill
                  intent={proposal.status === "rejected" ? "danger" : "success"}
                >
                  {proposal.status === "rejected" ? "반려" : "승인"}
                </StatusPill>
                <span className="ml-2 text-muted-foreground">
                  결정 기록이 리뷰 탭에 표시됩니다.
                </span>
              </div>
            ) : (
              <EmptyState
                title="아직 리뷰 결정이 없습니다"
                description="반려된 편집이 있으면 여기에 표시됩니다."
                className="border-0 p-4"
              />
            )}
          </div>
        </div>

        <div className="rounded border bg-card">
          <div className="p-3">
            <div className="text-xs font-semibold">
              리뷰어 ({proposal.assigneeUserId ? 1 : 0})
            </div>
            <div className="text-[11px] text-muted-foreground">
              이 제안을 검토하는 사용자
            </div>
          </div>
          <Separator />
          <div className="p-3">
            {proposal.assigneeUserId ? (
              <div className="flex items-center gap-2 text-xs">
                <span className="flex size-5 items-center justify-center rounded-full bg-primary/10 font-mono text-[10px] text-primary">
                  {proposal.assigneeUserId.slice(0, 2)}
                </span>
                {proposal.assigneeUserId}
              </div>
            ) : (
              <AssignReviewerForm proposal={proposal} review={review} />
            )}
          </div>
        </div>
      </div>

      <div className="rounded border bg-card">
        <div className="p-3 text-xs font-semibold">이 제안의 변경 요약</div>
        <Separator />
        <div className="grid gap-x-8 gap-y-2 p-3 sm:grid-cols-2">
          <div>
            <div className="section-label">검증 카운트</div>
            <div className="mt-0.5 font-mono text-[11px]">
              객체 {readCount(proposal.validation, "objectTypeCount")} · 링크{" "}
              {readCount(proposal.validation, "linkTypeCount")} · 액션{" "}
              {readCount(proposal.validation, "actionTypeCount")}
            </div>
          </div>
          <div>
            <div className="section-label">제출 시 차단 변경</div>
            <div className="mt-0.5">
              {proposal.hasBlockedChangesAtSubmit ? (
                <StatusPill intent="danger">차단 변경 포함</StatusPill>
              ) : (
                <StatusPill intent="success">없음</StatusPill>
              )}
            </div>
          </div>
          <div>
            <div className="section-label">리비전</div>
            <div className="mt-0.5 font-mono text-[11px]">
              {proposal.revisionCount}회
              {proposal.lastRevisedAt
                ? ` · 마지막 ${formatDateTime(proposal.lastRevisedAt)}`
                : null}
            </div>
          </div>
          <div>
            <div className="section-label">핑거프린트</div>
            <div className="mt-0.5 font-mono text-[11px]">
              {truncateMiddle(proposal.fingerprint, 26)}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AssignReviewerForm({
  proposal,
  review,
}: {
  proposal: OntologyProposalPayload;
  review: FoundryLiteOntologyProposalReviewState;
}) {
  const [reviewerUserId, setReviewerUserId] = useState("");
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted-foreground">
        제안을 검토할 리뷰어를 지정하세요. 제출자 본인은 결정할 수 없습니다
        (직무 분리).
      </p>
      <div className="flex items-center gap-2">
        <Input
          value={reviewerUserId}
          onChange={(event) => setReviewerUserId(event.target.value)}
          placeholder="리뷰어 사용자 ID"
          className="h-7 w-48 text-xs"
        />
        <Button
          size="sm"
          variant="outline"
          disabled={
            reviewerUserId.trim().length === 0 || review.assign.isRunning
          }
          onClick={() =>
            void review.assign.execute({
              proposalId: proposal.id,
              reviewerUserId: reviewerUserId.trim(),
            })
          }
        >
          <UserPlus />
          {review.assign.isRunning ? "지정 중…" : "리뷰어 지정"}
        </Button>
      </div>
      {review.assign.error ? <ErrorState error={review.assign.error} /> : null}
    </div>
  );
}
