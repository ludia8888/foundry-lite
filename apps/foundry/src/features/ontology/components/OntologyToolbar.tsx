import { idempotencyKey } from "@foundry-lite/sdk";
import type {
  FoundryLiteOntologyBranchCreatePayload,
  FoundryLiteOntologyBranchMutationsState,
} from "@foundry-lite/sdk/react";
import { Box, ChevronDown, GitBranch, Search, X } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

export type OntologyTab = "ontology" | "proposals";

interface OntologyToolbarProps {
  tab: OntologyTab;
  versionLabel: string | null;
  search: string;
  isDraftDirty: boolean;
  /** 편집 중인 열린 브랜치 이름 — 있으면 다크 브랜치 바를 표시한다. */
  editingBranchName: string | null;
  isSavingToBranch: boolean;
  createMutation: FoundryLiteOntologyBranchMutationsState["create"];
  onTabChange: (tab: OntologyTab) => void;
  onSearchChange: (search: string) => void;
  onDiscardDraft: () => void;
  onReturnToMain: () => void;
  onSaveToBranch: () => void;
  onCreateBranch: (
    payload: FoundryLiteOntologyBranchCreatePayload,
  ) => Promise<unknown>;
}

/** 상단 툴바: 온톨로지 스위처 + 탭 + 검색 + 변경 취소/브랜치 만들기. */
export function OntologyToolbar({
  tab,
  versionLabel,
  search,
  isDraftDirty,
  editingBranchName,
  isSavingToBranch,
  createMutation,
  onTabChange,
  onSearchChange,
  onDiscardDraft,
  onReturnToMain,
  onSaveToBranch,
  onCreateBranch,
}: OntologyToolbarProps) {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [branchName, setBranchName] = useState("");
  const [pendingKey, setPendingKey] = useState<string | null>(null);

  const handleOpenCreate = () => {
    setBranchName("");
    setPendingKey(null);
    setIsCreateOpen(true);
  };

  const handleSubmitCreate = () => {
    const name = branchName.trim();
    if (name.length === 0) return;
    const key = idempotencyKey("ontology.branches.create", name);
    setPendingKey(key);
    void onCreateBranch({ name, idempotencyKey: key }).then((created) => {
      if (created) setIsCreateOpen(false);
    });
  };

  return (
    <div className="shrink-0">
      {editingBranchName ? (
        <div
          className="flex h-7 items-center px-3 text-[11px] text-[#c5cbd3]"
          style={{ backgroundColor: "#100d20" }}
          data-testid="ontology-branch-topbar"
        >
          <button
            type="button"
            onClick={onReturnToMain}
            className="flex items-center gap-1.5 hover:text-white"
          >
            <X className="size-3" />
            메인 브랜치로 돌아가기
          </button>
          <div className="flex flex-1 items-center justify-center gap-1.5">
            <span className="text-[#8f99a8]">브랜치 편집 중</span>
            <GitBranch className="size-3 text-[#c5cbd3]" />
            <span className="font-medium text-white">{editingBranchName}</span>
            <ChevronDown className="size-3 text-[#8f99a8]" />
          </div>
          <span className="w-[132px]" aria-hidden />
        </div>
      ) : null}
      <div className="flex h-12 items-center gap-2 border-b bg-card pr-3">
        <div className="flex h-full w-12 shrink-0 items-center justify-center border-r bg-[#eff3fc] text-primary">
          <Box className="size-4.5" />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger className="flex h-7 items-center gap-1 rounded px-2 text-xs font-medium text-primary hover:bg-primary/10">
            온톨로지
            <ChevronDown className="size-3.5" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64">
            <DropdownMenuLabel className="section-label">
              온톨로지 목록
            </DropdownMenuLabel>
            <DropdownMenuItem className="flex-col items-start gap-0.5 bg-primary/5">
              <span className="text-xs font-medium text-primary">
                기본 온톨로지
              </span>
              <span className="text-[11px] text-muted-foreground">
                테넌트 기본 온톨로지 · {versionLabel ?? "버전 로딩 중"}
              </span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled className="justify-between text-xs">
              리소스 마이그레이션
              <StatusPill intent="neutral">future</StatusPill>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Separator orientation="vertical" className="mx-1 h-5" />
        <div className="flex items-center gap-1">
          {(
            [
              ["ontology", "온톨로지"],
              ["proposals", "제안"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => onTabChange(id)}
              className={cn(
                "h-7 rounded px-2.5 text-xs text-primary",
                tab === id ? "bg-[#d6e4f8] font-medium" : "hover:bg-primary/10",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex min-w-0 flex-1 justify-center">
          <div className="relative w-64">
            <Search className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => onSearchChange(event.target.value)}
              placeholder="리소스 검색…"
              className={cn("h-7 bg-[#f6f7f9] pl-7 text-xs", search && "pr-12")}
            />
            {search ? (
              <button
                type="button"
                onClick={() => onSearchChange("")}
                className="absolute top-1/2 right-2 -translate-y-1/2 text-xs text-muted-foreground hover:text-foreground"
              >
                지우기
              </button>
            ) : null}
          </div>
        </div>
        <Button
          size="sm"
          variant="secondary"
          className="text-muted-foreground"
          disabled={!isDraftDirty}
          onClick={onDiscardDraft}
        >
          변경 취소
        </Button>
        {editingBranchName ? (
          <Button
            size="sm"
            className="bg-success text-success-foreground hover:bg-success/90"
            disabled={!isDraftDirty || isSavingToBranch}
            onClick={onSaveToBranch}
          >
            <GitBranch />
            {isSavingToBranch ? "저장하는 중…" : "브랜치에 저장"}
          </Button>
        ) : (
          <Button
            size="sm"
            className="bg-success text-success-foreground hover:bg-success/90"
            onClick={handleOpenCreate}
          >
            <GitBranch />
            브랜치 만들기
          </Button>
        )}

        <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>브랜치 만들기</DialogTitle>
              <DialogDescription>
                메인 온톨로지에서 분기한 작업 브랜치를 만들고, 드래프트 편집 →
                검증 → 제안 순서로 진행합니다.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="ontology-branch-name" className="text-xs">
                브랜치 이름
              </Label>
              <Input
                id="ontology-branch-name"
                value={branchName}
                onChange={(event) => setBranchName(event.target.value)}
                placeholder="예: order-status-refactor"
                className="h-8 text-xs"
              />
              {pendingKey ? (
                <p className="font-mono text-[11px] text-muted-foreground">
                  idempotency_key={pendingKey}
                </p>
              ) : null}
              {createMutation.error ? (
                <ErrorState error={createMutation.error} />
              ) : null}
            </div>
            <DialogFooter>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setIsCreateOpen(false)}
              >
                취소
              </Button>
              <Button
                size="sm"
                className="bg-success text-success-foreground hover:bg-success/90"
                disabled={
                  branchName.trim().length === 0 || createMutation.isRunning
                }
                onClick={handleSubmitCreate}
              >
                <GitBranch />
                {createMutation.isRunning ? "만드는 중…" : "브랜치 만들기"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
