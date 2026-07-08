import {
  ArrowLeft,
  CircleCheck,
  Cog,
  Database,
  Layers,
  LineChart,
  ListChecks,
  type LucideIcon,
  MonitorSmartphone,
  PanelsTopLeft,
  Puzzle,
  Shield,
  Zap,
} from "lucide-react";

import { cn } from "@/lib/utils";

import { ResourceKindIcon } from "./ResourceTable";

/** 객체 타입 상세의 좌측 섹션 id. */
export type ObjectTypeSection =
  | "overview"
  | "properties"
  | "security"
  | "datasources"
  | "capabilities"
  | "objectViews"
  | "interfaces"
  | "automations"
  | "usage"
  | "advanced";

type SectionDef = {
  id: ObjectTypeSection;
  label: string;
  icon: LucideIcon;
  /** 백엔드 미지원 섹션은 future 배지로 강등한다. */
  isFuture?: boolean;
};

const SECTIONS: SectionDef[] = [
  { id: "overview", label: "개요", icon: MonitorSmartphone },
  { id: "properties", label: "속성", icon: ListChecks },
  { id: "security", label: "보안", icon: Shield, isFuture: true },
  { id: "datasources", label: "데이터소스", icon: Database },
  { id: "capabilities", label: "기능", icon: Layers, isFuture: true },
  { id: "objectViews", label: "객체 뷰", icon: PanelsTopLeft, isFuture: true },
  { id: "interfaces", label: "인터페이스", icon: Puzzle },
  { id: "automations", label: "자동화", icon: Zap, isFuture: true },
  { id: "usage", label: "사용량", icon: LineChart },
  { id: "advanced", label: "고급", icon: Cog },
];

interface ObjectTypeSectionNavProps {
  displayName: string;
  objectCountLabel: string;
  section: ObjectTypeSection;
  counts: Partial<Record<ObjectTypeSection, number>>;
  onSelectSection: (section: ObjectTypeSection) => void;
  onBack: () => void;
}

/**
 * 객체 타입 상세 좌측 네비 (272px 고정): 뒤로가기 + 컬러 아이콘 헤더 +
 * 섹션 리스트. 선택 행은 배경 #F3F8FF + 텍스트/아이콘 파랑(#185FA5).
 */
export function ObjectTypeSectionNav({
  displayName,
  objectCountLabel,
  section,
  counts,
  onSelectSection,
  onBack,
}: ObjectTypeSectionNavProps) {
  return (
    <aside className="w-[200px] shrink-0 overflow-hidden rounded border bg-card">
      <button
        type="button"
        onClick={onBack}
        className="flex h-9 w-full items-center gap-1.5 px-3 text-left text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        객체 타입
      </button>
      <div className="flex items-center gap-2 px-3 pb-3">
        <ResourceKindIcon kind="objectType" className="size-11 rounded-[4px]" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[15px] font-bold leading-tight">
            {displayName}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {objectCountLabel}
          </div>
        </div>
        <CircleCheck className="size-4 shrink-0 fill-[#38895b] text-white" />
      </div>
      <nav className="p-1.5">
        {SECTIONS.map((item) => {
          const Icon = item.icon;
          const isSelected = item.id === section;
          const count = counts[item.id];
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelectSection(item.id)}
              className={cn(
                "flex h-9 w-full items-center gap-2 rounded px-2 text-left",
                isSelected
                  ? item.id === "advanced"
                    ? "bg-[#f5f9fe] text-[#185fa5]"
                    : "bg-[#f3f8ff] text-[#185fa5]"
                  : "text-foreground/80 hover:bg-muted/50",
              )}
            >
              <Icon className="size-3.5 shrink-0" />
              <span className="min-w-0 flex-1 truncate text-xs">
                {item.label}
              </span>
              {item.isFuture ? (
                <span className="rounded bg-[#eff0f2] px-1 py-0.5 text-[9px] font-medium tracking-wide text-muted-foreground uppercase">
                  future
                </span>
              ) : typeof count === "number" ? (
                <span className="rounded bg-[#eff0f2] px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                  {count}
                </span>
              ) : null}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
