import { idempotencyKey } from "@foundry-lite/sdk";
import type { OntologyBranchRebaseResolution } from "@foundry-lite/sdk";
import type {
  FoundryLiteOntologyBranchDiffState,
  FoundryLiteOntologyBranchMutationsState,
  FoundryLiteOntologyBranchState,
} from "@foundry-lite/sdk/react";
import {
  ChevronDown,
  GitCompareArrows,
  GitPullRequestArrow,
} from "lucide-react";
import { useMemo, useState } from "react";

import type { DataTableColumn } from "@/components/shared/DataTable";
import { DataTable } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

import type { OntologyResourceKind } from "../lib/ontology-view";
import {
  DIFF_CHANGE_INTENTS,
  DIFF_CHANGE_LABELS,
  RESOURCE_KIND_LABELS,
} from "../lib/ontology-view";

type DiffRow = {
  kind: string;
  apiName: string;
  branchChange: string;
  mainChange: string;
  hasConflict: boolean;
};

interface BranchDiffPanelProps {
  branchDetail: FoundryLiteOntologyBranchState;
  diff: FoundryLiteOntologyBranchDiffState;
  rebaseMutation: FoundryLiteOntologyBranchMutationsState["rebase"];
  proposeMutation: FoundryLiteOntologyBranchMutationsState["propose"];
  onRebase: (resolutions: OntologyBranchRebaseResolution[]) => void;
  onPropose: (input: {
    title: string;
    description: string | null;
    idempotencyKey: string;
  }) => Promise<unknown>;
}

