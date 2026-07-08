import type { LucideIcon } from "lucide-react";
import {
  Activity,
  ArrowLeftRight,
  Box,
  FunctionSquare,
  Globe,
  History,
  Layers,
  ListTree,
  Monitor,
  Search,
  Settings,
  Sparkles,
  Zap,
} from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import type {
  OntologySearchMatchCounts,
  OntologySection,
} from "../lib/ontology-view";

export type ResourceNavCounts = {
  objectTypes: number;
  properties: number;
  linkTypes: number;
  actionTypes: number;
  interfaces: number;
  functionTypes: number;
};

interface ResourceNavProps {
  section: OntologySection;
  counts: ResourceNavCounts;
  /** 검색 중이면 카테고리별 카운트가 매치 수로 바뀌고 검색 결과 행이 나타난다. */
  matchCounts?: OntologySearchMatchCounts | null;
  onSelectSection: (section: OntologySection) => void;
}

type NavEntry = {
  section: OntologySection;
  label: string;
  icon: LucideIcon;
  count: number | null;
};

function NavCountBadge({
  isSelected,
  children,
  testId,
}: {
  isSelected: boolean;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <span
      data-testid={testId}
      className={cn(
        "min-w-7 rounded px-1.5 py-0.5 text-center font-mono text-[11px]",
        isSelected
          ? "bg-[#e8ecfa] text-[#325caa]"
          : "bg-[#eff0f2] text-foreground/80",
      )}
    >
      {children}
    </span>
  );
}

/** 클릭 불가한 패리티 행 (상태 이슈/정리/히스토리/고급 등). */
function StaticNavRow({
  icon: Icon,
  label,
  count,
}: {
  icon: LucideIcon;
  label: string;
  count: string | null;
}) {
  return (
    <div className="flex h-10 w-full items-center gap-2.5 px-3 text-left text-[13px] text-foreground">
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {count !== null ? (
        <NavCountBadge isSelected={false}>{count}</NavCountBadge>
      ) : null}
    </div>
  );
}

/** 좌측 흰 카드 네비: 그룹 구분선 + 행(아이콘+라벨+우측 카운트), 선택 행 연파랑 아웃라인. */
export function ResourceNav({
  section,
  counts,
  matchCounts = null,
  onSelectSection,
}: ResourceNavProps) {
  const isSearching = matchCounts !== null;
  const resourceEntries: NavEntry[] = [
    {
      section: "objectTypes",
      label: "객체 타입",
      icon: Box,
      count: counts.objectTypes,
    },
    {
      section: "linkTypes",
      label: "링크 타입",
      icon: ArrowLeftRight,
      count: counts.linkTypes,
    },
    {
      section: "actionTypes",
      label: "액션 타입",
      icon: Zap,
      count: counts.actionTypes,
    },
    {
      section: "interfaces",
      label: "인터페이스",
      icon: Layers,
      count: counts.interfaces,
    },
    {
      section: "functionTypes",
      label: "함수",
      icon: FunctionSquare,
      count: counts.functionTypes,
    },
  ];
  const totalMatches = isSearching
    ? Object.values(matchCounts).reduce((sum, count) => sum + count, 0)
    : 0;

  const renderEntry = (entry: NavEntry) => {
    const isSelected = entry.section === section;
    const matchCount =
      isSearching && entry.section !== "overview"
        ? matchCounts[entry.section as Exclude<OntologySection, "overview">]
        : null;
    return (
      <div key={entry.section}>
        <button
          type="button"
          onClick={() => onSelectSection(entry.section)}
          className={cn(
            "flex h-10 w-full items-center gap-2.5 px-3 text-left text-[13px]",
            isSelected
              ? "rounded-[3px] bg-[#fafbff] font-medium text-[#325caa] ring-1 ring-[#4c90f0] ring-inset"
              : "text-foreground hover:bg-muted/60",
          )}
        >
          <entry.icon
            className={cn(
              "size-4 shrink-0",
              isSelected ? "text-[#325caa]" : "text-muted-foreground",
            )}
          />
          <span className="min-w-0 flex-1 truncate">{entry.label}</span>
          {entry.count !== null ? (
            <NavCountBadge
              isSelected={isSelected}
              testId={
                matchCount !== null
                  ? `ontology-nav-match-${entry.section}`
                  : undefined
              }
            >
              {matchCount ?? entry.count}
            </NavCountBadge>
          ) : null}
        </button>
        {entry.section === "objectTypes" && isSelected ? (
          <div className="flex h-10 items-center gap-2.5 pr-3 pl-5 text-[13px] text-foreground">
            <span
              aria-hidden
              className="size-2.5 shrink-0 rounded-bl border-b border-l border-border"
            />
            <ListTree className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate">속성</span>
            <NavCountBadge isSelected={false}>
              {counts.properties}
            </NavCountBadge>
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <nav className="overflow-hidden rounded border bg-card">
      <div className="py-0.5">
        {renderEntry({
          section: "overview",
          label: "개요",
          icon: Monitor,
          count: null,
        })}
      </div>
      {isSearching ? (
        <div className="border-t py-0.5">
          <div className="flex h-10 w-full items-center gap-2.5 rounded-[3px] bg-[#fafbff] px-3 text-left text-[13px] font-medium text-[#325caa] ring-1 ring-[#4c90f0] ring-inset">
            <Search className="size-4 shrink-0 text-[#325caa]" />
            <span className="min-w-0 flex-1 truncate">검색 결과</span>
            <NavCountBadge isSelected testId="ontology-nav-match-total">
              {totalMatches}
            </NavCountBadge>
          </div>
        </div>
      ) : null}
      <div className="border-t py-0.5">
        {resourceEntries.slice(0, 3).map(renderEntry)}
        <StaticNavRow icon={Globe} label="공유 속성" count="0" />
        {resourceEntries.slice(3).map(renderEntry)}
      </div>
      <div className="border-t py-0.5">
        <StaticNavRow icon={Activity} label="상태 이슈" count="0" />
        <StaticNavRow icon={Sparkles} label="정리" count={null} />
      </div>
      <div className="border-t py-0.5">
        <StaticNavRow icon={History} label="히스토리" count={null} />
      </div>
      <div className="border-t py-0.5">
        <StaticNavRow icon={Settings} label="고급" count={null} />
      </div>
    </nav>
  );
}
