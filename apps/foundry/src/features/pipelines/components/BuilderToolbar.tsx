import type { PipelineBranch } from "@foundry-lite/sdk";
import {
  Check,
  ChevronDown,
  CircleArrowUp,
  GitBranch,
  GitPullRequestArrow,
  Plus,
  Redo2,
  Rocket,
  Star,
  Undo2,
  Users,
  X,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
  asText,
  shortFingerprint,
  type PipelineValidationResult,
} from "../pipeline-model";

export type BuilderView = "edit" | "proposals" | "history";

const VIEW_LABELS: Record<BuilderView, string> = {
  edit: "편집",
  proposals: "제안",
  history: "히스토리",
};

/** 상단 좌측 2행의 future 메뉴 (공식 상단 바의 File/Settings/Help). */
const FUTURE_MENUS = ["파일", "설정", "도움말"] as const;

interface BuilderToolbarProps {
  branches: readonly PipelineBranch[];
  branchId: string | null;
  branch: PipelineBranch | null;
  validation: PipelineValidationResult | null;
  validNodeCount: number;
  proposalCount: number;
  activeView: BuilderView;
  isDirty: boolean;
  isSaving: boolean;
  isProposing: boolean;
  canPropose: boolean;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onViewChange: (view: BuilderView) => void;
  onSelectBranch: (branchId: string) => void;
  onCreateBranch: () => void;
  onSave: () => void;
  onPropose: () => void;
}

/**
 * 공식 상단 바 2행 구조:
 * 1행 = [앱 아이콘] [Pipeline Builder > 파이프라인명 > 브랜치명(볼드) ☆] · [편집|제안|히스토리] ·
 *       우측 [↶↷] [브랜치 ▾] [저장(그린)] [변경 제안] [배포 ▾] [✓N ✗N] [공유]
 * 2행 = [파일/설정/도움말 future 메뉴] [분기 아이콘+개수] [Batch 다크 배지]
 */
