import type { PipelineBranch } from "@foundry-lite/sdk";
import {
  Check,
  ChevronDown,
  CircleArrowUp,
  Copy,
  GitBranch,
  GitPullRequestArrow,
  HelpCircle,
  Plus,
  Redo2,
  RefreshCcw,
  Rocket,
  Settings2,
  ShieldCheck,
  Star,
  TestTube2,
  Undo2,
  Users,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

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
  isTesting: boolean;
  testState: "missing" | "passed" | "failed" | "stale";
  canPropose: boolean;
  canUndo: boolean;
  canRedo: boolean;
  isBaseStale: boolean;
  isProtected: boolean;
  isRebasing: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onViewChange: (view: BuilderView) => void;
  onSelectBranch: (branchId: string) => void;
  onCreateBranch: () => void;
  onRebase: () => void;
  onSave: () => void;
  onRunTests: () => void;
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
  isTesting,
  testState,
  canPropose,
  canUndo,
  canRedo,
  isBaseStale,
  isProtected,
  isRebasing,
  onUndo,
  onRedo,
  onViewChange,
  onSelectBranch,
  onCreateBranch,
  onRebase,
  onSave,
  onRunTests,
  onPropose,
}: BuilderToolbarProps) {
  const [isStarred, setIsStarred] = useState(false);
  const [isCopied, setIsCopied] = useState(false);
  const copyResetTimer = useRef<number | null>(null);
  const pipelineName = asText(branch?.pipelineId) ?? "파이프라인";
  const branchName = asText(branch?.name) ?? asText(branchId) ?? "브랜치";
  useEffect(
    () => () => {
      if (copyResetTimer.current !== null) {
        window.clearTimeout(copyResetTimer.current);
      }
    },
    [],
  );
  const handleCopyLink = () => {
    if (!navigator.clipboard) return;
    void navigator.clipboard.writeText(window.location.href).then(() => {
      setIsCopied(true);
      if (copyResetTimer.current !== null) {
        window.clearTimeout(copyResetTimer.current);
      }
      copyResetTimer.current = window.setTimeout(() => {
        setIsCopied(false);
        copyResetTimer.current = null;
      }, 1600);
    });
  };

  return (
    <header className="flex h-11 shrink-0 items-stretch border-b border-[#C5CBD3] bg-card">
      <Link
        to="/"
        aria-label="Foundry 홈으로 이동"
        className="flex w-11 shrink-0 items-center justify-center border-r border-[#C5CBD3] bg-[#EAF5F4] hover:bg-[#DCEFED]"
      >
        <PipelineGlyph />
      </Link>

      <div className="flex min-w-0 flex-col justify-center gap-0.5 border-r border-[#C5CBD3] pr-3 pl-3">
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
            aria-label={isStarred ? "즐겨찾기 해제" : "즐겨찾기"}
            className="flex size-4 items-center justify-center text-muted-foreground hover:bg-muted"
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
          <BuilderHeaderMenus
            isDirty={isDirty}
            isSaving={isSaving}
            onSave={onSave}
            onCreateBranch={onCreateBranch}
            onViewChange={onViewChange}
          />
          <span className="h-3 w-px bg-[#C5CBD3]" aria-hidden="true" />
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
          {isProtected ? (
            <span className="flex items-center gap-1 text-[10px] font-semibold text-[#1C6B42]">
              <ShieldCheck className="size-3" />
              Protected
            </span>
          ) : null}
        </div>
      </div>

      <nav
        role="tablist"
        aria-label="파이프라인 작업 보기"
        className="flex shrink-0 items-stretch gap-1 px-3"
      >
        {(Object.keys(VIEW_LABELS) as BuilderView[]).map((view) => (
          <button
            key={view}
            type="button"
            role="tab"
            aria-selected={activeView === view}
            className={cn(
              "relative flex items-center gap-1 border-b-2 px-2.5 text-[13px] whitespace-nowrap transition-colors",
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
      </nav>

      <div className="ml-auto flex items-center gap-1.5 overflow-x-auto pr-2">
        {/* undo/redo: 로컬 그래프 편집 스택 */}
        <div className="flex overflow-hidden rounded-[2px] border border-[#AEB6C1]">
          <button
            type="button"
            aria-label="실행 취소"
            title="실행 취소"
            disabled={!canUndo}
            className="flex size-7 items-center justify-center bg-[#F1F3F5] text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
            onClick={onUndo}
          >
            <Undo2 className="size-3.5" />
          </button>
          <button
            type="button"
            aria-label="다시 실행"
            title="다시 실행"
            disabled={!canRedo}
            className="flex size-7 items-center justify-center border-l bg-[#F1F3F5] text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
            onClick={onRedo}
          >
            <Redo2 className="size-3.5" />
          </button>
        </div>

        <div className="flex items-center">
          <Select value={branchId ?? ""} onValueChange={onSelectBranch}>
            <SelectTrigger
              aria-label="파이프라인 브랜치"
              size="sm"
              className="h-7 w-32 gap-1 rounded-[2px] border-[#AEB6C1] text-[12px]"
            >
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
            aria-label="새 브랜치"
            className="size-7 rounded-[2px] px-0"
            title="새 브랜치"
            onClick={onCreateBranch}
          >
            <Plus className="size-3.5" />
          </Button>
        </div>

        <Button
          size="sm"
          className="h-7 rounded-[2px] border border-[#238551] bg-white px-2.5 text-[12px] text-[#1C6B42] hover:bg-[#F1FAF5] disabled:opacity-60"
          disabled={!isDirty || isSaving || !branchId}
          title={isDirty ? "변경 사항 저장" : "저장할 변경이 없습니다"}
          onClick={onSave}
        >
          <CircleArrowUp className="size-3.5" />
          {isSaving ? "저장 중..." : "저장"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 rounded-[2px] border-[#AEB6C1] px-2 text-[11px]"
          disabled={!isBaseStale || isRebasing || isDirty || !branchId}
          title={
            isBaseStale
              ? "최신 Pipeline 버전과 3-way rebase"
              : "브랜치 base가 최신입니다"
          }
          onClick={onRebase}
        >
          <RefreshCcw className="size-3.5" />
          {isRebasing ? "Rebasing..." : "Rebase"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className={cn(
            "h-7 rounded-[2px] px-2 text-[11px]",
            testState === "passed"
              ? "border-[#238551] bg-[#F1FAF5] text-[#1C6B42]"
              : testState === "failed"
                ? "border-destructive/50 text-destructive"
                : "border-[#AEB6C1]",
          )}
          disabled={isDirty || isTesting || !branchId}
          title={
            isDirty
              ? "먼저 변경 사항을 저장하세요"
              : "저장된 작업의 구조와 출력 설정을 검사합니다"
          }
          onClick={onRunTests}
        >
          <TestTube2 className="size-3.5" />
          {isTesting
            ? "작업 테스트 중..."
            : testState === "passed"
              ? "작업 테스트 통과"
              : testState === "failed"
                ? "작업 테스트 실패"
                : testState === "stale"
                  ? "작업 다시 테스트"
                  : "작업 테스트"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 rounded-[2px] border border-transparent px-2 text-[12px] text-muted-foreground hover:border-[#AEB6C1]"
          disabled={!canPropose || isProposing}
          title={
            canPropose
              ? "통과한 작업 테스트와 함께 리뷰를 요청합니다"
              : "변경을 저장하고 작업 테스트를 통과해야 제안할 수 있습니다"
          }
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
              className="h-7 rounded-[2px] border-[#AEB6C1] px-2.5 text-[12px] text-primary"
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
            className="flex h-7 items-center overflow-hidden rounded-[2px] border border-[#AEB6C1] bg-[#F1F3F5] font-mono text-[11px]"
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
        <span className="mx-0.5 h-5 w-px bg-[#C5CBD3]" aria-hidden="true" />
        <button
          type="button"
          aria-live="polite"
          className="flex h-7 items-center gap-1.5 px-2 text-[12px] hover:bg-muted"
          onClick={handleCopyLink}
        >
          {isCopied ? (
            <Copy className="size-3.5 text-success" />
          ) : (
            <Users className="size-3.5" />
          )}
          {isCopied ? "링크 복사됨" : "공유"}
        </button>
      </div>
    </header>
  );
}

function BuilderHeaderMenus({
  isDirty,
  isSaving,
  onSave,
  onCreateBranch,
  onViewChange,
}: {
  isDirty: boolean;
  isSaving: boolean;
  onSave: () => void;
  onCreateBranch: () => void;
  onViewChange: (view: BuilderView) => void;
}) {
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-[#4F5B6A]">
      <DropdownMenu>
        <DropdownMenuTrigger className="flex items-center gap-0.5 hover:text-foreground">
          파일 <ChevronDown className="size-2.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-44">
          <DropdownMenuItem
            disabled={!isDirty || isSaving}
            onSelect={onSave}
          >
            변경 사항 저장
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={onCreateBranch}>
            새 브랜치
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger className="flex items-center gap-0.5 hover:text-foreground">
          설정 <ChevronDown className="size-2.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-48">
          <DropdownMenuItem onSelect={() => onViewChange("history")}>
            <Settings2 className="size-3.5" />
            배포 및 실행 설정
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={onCreateBranch}>
            <GitBranch className="size-3.5" />
            브랜치 관리
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger className="flex items-center gap-0.5 hover:text-foreground">
          도움말 <ChevronDown className="size-2.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-52">
          <DropdownMenuItem
            onSelect={() =>
              window.open(
                "https://www.palantir.com/docs/foundry/pipeline-builder/overview",
                "_blank",
                "noopener,noreferrer",
              )
            }
          >
            <HelpCircle className="size-3.5" />
            Pipeline Builder 공개 문서
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
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
