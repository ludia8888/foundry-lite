import type { InsightReviewPayload } from "@foundry-lite/sdk";
import { FileText, Link2 } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";

import { EvidenceRow } from "./EvidenceRow";

interface ReviewEvidencePanelProps {
  review: InsightReviewPayload;
}

/** 인사이트 리뷰의 claim + evidence 객체 + action proposal 파라미터를 함께 보여준다. */
export function ReviewEvidencePanel({ review }: ReviewEvidencePanelProps) {
  const proposal = review.actionProposal;
  return (
    <div className="space-y-3">
      <div className="rounded border p-2.5">
        <div className="mb-1.5 flex items-center gap-1.5">
          <FileText className="size-3.5 text-muted-foreground" />
          <span className="section-label">클레임</span>
          <StatusPill intent="neutral" className="ml-auto">
            {review.claimId}
          </StatusPill>
        </div>
        <p className="text-[12px] leading-relaxed text-foreground/90">
          {review.claimText}
        </p>
      </div>

      <div>
        <div className="mb-1 flex items-center gap-1.5">
          <Link2 className="size-3.5 text-muted-foreground" />
          <span className="section-label">
            증거 객체 ({review.evidenceObjectIds.length})
          </span>
        </div>
        {review.evidenceObjectIds.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {review.evidenceObjectIds.map((objectId) => (
              <span
                key={objectId}
                className="rounded border bg-muted/40 px-1.5 py-0.5 font-mono text-[11px]"
              >
                {objectId}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            연결된 증거 객체가 없습니다.
          </p>
        )}
      </div>

      {proposal?.actionApiName ? (
        <div>
          <div className="mb-1 section-label">제안 액션</div>
          <div className="rounded border px-2.5">
            <EvidenceRow label="action">{proposal.actionApiName}</EvidenceRow>
            <EvidenceRow label="target">
              {proposal.targetObjectType ?? "-"} ·{" "}
              {proposal.targetObjectId ?? "-"}
            </EvidenceRow>
            <EvidenceRow label="expected version">
              {proposal.expectedObjectVersion ?? "-"}
            </EvidenceRow>
            {proposal.params && Object.keys(proposal.params).length > 0 ? (
              <EvidenceRow label="params">
                {JSON.stringify(proposal.params)}
              </EvidenceRow>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