export function BuilderToolbar({
  branches,
  branchId,
  branch,
  validation,
  validNodeCount,
  proposalCount,
  activeView,
  isDirty,
  isSaving,
  isProposing,
  canPropose,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  onViewChange,
  onSelectBranch,
  onCreateBranch,
  onSave,
  onPropose,
}: BuilderToolbarProps) {
  const [isStarred, setIsStarred] = useState(false);
  const pipelineName = asText(branch?.pipelineId) ?? "파이프라인";
  const branchName = asText(branch?.name) ?? asText(branchId) ?? "브랜치";

  return (
    <div className="flex h-12 shrink-0 items-stretch border-b bg-card">
      {/* 앱 아이콘: 연민트 배경 + 틸 파이프 글리프 */}
      <div className="flex w-12 shrink-0 items-center justify-center bg-[#EAF5F4]">
        <PipelineGlyph />
      </div>

      {/* 좌측 2행: 브레드크럼 / future 메뉴 + 분기 수 + Batch */}
      <div className="flex min-w-0 flex-col justify-center gap-0.5 pr-2 pl-3">
        <div className="flex min-w-0 items-center gap-1 leading-none">
          <span className="hidden truncate text-[12px] text-muted-foreground lg:inline">
            Pipeline Builder
          </span>
          <BreadcrumbCaret />
          <span className="hidden max-w-36 truncate text-[12px] text-muted-foreground lg:inline">
            {pipelineName}
          </span>
          <BreadcrumbCaret />
          <span className="max-w-44 truncate text-[13px] font-bold">
            {branchName}
          </span>
          <button
            type="button"
            className="flex size-4 items-center justify-center rounded text-muted-foreground hover:bg-muted"
            title={isStarred ? "즐겨찾기 해제" : "즐겨찾기"}
            onClick={() => setIsStarred((prev) => !prev)}
          >
            <Star
              className={cn(
                "size-3.5",
                isStarred ? "fill-[#EC9A3C] text-[#EC9A3C]" : null,
              )}
            />
          </button>
        </div>
        <div className="flex items-center gap-2 leading-none">
          <div className="flex items-center gap-1.5">
            {FUTURE_MENUS.map((menu) => (
              <button
                key={menu}
                type="button"
                disabled
                title="곧 제공 예정"
                className="flex cursor-not-allowed items-center gap-0.5 text-[11px] text-muted-foreground/80"
              >
                {menu}
                <ChevronDown className="size-2.5" />
              </button>
            ))}
          </div>
          <Separator orientation="vertical" className="!h-3" />
          <span
            className="flex items-center gap-1 text-[11px] text-muted-foreground"
            title={`브랜치 ${branches.length}개`}
          >
            <GitBranch className="size-3" />
            {branches.length}
          </span>
          <span className="rounded-[2px] bg-[#404854] px-1.5 py-0.5 text-[10px] leading-none font-semibold text-white">
            Batch
          </span>
        </div>
      </div>

      {/* 문서 탭: 편집 | 제안 | 히스토리 (활성 = 파란 밑줄) */}
      <div className="ml-4 flex items-stretch gap-1">
        {(Object.keys(VIEW_LABELS) as BuilderView[]).map((view) => (
          <button
            key={view}
            type="button"
            className={cn(
              "relative flex items-center gap-1 border-b-2 px-2.5 text-[13px] transition-colors",
              activeView === view
                ? "border-primary font-semibold text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
            onClick={() => onViewChange(view)}
          >
            {VIEW_LABELS[view]}
            {view === "proposals" && proposalCount > 0 ? (
              <span className="rounded bg-primary/10 px-1 font-mono text-[10px] text-primary">
                {proposalCount}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      <div className="ml-auto flex items-center gap-1.5 pr-3">
        {/* undo/redo: 로컬 그래프 편집 스택 */}
        <div className="flex overflow-hidden rounded border">
          <button
            type="button"
            title="실행 취소"
            disabled={!canUndo}
            className="flex size-7 items-center justify-center bg-[#F1F3F5] text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
            onClick={onUndo}
          >
            <Undo2 className="size-3.5" />
          </button>
          <button
            type="button"
            title="다시 실행"
            disabled={!canRedo}
            className="flex size-7 items-center justify-center border-l bg-[#F1F3F5] text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
            onClick={onRedo}
          >
            <Redo2 className="size-3.5" />
          </button>
        </div>

        <div className="flex items-center">
          <Select value={branchId ?? undefined} onValueChange={onSelectBranch}>
            <SelectTrigger size="sm" className="h-7 w-36 gap-1 text-[12px]">
              <GitBranch className="size-3.5 shrink-0 text-muted-foreground" />
              <SelectValue placeholder="브랜치 선택" />
            </SelectTrigger>
            <SelectContent>
              {branches.map((item) => (
                <SelectItem
                  key={item.id}
                  value={item.id}
                  className="text-[12px]"
                >
                  {String(item.name ?? item.id)}
                </SelectItem>
              ))}
              {branch ? (
                <div className="border-t px-2 py-1.5 font-mono text-[10px] text-muted-foreground">
                  fp {shortFingerprint(branch.graphFingerprint)}
                </div>
              ) : null}
            </SelectContent>
          </Select>
          <Button
            variant="ghost"
            size="sm"
            className="size-7 px-0"
            title="새 브랜치"
            onClick={onCreateBranch}
          >
            <Plus className="size-3.5" />
          </Button>
        </div>

        <Button
          size="sm"
          className="h-7 bg-success px-2.5 text-[12px] text-success-foreground hover:bg-success/90 disabled:opacity-70"
          disabled={!isDirty || isSaving || !branchId}
          title={isDirty ? "변경 사항 저장" : "저장할 변경이 없습니다"}
          onClick={onSave}
        >
          <CircleArrowUp className="size-3.5" />
          {isSaving ? "저장 중..." : "저장"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-[12px] text-muted-foreground"
          disabled={!canPropose || isProposing}
          onClick={onPropose}
        >
          <GitPullRequestArrow className="size-3.5" />
          {isProposing ? "제안 중..." : "변경 제안"}
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="sm"
              variant="outline"
              className="h-7 px-2.5 text-[12px] text-primary"
            >
              배포
              <ChevronDown className="size-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-52">
            <DropdownMenuLabel className="text-[11px]">
              파이프라인 배포
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-[12px]"
              onSelect={() => onViewChange("history")}
            >
              <Rocket className="size-3.5" />
              히스토리에서 버전 배포…
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* 검증 배지: ✓통과 노드 수 ✗오류 수 */}
        {validation ? (
          <div
            className="flex h-7 items-center overflow-hidden rounded border bg-[#F1F3F5] font-mono text-[11px]"
            title={
              validation.valid
                ? `검증 통과${validation.warnings.length > 0 ? ` · 경고 ${validation.warnings.length}건` : ""}`
                : `검증 오류 ${validation.errors.length}건`
            }
          >
            <span className="flex items-center gap-0.5 px-1.5 text-success">
              <Check className="size-3" />
              {validNodeCount}
            </span>
            <span
              className={cn(
                "flex items-center gap-0.5 border-l px-1.5",
                validation.errors.length > 0
                  ? "bg-destructive/10 text-destructive"
                  : "text-muted-foreground",
              )}
            >
              <X className="size-3" />
              {validation.errors.length}
            </span>
          </div>
        ) : null}
        {isDirty ? (
          <span
            className="size-2 shrink-0 rounded-full bg-[#EC9A3C]"
            title="저장되지 않은 변경"
          />
        ) : null}

        <Separator orientation="vertical" className="!h-5" />
        <button
          type="button"
          disabled
          title="공유 · 곧 제공 예정"
          className="flex h-7 cursor-not-allowed items-center gap-1 px-1.5 text-[12px] text-muted-foreground/80"
        >
          <Users className="size-3.5" />
          공유
        </button>
      </div>
    </div>
  );
}

function BreadcrumbCaret() {
  return (
    <span className="hidden text-[11px] text-muted-foreground/50 lg:inline">
      ›
    </span>
  );
}

/** 공식 앱 아이콘: 좌측 두 노드가 우측 한 노드로 합류하는 틸 파이프 글리프. */
function PipelineGlyph() {
  return (
    <svg viewBox="0 0 20 20" className="size-5" aria-hidden="true">
      <g stroke="#00847A" strokeWidth="2" fill="none">
        <path d="M4 5v10" />
        <path d="M4 10h9" />
      </g>
      <rect x="1.5" y="1.5" width="5" height="5" rx="1" fill="#00847A" />
      <rect x="1.5" y="13.5" width="5" height="5" rx="1" fill="#00847A" />
      <rect x="12.5" y="7.5" width="5" height="5" rx="1" fill="#00847A" />
    </svg>
  );
}
