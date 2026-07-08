import type { PipelineProposal } from "@foundry-lite/sdk";
import { useMemo } from "react";

import { StatusPill } from "@/components/shared/StatusPill";

import { statusLabel } from "../lib/approvals-view";
import { pipelineDiffModel } from "../lib/diff-model";
import { PipelineDiffViewer } from "./DiffViewer";
import { EvidenceRow } from "./EvidenceRow";

interface PipelineProposalDetailProps {
  proposal: PipelineProposal;
}

function field(proposal: PipelineProposal, key: string): string | null {
  const value = (proposal as Record<string, unknown>)[key];
  return typeof value === "string" ? value : null;
}

/** 파이프라인 제안 상세: 그래프 diff + 결정 증거 (현재 시드는 모두 실행 완료 상태). */
export function PipelineProposalDetail({
  proposal,
}: PipelineProposalDetailProps) {
  const diff = useMemo(() => pipelineDiffModel(proposal), [proposal]);
  const title = field(proposal, "title") ?? proposal.id;
  const description = field(proposal, "description");

  return (
    <div className="space-y-3">
      <div className="rounded border p-2.5">
        <div className="mb-1 flex items-center gap-2">
          <span className="section-label">파이프라인 제안</span>
          <StatusPill intent="neutral" className="ml-auto">
            {statusLabel(proposal.status)}
          </StatusPill>
        </div>
        <div className="text-[13px] font-medium">{title}</div>
        {description ? (
          <p className="mt-1 text-[12px] text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>

      <PipelineDiffViewer diff={diff} />

      <p className="rounded border border-dashed px-2.5 py-2 text-[11px] text-muted-foreground">
        파이프라인 제안 결정은 Pipeline Builder에서 수행됩니다. 이 화면에서는
        통합 리뷰 큐 관점의 diff와 결정 증거를 읽기 전용으로 제공합니다{" "}
        <StatusPill intent="warning">future</StatusPill>
      </p>

      <div className="rounded border px-2.5">
        <EvidenceRow label="proposal_id">{proposal.id}</EvidenceRow>
        <EvidenceRow label="pipeline_id">
          {field(proposal, "pipelineId") ?? "-"}
        </EvidenceRow>
        <EvidenceRow label="branch_id">
          {field(proposal, "branchId") ?? "-"}
        </EvidenceRow>
        <EvidenceRow label="created_by">
          {field(proposal, "createdBy") ?? "-"}
        </EvidenceRow>
      </div>
    </div>
  );
}
