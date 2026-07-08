import {
  ChevronDown,
  Eye,
  GitBranch,
  GitPullRequestArrow,
  Hammer,
  Loader2,
  Star,
} from "lucide-react";
import { useState } from "react";

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

/** 공식 탭(code-view.png): Code | Branches | Pull requests | Checks | Settings. Code만 구현. */
const DOCUMENT_TABS = [
  { id: "code", label: "Code", implemented: true },
  { id: "branches", label: "Branches", implemented: false },
  { id: "pull-requests", label: "Pull requests", implemented: false },
  { id: "checks", label: "Checks", implemented: false },
  { id: "settings", label: "Settings", implemented: false },
] as const;

/** 공식 상단 바의 File/Edit/View future 메뉴. */
const FUTURE_MENUS = ["파일", "편집", "보기"] as const;

const BRANCHES = ["master"] as const;

interface CodeToolbarProps {
  repositoryName: string;
  isBuilding: boolean;
  canBuild: boolean;
  onBuild: () => void;
}

/**
 * 공식 Code Repositories 상단 바(code-view.png) 2행 구조:
 * 1행 = [앱 아이콘] [Code Repositories › 저장소 › master(볼드) ☆] · [Code|Checks|Settings] ·
 *       우측 [미리보기(future)] [빌드(그린)] [변경 제안(future)]
 * 2행 = [파일/편집/보기 future 메뉴] [브랜치 ▾] [SQL 배지]
 */
export function CodeToolbar({
  repositoryName,
  isBuilding,
  canBuild,
  onBuild,
}: CodeToolbarProps) {
  const [isStarred, setIsStarred] = useState(false);
  const [activeTab, setActiveTab] = useState<string>("code");

  return (
    <div className="flex h-12 shrink-0 items-stretch border-b bg-card">
      {/* 앱 아이콘: 연민트 배경 + 틸 코드 글리프 */}
      <div className="flex w-12 shrink-0 items-center justify-center bg-[#EAF5F4]">
        <CodeGlyph />
      </div>

      {/* 좌측 2행: 브레드크럼 / future 메뉴 + 브랜치 + SQL 배지 */}
      <div className="flex min-w-0 flex-col justify-center gap-0.5 pr-2 pl-3">
        <div className="flex min-w-0 items-center gap-1 leading-none">
          <span className="hidden truncate text-[12px] text-muted-foreground lg:inline">
            Code Repositories
          </span>
          <BreadcrumbCaret />
          <span className="hidden max-w-40 truncate text-[12px] text-muted-foreground lg:inline">
            {repositoryName}
          </span>
          <BreadcrumbCaret />
          <span className="max-w-44 truncate text-[13px] font-bold">
            master
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
          <Select value="master">
            <SelectTrigger
              size="sm"
              className="h-5 gap-1 border-none px-1 text-[11px] text-muted-foreground shadow-none"
            >
              <GitBranch className="size-3 shrink-0" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {BRANCHES.map((branch) => (
                <SelectItem key={branch} value={branch} className="text-[12px]">
                  {branch}
                </SelectItem>
              ))}
              <div className="border-t px-2 py-1.5 text-[10px] text-muted-foreground">
                브랜치 생성은 곧 제공 예정
              </div>
            </SelectContent>
          </Select>
          <span className="rounded-[2px] bg-[#404854] px-1.5 py-0.5 text-[10px] leading-none font-semibold text-white">
            SQL
          </span>
        </div>
      </div>

      {/* 문서 탭: Code | Checks | Settings (활성 = 파란 밑줄, 미구현 = future) */}
      <div className="ml-4 flex items-stretch gap-1">
        {DOCUMENT_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            disabled={!tab.implemented}
            title={tab.implemented ? undefined : "곧 제공 예정"}
            className={cn(
              "relative flex items-center gap-1 border-b-2 px-2.5 text-[13px] transition-colors",
              activeTab === tab.id && tab.implemented
                ? "border-primary font-semibold text-primary"
                : "border-transparent text-muted-foreground",
              tab.implemented
                ? "hover:text-foreground"
                : "cursor-not-allowed opacity-60",
            )}
            onClick={() => tab.implemented && setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="ml-auto flex items-center gap-1.5 pr-3">
        <button
          type="button"
          disabled
          title="미리보기 diff · 곧 제공 예정"
          className="flex h-7 cursor-not-allowed items-center gap-1 rounded border px-2 text-[12px] text-muted-foreground/80"
        >
          <Eye className="size-3.5" />
          미리보기
        </button>

        {/* Build: 공식 상단 바의 그린 Build 버튼 (register→run 실행) */}
        <Button
          size="sm"
          className="h-7 bg-success px-2.5 text-[12px] text-success-foreground hover:bg-success/90 disabled:opacity-70"
          disabled={!canBuild || isBuilding}
          title={
            canBuild
              ? "SQL transform 등록 후 실행"
              : "SQL과 출력 데이터셋을 입력하세요"
          }
          onClick={onBuild}
        >
          {isBuilding ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Hammer className="size-3.5" />
          )}
          {isBuilding ? "빌드 중..." : "빌드"}
        </Button>

        <button
          type="button"
          disabled
          title="변경 제안 · 곧 제공 예정"
          className="flex h-7 cursor-not-allowed items-center gap-1 px-1.5 text-[12px] text-muted-foreground/80"
        >
          <GitPullRequestArrow className="size-3.5" />
          변경 제안
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

/** 공식 앱 아이콘 계열: 틸 코드 브라켓 글리프. */
function CodeGlyph() {
  return (
    <svg viewBox="0 0 20 20" className="size-5" aria-hidden="true">
      <g
        stroke="#00847A"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M7 6l-4 4 4 4" />
        <path d="M13 6l4 4-4 4" />
      </g>
    </svg>
  );
}
