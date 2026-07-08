import type { PipelineProposal } from "@foundry-lite/sdk";
import { Check, GitPullRequestArrow, Play, Undo2, X } from "lucide-react";
import { useMemo, useState } from "react";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { asText, formatTimestamp } from "../pipeline-model";
import type { PipelineActions } from "../use-pipeline-actions";
import { ProposalDiffPanel } from "./ProposalDiffPanel";

const PROPOSAL_STATUS_INTENT: Record<string, StatusIntent> = {
  submitted: "info",
  approved: "success",
  rejected: "danger",
  executed: "success",
  withdrawn: "neutral",
};

const PROPOSAL_STATUS_LABEL: Record<string, string> = {
  submitted: "검토 대기",
  approved: "승인됨",
  rejected: "반려됨",
  executed: "적용됨",
  withdrawn: "철회됨",
};

interface ProposalsPanelProps {
  proposals: readonly PipelineProposal[];
  actions: PipelineActions;
}

/** 하단 제안 탭: 제출 → 승인/반려 → 적용(버전 생성) review flow + 행 클릭 diff. */
export function ProposalsPanel({ proposals, actions }: ProposalsPanelProps) {
  const [selectedProposalId, setSelectedProposalId] = useState<string | null>(
    null,
  );
  const selectedProposal =
    proposals.find((proposal) => proposal.id === selectedProposalId) ?? null;
  const columns = useMemo<DataTableColumn<PipelineProposal>[]>(
    () => [
      {
        key: "title",
        header: "제안",
        render: (row) => (
          <div className="min-w-0">
            <div className="truncate font-medium">
              {asText(row.title) ?? row.id}
            </div>
            <div className="truncate font-mono text-[10px] text-muted-foreground">
              {row.id}
            </div>
          </div>
        ),
      },
      {
        key: "branch",
        header: "브랜치",
        isMono: true,
        render: (row) => asText(row.branchId) ?? "-",
      },
      {
        key: "status",
        header: "상태",
        render: (row) => (
          <StatusPill intent={PROPOSAL_STATUS_INTENT[row.status] ?? "neutral"}>
            {PROPOSAL_STATUS_LABEL[row.status] ?? row.status}
          </StatusPill>
        ),
      },
      {
        key: "decision",
        header: "결정",
        render: (row) =>
          row.decision ? (
            <span className="text-[11px]">
              {String(row.decision)}
              {asText(row.decisionComment)
                ? ` · ${asText(row.decisionComment)}`
                : ""}
            </span>
          ) : (
            "-"
          ),
      },
      {
        key: "createdAt",
        header: "제출",
        render: (row) => formatTimestamp(row.createdAt),
      },
      {
        key: "actions",
        header: "동작",
        render: (row) => (
          <ProposalRowActions proposal={row} actions={actions} />
        ),
      },
    ],
    [actions],
  );

  return (
    <div className="grid gap-3 p-3 lg:grid-cols-2">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="section-label">변경 제안</span>
          <StatusPill intent="neutral">{proposals.length}건</StatusPill>
          {actions.decideProposal.error ? (
            <StatusPill intent="danger">
              결정 실패 · {actions.decideProposal.error.code}
            </StatusPill>
          ) : null}
          {actions.executeProposal.error ? (
            <StatusPill intent="danger">
              적용 실패 · {actions.executeProposal.error.code}
            </StatusPill>
          ) : null}
          {actions.executeProposal.result ? (
            <StatusPill intent="success">
              버전 v
              {String(actions.executeProposal.result.versionNumber ?? "?")}{" "}
              생성됨
            </StatusPill>
          ) : null}
        </div>
        <DataTable
          columns={columns}
          rows={proposals}
          rowKey={(row) => row.id}
          onRowClick={(row) => setSelectedProposalId(row.id)}
          selectedKey={selectedProposalId}
          emptyMessage="아직 제안이 없습니다. 그래프를 수정하고 '변경 제안'을 눌러 review flow를 시작하세요."
        />
      </div>
      {selectedProposal ? (
        <ProposalDiffPanel proposal={selectedProposal} actions={actions} />
      ) : (
        <div className="space-y-2">
          <span className="section-label">브랜치 diff</span>
          <p className="text-[11px] text-muted-foreground">
            제안 행을 클릭하면 base 버전 대비 노드 추가/삭제/변경 목록과 스키마
            Before/After가 표시됩니다.
          </p>
        </div>
      )}
    </div>
  );
}

function ProposalRowActions({
  proposal,
  actions,
}: {
  proposal: PipelineProposal;
  actions: PipelineActions;
}) {
  const isBusy =
    actions.decideProposal.isRunning ||
    actions.executeProposal.isRunning ||
    actions.withdrawProposal.isRunning;

  if (proposal.status === "submitted") {
    return (
      <div className="flex gap-1">
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-[11px] text-success"
          disabled={isBusy}
          onClick={() =>
            void actions.decideProposal.execute({
              proposalId: proposal.id,
              decision: "approve",
              comment: "빌더 화면에서 승인",
            })
          }
        >
          <Check className="size-3" />
          승인
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-[11px] text-destructive"
          disabled={isBusy}
          onClick={() =>
            void actions.decideProposal.execute({
              proposalId: proposal.id,
              decision: "reject",
              comment: "빌더 화면에서 반려",
            })
          }
        >
          <X className="size-3" />
          반려
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-[11px]"
          disabled={isBusy}
          onClick={() =>
            void actions.withdrawProposal.execute({ proposalId: proposal.id })
          }
        >
          <Undo2 className="size-3" />
          철회
        </Button>
      </div>
    );
  }
  if (proposal.status === "approved") {
    return (
      <Button
        size="sm"
        variant="outline"
        className="h-6 px-2 text-[11px] text-primary"
        disabled={isBusy}
        onClick={() =>
          void actions.executeProposal.execute({ proposalId: proposal.id })
        }
      >
        <Play className="size-3" />
        적용 (버전 생성)
      </Button>
    );
  }
  return (
    <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
      <GitPullRequestArrow className="size-3" />
      완료된 제안
    </span>
  );
}