/** 브랜치 변경 비교(diff) + 리베이스 + 변경 제안 다이얼로그. */
export function BranchDiffPanel({
  branchDetail,
  diff,
  rebaseMutation,
  proposeMutation,
  onRebase,
  onPropose,
}: BranchDiffPanelProps) {
  const [isOpen, setIsOpen] = useState(true);
  const [resolutionByKey, setResolutionByKey] = useState<
    Record<string, "main" | "branch">
  >({});

  const branch = branchDetail.branch;
  const changedCount = useMemo(
    () =>
      diff.resources.filter((resource) => resource.branchChange !== "unchanged")
        .length,
    [diff.resources],
  );

  const handleRebase = () => {
    const resolutions: OntologyBranchRebaseResolution[] = diff.conflicts.map(
      (conflict) => ({
        kind: conflict.kind,
        apiName: conflict.apiName,
        use:
          resolutionByKey[`${conflict.kind}:${conflict.apiName}`] ?? "branch",
      }),
    );
    onRebase(resolutions);
  };

  const columns: DataTableColumn<DiffRow>[] = [
    {
      key: "kind",
      header: "종류",
      className: "w-24",
      render: (row) =>
        RESOURCE_KIND_LABELS[row.kind as OntologyResourceKind] ?? row.kind,
    },
    {
      key: "apiName",
      header: "API 이름",
      isMono: true,
      render: (row) => row.apiName,
    },
    {
      key: "branchChange",
      header: "브랜치 변경",
      className: "w-24",
      render: (row) => (
        <StatusPill intent={DIFF_CHANGE_INTENTS[row.branchChange] ?? "neutral"}>
          {DIFF_CHANGE_LABELS[row.branchChange] ?? row.branchChange}
        </StatusPill>
      ),
    },
    {
      key: "mainChange",
      header: "메인 변경",
      className: "w-24",
      render: (row) => (
        <StatusPill intent={DIFF_CHANGE_INTENTS[row.mainChange] ?? "neutral"}>
          {DIFF_CHANGE_LABELS[row.mainChange] ?? row.mainChange}
        </StatusPill>
      ),
    },
    {
      key: "conflict",
      header: "충돌",
      className: "w-16",
      render: (row) =>
        row.hasConflict ? (
          <StatusPill intent="danger">충돌</StatusPill>
        ) : (
          <span className="text-[11px] text-muted-foreground">—</span>
        ),
    },
  ];

  return (
    <Collapsible
      open={isOpen}
      onOpenChange={setIsOpen}
      className="rounded border bg-card"
    >
      <CollapsibleTrigger className="flex h-9 w-full items-center gap-2 px-3 text-left">
        <ChevronDown
          className={cn(
            "size-3.5 text-muted-foreground transition-transform",
            !isOpen && "-rotate-90",
          )}
        />
        <GitCompareArrows className="size-3.5 text-primary" />
        <span className="text-xs font-semibold">브랜치 변경 비교</span>
        {branch ? (
          <>
            <span className="font-mono text-[11px] text-muted-foreground">
              {branch.name} ↔ 메인
            </span>
            {changedCount > 0 ? (
              <StatusPill intent="warning">변경 {changedCount}건</StatusPill>
            ) : null}
            {diff.hasConflicts ? (
              <StatusPill intent="danger">
                충돌 {diff.conflicts.length}건
              </StatusPill>
            ) : null}
          </>
        ) : null}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <Separator />
        <div className="space-y-3 p-3">
          {!branch ? (
            <EmptyState
              icon={GitCompareArrows}
              title="브랜치를 선택하세요"
              description="브랜치를 선택하면 메인 대비 리소스별 변경(diff)과 충돌을 보여줍니다."
            />
          ) : diff.isLoading ? (
            <LoadingState rowCount={4} />
          ) : diff.error ? (
            <ErrorState error={diff.error} onRetry={diff.refetch} />
          ) : (
            <>
              <DataTable
                columns={columns}
                rows={diff.resources}
                rowKey={(row) => `${row.kind}:${row.apiName}`}
                emptyMessage="메인과 비교해 변경된 리소스가 없습니다."
              />

              {diff.baseStale || diff.hasConflicts ? (
                <div className="space-y-2 rounded border border-warning/40 bg-warning/10 p-2">
                  <div className="text-[11px] font-medium text-warning">
                    메인이 앞서 있습니다. 제안 전에 리베이스하세요.
                  </div>
                  {diff.conflicts.map((conflict) => {
                    const key = `${conflict.kind}:${conflict.apiName}`;
                    return (
                      <div
                        key={key}
                        className="flex flex-wrap items-center gap-2 text-[11px]"
                      >
                        <span className="font-mono">{conflict.apiName}</span>
                        <ToggleGroup
                          type="single"
                          size="sm"
                          variant="outline"
                          value={resolutionByKey[key] ?? "branch"}
                          onValueChange={(value) => {
                            if (value !== "main" && value !== "branch") return;
                            setResolutionByKey((current) => ({
                              ...current,
                              [key]: value,
                            }));
                          }}
                        >
                          <ToggleGroupItem
                            value="branch"
                            className="h-6 px-2 text-[11px]"
                          >
                            브랜치 버전 유지
                          </ToggleGroupItem>
                          <ToggleGroupItem
                            value="main"
                            className="h-6 px-2 text-[11px]"
                          >
                            메인 버전 사용
                          </ToggleGroupItem>
                        </ToggleGroup>
                      </div>
                    );
                  })}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={rebaseMutation.isRunning}
                    onClick={handleRebase}
                  >
                    {rebaseMutation.isRunning ? "리베이스 중…" : "리베이스"}
                  </Button>
                  {rebaseMutation.error ? (
                    <ErrorState error={rebaseMutation.error} />
                  ) : null}
                </div>
              ) : null}

              {branch.status === "open" && branch.proposalId === null ? (
                <ProposeDialog
                  branchName={branch.name}
                  changedCount={changedCount}
                  isBlocked={diff.baseStale || diff.hasConflicts}
                  proposeMutation={proposeMutation}
                  onPropose={onPropose}
                />
              ) : branch.proposalId ? (
                <p className="text-[11px] text-muted-foreground">
                  이 브랜치는 이미 제안으로 전환되었습니다. 제안 탭에서 리뷰를
                  진행하세요.
                </p>
              ) : null}
            </>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function ProposeDialog({
  branchName,
  changedCount,
  isBlocked,
  proposeMutation,
  onPropose,
}: {
  branchName: string;
  changedCount: number;
  isBlocked: boolean;
  proposeMutation: FoundryLiteOntologyBranchMutationsState["propose"];
  onPropose: BranchDiffPanelProps["onPropose"];
}) {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [pendingKey, setPendingKey] = useState<string | null>(null);

  const handleOpen = () => {
    setTitle(`"${branchName}" 변경 제안`);
    setDescription("");
    setPendingKey(null);
    setIsDialogOpen(true);
  };

  const handleSubmit = () => {
    const trimmedTitle = title.trim();
    if (trimmedTitle.length === 0) return;
    const key = idempotencyKey("ontology.branches.propose", branchName);
    setPendingKey(key);
    void onPropose({
      title: trimmedTitle,
      description: description.trim().length > 0 ? description.trim() : null,
      idempotencyKey: key,
    }).then((result) => {
      if (result) setIsDialogOpen(false);
    });
  };

  return (
    <>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={isBlocked} onClick={handleOpen}>
          <GitPullRequestArrow />
          변경 제안 만들기
        </Button>
        {isBlocked ? (
          <span className="text-[11px] text-muted-foreground">
            리베이스를 완료해야 제안할 수 있습니다.
          </span>
        ) : null}
      </div>
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>변경 제안 만들기</DialogTitle>
            <DialogDescription>
              이 브랜치의 온톨로지 변경 {changedCount}건을 초안 제안으로
              전환합니다. 모든 리뷰가 승인되면 제안을 머지할 수 있습니다.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="ontology-propose-title" className="text-xs">
                제목 (필수)
              </Label>
              <Input
                id="ontology-propose-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                className="h-8 text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ontology-propose-description" className="text-xs">
                설명 (선택)
              </Label>
              <Textarea
                id="ontology-propose-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="이 제안에 담긴 변경을 간단히 설명하세요"
                className="min-h-20 text-xs"
              />
            </div>
            {pendingKey ? (
              <p className="font-mono text-[11px] text-muted-foreground">
                idempotency_key={pendingKey}
              </p>
            ) : null}
            {proposeMutation.error ? (
              <ErrorState error={proposeMutation.error} />
            ) : null}
          </div>
          <DialogFooter>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setIsDialogOpen(false)}
            >
              취소
            </Button>
            <Button
              size="sm"
              disabled={title.trim().length === 0 || proposeMutation.isRunning}
              onClick={handleSubmit}
            >
              <GitPullRequestArrow />
              {proposeMutation.isRunning ? "제안 만드는 중…" : "변경 제안"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
