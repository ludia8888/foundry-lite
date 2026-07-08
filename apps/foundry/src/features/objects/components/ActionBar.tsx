import type { FoundryLiteOntologyActionView } from "@foundry-lite/sdk/react";
import {
  BarChart3,
  ChevronDown,
  LayoutGrid,
  Menu,
  Radio,
  RotateCw,
  Undo2,
  Redo2,
  Zap,
} from "lucide-react";
import type { ComponentPropsWithRef } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

export type ExploreViewTab = "explore" | "results";

interface ActionBarProps {
  resultCount: number;
  viewTab: ExploreViewTab;
  onViewTabChange: (tab: ExploreViewTab) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  actions: FoundryLiteOntologyActionView[];
  onRunAction: (action: FoundryLiteOntologyActionView) => void;
  onExportCsv: () => void;
  canExport: boolean;
  isLive: boolean;
  onLiveChange: (isLive: boolean) => void;
  isStreaming: boolean;
  liveSummary: string | null;
  onRefresh: () => void;
}

function OutlineMenuButton({
  label,
  ...props
}: { label: string } & ComponentPropsWithRef<"button">) {
  return (
    <button
      type="button"
      className="flex h-8 items-center gap-1.5 rounded border border-[#c5cdd4] bg-white px-3 text-[13px] font-medium text-[#1c2127] hover:bg-[#f6f8fa]"
      {...props}
    >
      {label}
      <ChevronDown className="size-3.5 text-[#5f6b7c]" />
    </button>
  );
}

/** 액션 바 (3행): undo/redo + 레이아웃 + 결과 카운트 필 + 탐색/결과 탭 + 액션·열기·내보내기. */
export function ActionBar({
  resultCount,
  viewTab,
  onViewTabChange,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  actions,
  onRunAction,
  onExportCsv,
  canExport,
  isLive,
  onLiveChange,
  isStreaming,
  liveSummary,
  onRefresh,
}: ActionBarProps) {
  return (
    <div className="relative flex h-10 shrink-0 items-center gap-1 border-b border-[#d5dce1] bg-white px-2">
      <button
        type="button"
        aria-label="필터 되돌리기"
        disabled={!canUndo}
        className="flex size-8 items-center justify-center rounded text-[#404854] hover:bg-[#f0f3f5] disabled:text-[#c5cdd4]"
        onClick={onUndo}
      >
        <Undo2 className="size-4" />
      </button>
      <button
        type="button"
        aria-label="필터 다시 실행"
        disabled={!canRedo}
        className="flex size-8 items-center justify-center rounded text-[#404854] hover:bg-[#f0f3f5] disabled:text-[#c5cdd4]"
        onClick={onRedo}
      >
        <Redo2 className="size-4" />
      </button>
      <span className="mx-1 h-5 w-px bg-[#d5dce1]" />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex h-8 items-center gap-1.5 rounded px-2 text-[13px] text-[#1c2127] hover:bg-[#f0f3f5]"
          >
            <LayoutGrid className="size-4 text-[#5f6b7c]" />
            레이아웃: <span className="font-semibold">기본</span>
            <ChevronDown className="size-3.5 text-[#5f6b7c]" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuLabel className="text-xs">레이아웃</DropdownMenuLabel>
          <DropdownMenuItem disabled className="text-xs">
            커스텀 레이아웃 · 준비 중
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <span className="mx-1 h-5 w-px bg-[#d5dce1]" />
      <span className="flex h-7 items-center rounded border border-[#accdea] bg-[#edf4fa] px-3 text-[13px] font-semibold text-[#215db0]">
        {resultCount}개 결과
      </span>
      <div className="pointer-events-none absolute inset-x-0 flex h-full items-stretch justify-center gap-5">
        {(
          [
            { id: "explore", label: "탐색", icon: BarChart3 },
            { id: "results", label: "결과", icon: Menu },
          ] as const
        ).map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => onViewTabChange(tab.id)}
            className={cn(
              "pointer-events-auto relative flex items-center gap-1.5 text-[14px]",
              viewTab === tab.id
                ? "font-semibold text-[#215db0]"
                : "font-medium text-[#1c2127] hover:text-[#215db0]",
            )}
          >
            <tab.icon className="size-4" />
            {tab.label}
            {viewTab === tab.id ? (
              <span className="absolute inset-x-0 bottom-0 h-[3px] rounded-t bg-[#2d72d2]" />
            ) : null}
          </button>
        ))}
      </div>
      <div className="relative z-10 ml-auto flex items-center gap-2">
        <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Radio
            className={cn(
              "size-3.5",
              isStreaming && "animate-pulse text-success",
            )}
          />
          실시간
          <Switch
            checked={isLive}
            onCheckedChange={onLiveChange}
            aria-label="실시간 구독"
          />
        </label>
        {liveSummary ? (
          <span className="font-mono text-[10px] text-muted-foreground">
            {liveSummary}
          </span>
        ) : null}
        <button
          type="button"
          aria-label="결과 새로고침"
          className="flex size-8 items-center justify-center rounded text-[#404854] hover:bg-[#f0f3f5]"
          onClick={onRefresh}
        >
          <RotateCw className="size-4" />
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <OutlineMenuButton label="액션" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="text-xs">
              객체 타입 액션
            </DropdownMenuLabel>
            {actions.length === 0 ? (
              <DropdownMenuItem disabled className="text-xs">
                이 객체 타입에 연결된 액션이 없습니다.
              </DropdownMenuItem>
            ) : (
              actions.map((action) => (
                <DropdownMenuItem
                  key={action.apiName}
                  disabled={!action.isEnabled}
                  className="text-xs"
                  onSelect={() => onRunAction(action)}
                >
                  <Zap className="size-3.5" />
                  {action.displayName}
                </DropdownMenuItem>
              ))
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <OutlineMenuButton label="다음에서 열기" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem disabled className="text-xs">
              Quiver에서 열기 · 준비 중
            </DropdownMenuItem>
            <DropdownMenuItem disabled className="text-xs">
              Workshop에서 열기 · 준비 중
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <OutlineMenuButton label="내보내기" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              disabled={!canExport}
              className="text-xs"
              onSelect={onExportCsv}
            >
              CSV 다운로드 (현재 페이지)
            </DropdownMenuItem>
            <DropdownMenuItem disabled className="text-xs">
              Excel 내보내기 · 준비 중
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
