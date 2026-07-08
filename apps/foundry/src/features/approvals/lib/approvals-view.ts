import type {
  InsightReviewPayload,
  OntologyProposalPayload,
  PipelineProposal,
} from "@foundry-lite/sdk";

import type { StatusIntent } from "@/components/shared/StatusPill";

/** 통합 리뷰 큐의 3개 소스. */
export type ApprovalSource = "insights" | "ontology" | "pipelines";

export const APPROVAL_SOURCE_LABELS: Record<ApprovalSource, string> = {
  insights: "인사이트 리뷰",
  ontology: "온톨로지 제안",
  pipelines: "파이프라인 제안",
};

/** intent 필터 값 — 각 소스 상태를 3개 축으로 정규화. */
export type ApprovalIntent = "pending" | "decided" | "all";

export const APPROVAL_INTENT_LABELS: Record<ApprovalIntent, string> = {
  pending: "리뷰 대기",
  decided: "결정 완료",
  all: "전체",
};

/** 소스별 payload를 하나의 큐 행 모델로 정규화한다. */
export type ApprovalQueueItem = {
  source: ApprovalSource;
  id: string;
  title: string;
  resourcePath: string | null;
  statusLabel: string;
  statusIntent: StatusIntent;
  isPending: boolean;
  submittedBy: string;
  assignee: string | null;
  createdAt: string;
  priority: "low" | "normal" | "high" | null;
};

const PIPELINE_TERMINAL_STATUSES = new Set([
  "executed",
  "rejected",
  "withdrawn",
  "abandoned",
]);

function intentForStatus(status: string, isPending: boolean): StatusIntent {
  if (isPending) return "info";
  if (
    status === "rejected" ||
    status === "withdrawn" ||
    status === "abandoned"
  ) {
    return "danger";
  }
  if (status === "approved" || status === "executed" || status === "applied") {
    return "success";
  }
  return "neutral";
}

const STATUS_KO: Record<string, string> = {
  pending: "리뷰 대기",
  submitted: "제출됨",
  approved: "승인됨",
  rejected: "거절됨",
  executed: "실행 완료",
  applied: "적용됨",
  withdrawn: "철회됨",
  abandoned: "폐기됨",
  open: "열림",
  in_review: "리뷰 중",
  under_review: "리뷰 중",
};

export function statusLabel(status: string): string {
  return STATUS_KO[status] ?? status;
}

/** 인사이트 리뷰 payload → 큐 행. ontology_change 타입은 별도 탭에서 다룬다. */
export function insightReviewToQueueItem(
  review: InsightReviewPayload,
): ApprovalQueueItem {
  const isPending = review.status === "pending";
  return {
    source: "insights",
    id: review.id,
    title: review.claimText || review.claimId,
    resourcePath: review.actionProposal?.actionApiName
      ? `action:${review.actionProposal.actionApiName}${
          review.actionProposal.targetObjectId
            ? ` · ${review.actionProposal.targetObjectId}`
            : ""
        }`
      : null,
    statusLabel: statusLabel(review.status),
    statusIntent: intentForStatus(review.status, isPending),
    isPending,
    submittedBy: review.createdByUserId,
    assignee: review.assigneeUserId,
    createdAt: review.createdAt,
    priority: review.priority,
  };
}

/** insights.reviews.list는 ontology_change 항목도 포함한다 — 인사이트 탭에서 제외한다. */
export function isActionReview(review: InsightReviewPayload): boolean {
  return review.proposalType !== "ontology_change";
}

export function ontologyProposalToQueueItem(
  proposal: OntologyProposalPayload,
): ApprovalQueueItem {
  const isPending =
    proposal.status === "submitted" ||
    proposal.status === "pending" ||
    proposal.status === "in_review" ||
    proposal.status === "under_review";
  return {
    source: "ontology",
    id: proposal.id,
    title: proposal.title,
    resourcePath: `ontology:${proposal.proposalType}`,
    statusLabel: statusLabel(proposal.status),
    statusIntent: intentForStatus(proposal.status, isPending),
    isPending,
    submittedBy: proposal.submittedByUserId,
    assignee: proposal.assigneeUserId,
    createdAt: proposal.createdAt,
    priority: null,
  };
}

function pipelineField(proposal: PipelineProposal, key: string): string | null {
  const value = (proposal as Record<string, unknown>)[key];
  return typeof value === "string" ? value : null;
}

export function pipelineProposalToQueueItem(
  proposal: PipelineProposal,
): ApprovalQueueItem {
  const isPending = !PIPELINE_TERMINAL_STATUSES.has(proposal.status);
  return {
    source: "pipelines",
    id: proposal.id,
    title: pipelineField(proposal, "title") ?? proposal.id,
    resourcePath: pipelineField(proposal, "pipelineId")
      ? `pipeline:${pipelineField(proposal, "pipelineId")}`
      : null,
    statusLabel: statusLabel(proposal.status),
    statusIntent: intentForStatus(proposal.status, isPending),
    isPending,
    submittedBy: pipelineField(proposal, "createdBy") ?? "-",
    assignee: pipelineField(proposal, "assignedTo"),
    createdAt: pipelineField(proposal, "createdAt") ?? "",
    priority: null,
  };
}

export function filterByIntent(
  items: ApprovalQueueItem[],
  intent: ApprovalIntent,
): ApprovalQueueItem[] {
  if (intent === "all") return items;
  if (intent === "pending") return items.filter((item) => item.isPending);
  return items.filter((item) => !item.isPending);
}

export function pendingCount(items: ApprovalQueueItem[]): number {
  return items.filter((item) => item.isPending).length;
}

/** ISO 문자열을 컴팩트한 한국어 날짜로. */
export function formatDate(iso: string): string {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
