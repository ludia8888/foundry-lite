import type { PipelineProposal } from "@foundry-lite/sdk";
import {
  useFoundryLiteProvidedInsightReviewQueue,
  useFoundryLiteProvidedOntologyProposalQueue,
  useFoundryLiteProvidedPipelineBuilder,
} from "@foundry-lite/sdk/react";
import { ClipboardCheck, MousePointerClick } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { EmptyState } from "@/components/shared/EmptyState";
import { PageHeader } from "@/components/shared/PageHeader";
import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { StatusPill } from "@/components/shared/StatusPill";

import { CreateReviewDialog } from "./components/CreateReviewDialog";
import { InsightReviewDetail } from "./components/InsightReviewDetail";
import { OntologyProposalDetail } from "./components/OntologyProposalDetail";
import { PipelineProposalDetail } from "./components/PipelineProposalDetail";
import { QueueList } from "./components/QueueList";
import { SourceTabs } from "./components/SourceTabs";
import type {
  ApprovalIntent,
  ApprovalQueueItem,
  ApprovalSource,
} from "./lib/approvals-view";
import {
  filterByIntent,
  insightReviewToQueueItem,
  isActionReview,
  ontologyProposalToQueueItem,
  pendingCount,
  pipelineProposalToQueueItem,
} from "./lib/approvals-view";

const CURRENT_USER_ID = "web-demo-operator";

/**
 * Approvals — 3개 소스(인사이트 리뷰 / 온톨로지 제안 / 파이프라인 제안)를 하나의
 * 리뷰 큐로 통합한다. 큐에서 assign/decide/execute, 상세에서 diff + evidence.
 */
export default function ApprovalsPage() {
  const [searchParams] = useSearchParams();
  const requestedSource = approvalSourceFromQuery(searchParams.get("source"));
  const [source, setSource] = useState<ApprovalSource>(requestedSource);
  const [intent, setIntent] = useState<ApprovalIntent>(
    searchParams.get("proposalId") ? "all" : "pending",
  );
  const [selectedId, setSelectedId] = useState<string | null>(
    searchParams.get("proposalId"),
  );

  const insights = useFoundryLiteProvidedInsightReviewQueue({
    limit: 100,
    currentUserId: CURRENT_USER_ID,
  });
  const ontology = useFoundryLiteProvidedOntologyProposalQueue({
    pageSize: 100,
  });
  const pipelines = useFoundryLiteProvidedPipelineBuilder({ limit: 100 });

  const insightItems = useMemo(
    () => insights.reviews.filter(isActionReview).map(insightReviewToQueueItem),
    [insights.reviews],
  );
  const ontologyItems = useMemo(
    () => ontology.proposals.map(ontologyProposalToQueueItem),
    [ontology.proposals],
  );
  const pipelineProposals: PipelineProposal[] = pipelines.data?.proposals ?? [];
  const pipelineItems = useMemo(
    () => pipelineProposals.map(pipelineProposalToQueueItem),
    [pipelineProposals],
  );

  const itemsBySource: Record<ApprovalSource, ApprovalQueueItem[]> = {
    insights: insightItems,
    ontology: ontologyItems,
    pipelines: pipelineItems,
  };
  const pendingCounts: Record<ApprovalSource, number> = {
    insights: pendingCount(insightItems),
    ontology: pendingCount(ontologyItems),
    pipelines: pendingCount(pipelineItems),
  };

  const sourceItems = itemsBySource[source];
  const visibleItems = useMemo(
    () => filterByIntent(sourceItems, intent),
    [sourceItems, intent],
  );
  const intentCounts: Record<ApprovalIntent, number> = {
    pending: pendingCount(sourceItems),
    decided: sourceItems.length - pendingCount(sourceItems),
    all: sourceItems.length,
  };

  const loadingBySource: Record<ApprovalSource, boolean> = {
    insights: insights.isLoading,
    ontology: ontology.isLoading,
    pipelines: pipelines.isLoading,
  };
  const errorBySource: Record<ApprovalSource, unknown> = {
    insights: insights.error,
    ontology: ontology.error,
    pipelines: pipelines.error,
  };
  const reloadBySource: Record<ApprovalSource, () => void> = {
    insights: () => void insights.reload(),
    ontology: () => void ontology.reload(),
    pipelines: () => void pipelines.reload(),
  };

  const selectedItem =
    visibleItems.find((item) => item.id === selectedId) ?? null;
  const selectedReview =
    source === "insights" && selectedItem
      ? (insights.reviews.find((review) => review.id === selectedItem.id) ??
        null)
      : null;
  const selectedPipeline =
    source === "pipelines" && selectedItem
      ? (pipelineProposals.find(
          (proposal) => proposal.id === selectedItem.id,
        ) ?? null)
      : null;

  const handleChangeSource = (next: ApprovalSource) => {
    setSource(next);
    setSelectedId(null);
  };

  const handleReviewCreated = (reviewId: string) => {
    setSource("insights");
    setIntent("pending");
    setSelectedId(reviewId);
    void insights.reload();
  };

  const handleInsightChanged = () => void insights.reload();
  const handleOntologyChanged = () => void ontology.reload();
  // 결정/실행 후 항목이 pending 필터에서 사라지지 않도록 전체 보기로 전환한다.
  const handleKeepVisibleAfterDecision = () => setIntent("all");

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <PageHeader
        title="Approvals"
        description="인사이트 리뷰 · 온톨로지/파이프라인 제안을 하나의 큐에서 검토하고 승인/거절/실행합니다."
        meta={<StatusPill intent="info">통합 리뷰 큐</StatusPill>}
        actions={
          <div className="flex items-center gap-3">
            <RequestTelemetry />
            <CreateReviewDialog onCreated={handleReviewCreated} />
          </div>
        }
      />

      <SourceTabs
        source={source}
        pendingCounts={pendingCounts}
        onChange={handleChangeSource}
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[minmax(360px,420px)_1fr]">
        <QueueList
          items={visibleItems}
          intent={intent}
          intentCounts={intentCounts}
          selectedId={selectedId}
          isLoading={loadingBySource[source]}
          error={errorBySource[source]}
          onChangeIntent={setIntent}
          onSelect={setSelectedId}
          onRetry={reloadBySource[source]}
        />

        <div className="min-h-0 overflow-auto rounded border bg-card p-3">
          {!selectedItem ? (
            <EmptyState
              icon={MousePointerClick}
              title="항목을 선택하세요"
              description="왼쪽 큐에서 제안을 선택하면 diff와 증거, 결정 액션이 표시됩니다."
            />
          ) : source === "insights" && selectedReview ? (
            <InsightReviewDetail
              review={selectedReview}
              currentUserId={CURRENT_USER_ID}
              onChanged={handleInsightChanged}
              onDecided={handleKeepVisibleAfterDecision}
            />
          ) : source === "ontology" ? (
            <OntologyProposalDetail
              proposalId={selectedItem.id}
              currentUserId={CURRENT_USER_ID}
              onChanged={handleOntologyChanged}
              onDecided={handleKeepVisibleAfterDecision}
            />
          ) : source === "pipelines" && selectedPipeline ? (
            <PipelineProposalDetail proposal={selectedPipeline} />
          ) : (
            <EmptyState
              icon={ClipboardCheck}
              title="상세를 불러올 수 없습니다"
              description="선택한 항목의 원본 데이터를 찾지 못했습니다. 큐를 새로고침하세요."
            />
          )}
        </div>
      </div>
    </div>
  );
}

function approvalSourceFromQuery(value: string | null): ApprovalSource {
  if (value === "ontology" || value === "pipelines") return value;
  return "insights";
}
