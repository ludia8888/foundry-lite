import type {
  FoundryLiteOntologyBranchesState,
  FoundryLiteOntologyBranchMutationsState,
  FoundryLiteOntologyBranchState,
} from "@foundry-lite/sdk/react";
import { AlertTriangle, GitBranch, Trash2 } from "lucide-react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

import {
  BRANCH_STATUS_LABELS,
  formatDateTime,
  truncateMiddle,
} from "../lib/ontology-view";

const MAIN_BRANCH_VALUE = "__main__";

interface BranchPanelProps {
  branches: FoundryLiteOntologyBranchesState;
  branchDetail: FoundryLiteOntologyBranchState;
  selectedBranchId: string | null;
  abandonMutation: FoundryLiteOntologyBranchMutationsState["abandon"];
  onSelectBranch: (branchId: string | null) => void;
  onAbandonBranch: (branchId: string) => void;
}

/** 좌측 브랜치 선택 카드: 셀렉터 + 기본 배지 + 브랜치 메타/경고. */
export function BranchPanel({
  branches,
  branchDetail,
  selectedBranchId,
  abandonMutation,
  onSelectBranch,
  onAbandonBranch,
}: BranchPanelProps) {
  const branch =
    selectedBranchId !== null && branchDetail.branch?.id === selectedBranchId
      ? branchDetail.branch
      : null;
  return (
    <div className="space-y-2 rounded border bg-card p-3">
      <div className="section-label">브랜치 선택</div>
      {branches.isLoading ? (
        <Skeleton className="h-8 w-full" />
      ) : (
        <Select
          value={selectedBranchId ?? MAIN_BRANCH_VALUE}
          onValueChange={(value) =>
            onSelectBranch(value === MAIN_BRANCH_VALUE ? null : value)
          }
        >
          <SelectTrigger size="sm" className="w-full text-xs">
            <GitBranch className="size-3.5 shrink-0 text-muted-foreground" />
            <SelectValue placeholder="브랜치 선택" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={MAIN_BRANCH_VALUE} className="text-xs">
              메인 브랜치
              <StatusPill intent="neutral">기본</StatusPill>
            </SelectItem>
            {branches.branches.map((item) => (
              <SelectItem key={item.id} value={item.id} className="text-xs">
                {item.name}
                <StatusPill
                  intent={item.status === "open" ? "info" : "neutral"}
                >
                  {BRANCH_STATUS_LABELS[item.status] ?? item.status}
                </StatusPill>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      {branches.error ? (
        <ErrorState error={branches.error} onRetry={branches.reload} />
      ) : null}

      {selectedBranchId && branchDetail.isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : null}
      {branchDetail.error ? (
        <ErrorState error={branchDetail.error} onRetry={branchDetail.refetch} />
      ) : null}

      {branch ? (
        <div className="space-y-1.5 border-t pt-2 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">상태</span>
            <StatusPill intent={branch.status === "open" ? "info" : "neutral"}>
              {BRANCH_STATUS_LABELS[branch.status] ?? branch.status}
            </StatusPill>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">기준 버전</span>
            <span className="font-mono text-[11px]">
              v{branch.baseVersionNumber}
            </span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">핑거프린트</span>
            <span className="font-mono text-[11px]">
              {truncateMiddle(branch.contentFingerprint, 18)}
            </span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">생성</span>
            <span className="text-[11px]">
              {formatDateTime(branch.createdAt)}
            </span>
          </div>
          {branchDetail.baseStale ? (
            <div className="flex items-start gap-1.5 rounded bg-warning/10 p-2 text-[11px] text-warning">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              메인이 이 브랜치의 기준 버전보다 앞서 있습니다. 제안 전에
              리베이스하세요.
            </div>
          ) : null}
          {branch.status === "open" ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 w-full justify-start px-2 text-destructive hover:text-destructive"
              disabled={abandonMutation.isRunning}
              onClick={() => onAbandonBranch(branch.id)}
            >
              <Trash2 />
              {abandonMutation.isRunning ? "폐기하는 중…" : "브랜치 폐기"}
            </Button>
          ) : null}
          {abandonMutation.error ? (
            <ErrorState error={abandonMutation.error} />
          ) : null}
        </div>
      ) : null}
      {!selectedBranchId && !branches.isLoading ? (
        <p className="text-[11px] text-muted-foreground">
          메인 브랜치는 읽기 전용입니다. 변경하려면 브랜치를 만드세요.
        </p>
      ) : null}
    </div>
  );
}
