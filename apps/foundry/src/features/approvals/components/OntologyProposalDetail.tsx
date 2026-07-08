import {
  useFoundryLiteProvidedOntologyProposal,
  useFoundryLiteProvidedOntologyProposalReview,
} from "@foundry-lite/sdk/react";
import { Check, Play, UserPlus, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { statusLabel } from "../lib/approvals-view";
import { ontologyDiffModel, stripBranchDiffMarker } from "../lib/diff-model";
import { OntologyDiffViewer } from "./DiffViewer";
import { EvidenceRow } from "./EvidenceRow";
import { MutationEvidence } from "./MutationEvidence";
import type { MutationEvidenceData } from "./MutationEvidence";
import { PermissionDeniedCard } from "./PermissionDeniedCard";

interface OntologyProposalDetailProps {
  proposalId: string;
  currentUserId: string;
  onChanged: () => void;
  onDecided: () => void;
}

const PENDING_STATUSES = new Set([
  "submitted",
  "pending",
  "in_review",
  "under_review",
]);

/** 온톨로지 제안 상세: diff + 검증 + assign/decide/execute (직무 분리 안내 포함). */
export function OntologyProposalDetail({
  proposalId,
  currentUserId,
  onChanged,
  onDecided,
}: OntologyProposalDetailProps) {
  const detail = useFoundryLiteProvidedOntologyProposal(proposalId);
  const [comment, setComment] = useState("");
  const [evidence, setEvidence] = useState<MutationEvidenceData | null>(null);

  const review = useFoundryLiteProvidedOntologyProposalReview({
    onSuccess: (proposal, operation) => {
      const labels: Record<string, string> = {
        assign: "리뷰어 배정 완료",
        decide: `제안 ${statusLabel(proposal.status)}`,
        execute: "제안 실행 완료",
        withdraw: "제안 철회 완료",
      };
      toast.success(labels[operation] ?? "완료");
      if (operation === "decide") setComment("");
      if (operation === "decide" || operation === "execute") onDecided();
      void detail.refetch();
      onChanged();
    },
    onError: (error, operation) => {
      if (error.code !== "PERMISSION_DENIED") {
        toast.error(`${operation} 실패: ${error.code}`);
      }
    },
  });

  const proposal = detail.proposal;
  const diff = useMemo(
    () => (proposal ? ontologyDiffModel(proposal) : null),
    [proposal],
  );

  if (detail.isLoading) return <LoadingState rowCount={6} />;
  if (detail.error)
    return (
      <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
    );
  if (!proposal || !diff) return null;

  const isPending = PENDING_STATUSES.has(proposal.status);
  const isAssignedToMe = proposal.assigneeUserId === currentUserId;
  const canExecute = proposal.status === "approved";
  const fingerprint = detail.fingerprint ?? proposal.fingerprint;

  const captureEvidence = (
    label: string,
    extra: MutationEvidenceData["extra"],
  ) => {
    setEvidence({
      label,
      idempotencyKey: `ontology:proposal:${proposal.id}`,
      requestId: review.requestId,
      extra,
    });
  };

  const handleAssign = () =>
    void review.assign
      .execute({ proposalId: proposal.id, reviewerUserId: currentUserId })
      .then((result) => {
        if (result)
          captureEvidence("리뷰어 배정 완료", [
            { key: "reviewer", value: result.assigneeUserId ?? "-" },
          ]);
      });

  const handleDecide = (decision: "approve" | "reject") =>
    void review.decide
      .execute({
        proposalId: proposal.id,
        decision,
        expectedFingerprint: fingerprint,
        comment: comment.trim() || null,
      })
      .then((result) => {
        if (result)
          captureEvidence(`제안 ${statusLabel(result.status)}`, [
            { key: "status", value: result.status },
            { key: "expected_fingerprint", value: fingerprint },
          ]);
      });

  const handleExecute = () =>
    void review.execute
      .execute({ proposalId: proposal.id, expectedFingerprint: fingerprint })
      .then((result) => {
        if (result)
          captureEvidence("제안 실행 완료", [
            { key: "status", value: result.status },
            {
              key: "applied_version",
              value: JSON.stringify(result.appliedOntologyVersion ?? {}).slice(
                0,
                48,
              ),
            },
          ]);
      });

  return (
    <div className="space-y-3">
      <div className="rounded border p-2.5">
        <div className="mb-1 flex items-center gap-2">
          <span className="section-label">제안</span>
          <StatusPill intent="neutral" className="ml-auto">
            {proposal.proposalType}
          </StatusPill>
        </div>
        <div className="text-[13px] font-medium">{proposal.title}</div>
        {stripBranchDiffMarker(proposal.description) ? (
          <p className="mt-1 whitespace-pre-line text-[12px] text-muted-foreground">
            {stripBranchDiffMarker(proposal.description)}
          </p>
        ) : null}
      </div>

      <OntologyDiffViewer diff={diff} />

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
              disabled={review.assign.isRunning || isAssignedToMe}
              onClick={handleAssign}
            >
              <UserPlus />
              {isAssignedToMe ? "나에게 배정됨" : "나에게 배정"}
            </Button>
            <div className="ml-auto flex items-center gap-2">
              <Button
                size="sm"
                variant="destructive"
                disabled={review.decide.isRunning}
                onClick={() => handleDecide("reject")}
              >
                <X />
                거절
              </Button>
              <Button
                size="sm"
                disabled={review.decide.isRunning}
                onClick={() => handleDecide("approve")}
                className="bg-success text-success-foreground hover:bg-success/90"
              >
                <Check />
                승인
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {canExecute ? (
        <div className="space-y-2 rounded border p-2.5">
          <div className="flex items-center justify-between">
            <span className="section-label">제안 실행</span>
            {proposal.executionStatus ? (
              <StatusPill intent="neutral">
                {proposal.executionStatus}
              </StatusPill>
            ) : null}
          </div>
          <Button
            size="sm"
            disabled={review.execute.isRunning}
            onClick={handleExecute}
          >
            <Play />
            제안 실행 (execute)
          </Button>
        </div>
      ) : null}

      {evidence ? <MutationEvidence evidence={evidence} /> : null}

      {review.separationOfDuties.isDenied ? (
        <PermissionDeniedCard
          operation={review.separationOfDuties.deniedOperation ?? "결정"}
          requestId={review.separationOfDuties.requestId}
          message={review.separationOfDuties.error?.message}
        />
      ) : review.error && review.error.code !== "PERMISSION_DENIED" ? (
        <ErrorState error={review.error} />
      ) : null}

      <div className="rounded border px-2.5">
        <EvidenceRow label="proposal_id">{proposal.id}</EvidenceRow>
        <EvidenceRow label="fingerprint">{fingerprint}</EvidenceRow>
        <EvidenceRow label="submitted_by">
          {proposal.submittedByUserId}
        </EvidenceRow>
        <EvidenceRow label="revisions">{proposal.revisionCount}</EvidenceRow>
      </div>
    </div>
  );
}
