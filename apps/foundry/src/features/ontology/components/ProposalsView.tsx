import {
  useFoundryLiteProvidedOntologyProposal,
  useFoundryLiteProvidedOntologyProposalQueue,
  useFoundryLiteProvidedOntologyProposalReview,
} from "@foundry-lite/sdk/react";
import { GitPullRequestArrow, History, Monitor, Pencil } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

import {
  PROPOSAL_STATUS_LABELS,
  formatDateTime,
  proposalStatusIntent,
  proposalStatusLabel,
} from "../lib/ontology-view";
import { ProposalOverview } from "./ProposalOverview";
import { ProposalReviewPanel } from "./ProposalReviewPanel";

const ALL_STATUS_VALUE = "__all__";

type ProposalSubview = "overview" | "review" | "changelog";

interface ProposalsViewProps {
  selectedProposalId: string | null;
  onSelectProposal: (proposalId: string | null) => void;
}

/** 제안 탭: 좌측 제안 목록/서브 네비 + 개요·리뷰·변경 이력. */
export function ProposalsView({
  selectedProposalId,
  onSelectProposal,
}: ProposalsViewProps) {
  const [statusFilter, setStatusFilter] = useState<string>(ALL_STATUS_VALUE);
  const [subview, setSubview] = useState<ProposalSubview>("overview");

  const queue = useFoundryLiteProvidedOntologyProposalQueue({
    status: statusFilter === ALL_STATUS_VALUE ? undefined : statusFilter,
    pageSize: 30,
  });
  const detail = useFoundryLiteProvidedOntologyProposal(selectedProposalId);
  const review = useFoundryLiteProvidedOntologyProposalReview({
    onSuccess: () => {
      void detail.refetch();
      queue.reload();
    },
  });

  const subviews: Array<{
    id: ProposalSubview;
    label: string;
    icon: typeof Monitor;
  }> = [
    { id: "overview", label: "개요", icon: Monitor },
    { id: "review", label: "리뷰", icon: Pencil },
    { id: "changelog", label: "변경 이력", icon: History },
  ];

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="flex w-72 shrink-0 flex-col border-r bg-card">
        <div className="border-b p-2">
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger size="sm" className="w-full text-xs">
              <GitPullRequestArrow className="size-3.5 shrink-0 text-muted-foreground" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_STATUS_VALUE} className="text-xs">
                전체 제안
              </SelectItem>
              {Object.entries(PROPOSAL_STATUS_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value} className="text-xs">
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-1.5">
          {queue.isLoading ? (
            <LoadingState rowCount={5} className="p-1.5" />
          ) : queue.error ? (
            <ErrorState error={queue.error} onRetry={queue.reload} />
          ) : queue.proposals.length === 0 ? (
            <EmptyState
              icon={GitPullRequestArrow}
              title="제안이 없습니다"
              description="온톨로지 탭에서 브랜치 변경을 제안으로 전환하세요."
              className="border-0"
            />
          ) : (
            queue.proposals.map((proposal) => (
              <button
                key={proposal.id}
                type="button"
                onClick={() => onSelectProposal(proposal.id)}
                className={cn(
                  "w-full rounded p-2 text-left hover:bg-muted/60",
                  selectedProposalId === proposal.id && "bg-accent",
                )}
              >
                <div className="flex items-center gap-1.5">
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">
                    {proposal.title}
                  </span>
                  <StatusPill intent={proposalStatusIntent(proposal.status)}>
                    {proposalStatusLabel(proposal.status)}
                  </StatusPill>
                </div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">
                  {proposal.submittedByUserId} ·{" "}
                  {formatDateTime(proposal.createdAt)}
                </div>
              </button>
            ))
          )}
          {queue.hasNextPage ? (
            <Button
              size="sm"
              variant="ghost"
              className="mt-1 w-full"
              disabled={queue.isLoadingMore}
              onClick={() => void queue.loadMore()}
            >
              {queue.isLoadingMore ? "불러오는 중…" : "더 보기"}
            </Button>
          ) : null}
        </div>
        <Separator />
        <nav className="p-1.5">
          {subviews.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSubview(item.id)}
              className={cn(
                "flex h-8 w-full items-center gap-2 rounded border border-transparent px-2 text-xs",
                subview === item.id
                  ? "border-primary/40 bg-primary/10 font-medium text-primary"
                  : "hover:bg-muted/60",
              )}
            >
              <item.icon className="size-3.5" />
              {item.label}
            </button>
          ))}
        </nav>
      </aside>

      <main className="min-w-0 flex-1 overflow-auto bg-canvas p-4">
        {!selectedProposalId ? (
          <EmptyState
            icon={GitPullRequestArrow}
            title="제안을 선택하세요"
            description="좌측 목록에서 제안을 선택하면 개요·리뷰·변경 이력을 확인할 수 있습니다."
          />
        ) : detail.isLoading ? (
          <LoadingState rowCount={6} />
        ) : detail.error ? (
          <ErrorState error={detail.error} onRetry={detail.refetch} />
        ) : detail.proposal ? (
          subview === "overview" ? (
            <ProposalOverview
              proposal={detail.proposal}
              review={review}
              onGoToReview={() => setSubview("review")}
            />
          ) : subview === "review" ? (
            <ProposalReviewPanel proposal={detail.proposal} review={review} />
          ) : (
            <ChangelogCard timeline={detail.proposal.timeline ?? []} />
          )
        ) : null}
      </main>
    </div>
  );
}

function ChangelogCard({
  timeline,
}: {
  timeline: Array<Record<string, unknown>>;
}) {
  if (timeline.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="변경 이력이 없습니다"
        description="제출·지정·결정·적용 이벤트가 기록되면 여기에 표시됩니다."
      />
    );
  }
  return (
    <div className="rounded border bg-card">
      <div className="p-3 text-xs font-semibold">변경 이력</div>
      <Separator />
      <ul className="p-3">
        {timeline.map((entry, index) => {
          const event =
            typeof entry.event === "string"
              ? entry.event
              : typeof entry.kind === "string"
                ? entry.kind
                : "event";
          const at =
            typeof entry.at === "string"
              ? entry.at
              : typeof entry.createdAt === "string"
                ? entry.createdAt
                : null;
          const actor =
            typeof entry.byUserId === "string"
              ? entry.byUserId
              : typeof entry.actorUserId === "string"
                ? entry.actorUserId
                : null;
          return (
            <li
              key={`${event}-${index}`}
              className="flex items-center gap-2 border-b py-1.5 text-xs last:border-b-0"
            >
              <span className="size-1.5 shrink-0 rounded-full bg-primary" />
              <span className="font-mono text-[11px]">{event}</span>
              {actor ? (
                <span className="text-muted-foreground">by {actor}</span>
              ) : null}
              <span className="ml-auto text-[11px] text-muted-foreground">
                {formatDateTime(at)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
