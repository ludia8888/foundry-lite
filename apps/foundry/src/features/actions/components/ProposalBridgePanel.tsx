import type { InsightReviewPayload } from "@foundry-lite/sdk";
import { useFoundryLiteProvidedInsightReviewQueue } from "@foundry-lite/sdk/react";
import { GitPullRequestArrow } from "lucide-react";

import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

interface ProposalBridgePanelProps {
  onOpenApprovals: () => void;
}

function statusIntent(status: string): StatusIntent {
  if (status === "approved") return "success";
  if (status === "rejected") return "danger";
  if (status === "pending") return "warning";
  return "neutral";
}

function proposalTitle(review: InsightReviewPayload): string {
  const proposal = review.actionProposal as
    | (Record<string, unknown> & { title?: string; actionApiName?: string })
    | null;
  if (proposal?.title) return String(proposal.title);
  if (proposal?.actionApiName) return String(proposal.actionApiName);
  return review.claimText || review.id;
}

/**
 * 승인 필요 액션 브리지: 백엔드 insight review 큐에서 action proposal을 읽어,
 * 승인이 필요한 액션은 여기서 proposal/review로 이어짐을 보여준다.
 */
export function ProposalBridgePanel({
  onOpenApprovals,
}: ProposalBridgePanelProps) {
  const queue = useFoundryLiteProvidedInsightReviewQueue();
  const actionReviews = queue.reviews.filter(
    (review) => review.actionProposal?.actionApiName,
  );

  return (
    <div className="space-y-2 rounded border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <GitPullRequestArrow className="size-3.5 text-muted-foreground" />
          <span className="section-label">승인 필요 액션 · proposal</span>
        </div>
        <Button size="sm" variant="ghost" onClick={onOpenApprovals}>
          Approvals 열기
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        승인 정책이 걸린 액션은 직접 apply 대신 review 큐로 제출되어 승인 후
        실행됩니다.
      </p>
      {queue.isLoading ? (
        <div className="text-[11px] text-muted-foreground">불러오는 중...</div>
      ) : actionReviews.length === 0 ? (
        <div className="rounded border border-dashed px-2 py-3 text-center text-[11px] text-muted-foreground">
          대기 중인 액션 proposal이 없습니다.
        </div>
      ) : (
        <ul className="space-y-1">
          {actionReviews.map((review) => {
            const proposal = review.actionProposal;
            return (
              <li
                key={review.id}
                className="space-y-1 rounded border bg-muted/40 px-2 py-1.5"
              >
                <div className="flex items-center gap-1.5">
                  <StatusPill intent={statusIntent(review.status)}>
                    {review.status}
                  </StatusPill>
                  <span className="truncate text-[11px] font-medium">
                    {proposalTitle(review)}
                  </span>
                </div>
                <div className="font-mono text-[10px] text-muted-foreground">
                  {proposal?.actionApiName} · {proposal?.targetObjectType}/
                  {proposal?.targetObjectId} · v
                  {proposal?.expectedObjectVersion ?? "?"}
                </div>
                {review.approvedActionRunId ? (
                  <div className="font-mono text-[10px] text-muted-foreground">
                    approved_run_id={review.approvedActionRunId}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
