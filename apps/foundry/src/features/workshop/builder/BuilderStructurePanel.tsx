import {
  Boxes,
  FileText,
  FunctionSquare,
  History,
  Plus,
  Zap,
} from "lucide-react";

import { cn } from "@/lib/utils";

import {
  widgetCounts,
  WIDGET_LABELS,
  type AppDefinition,
} from "../lib/app-model";

interface BuilderStructurePanelProps {
  definition: AppDefinition;
  objectTypeCount: number;
  selectedSectionId: string | null;
  onSelectSection: (sectionId: string) => void;
}

function CapabilityRow({
  icon,
  label,
  count,
}: {
  icon: React.ReactNode;
  label: string;
  count: number;
}) {
  return (
    <div className="flex items-center gap-2 py-1">
      <span className="text-[#5f6b7c]">{icon}</span>
      <span className="flex-1 text-[12px] text-[#1c2127]">{label}</span>
      <span className="font-mono text-[12px] text-muted-foreground tabular-nums">
        {count}
      </span>
    </div>
  );
}

/** 좌측 앱 구조 패널: Overview + 객체 타입 + 위젯 트리 + Variables + Capabilities + History. */
export function BuilderStructurePanel({
  definition,
  objectTypeCount,
  selectedSectionId,
  onSelectSection,
}: BuilderStructurePanelProps) {
  const counts = widgetCounts(definition.page);
  const variables = definition.page.sections
    .flatMap((section) => section.widgets)
    .filter((widget) => widget.objectApiName)
    .map((widget) => widget.objectApiName as string);
  const uniqueVariables = Array.from(new Set(variables));

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="space-y-1 border-b border-[#e4e9ed] px-3 py-3">
        <div className="text-[13px] font-semibold text-[#1c2127]">개요</div>
        <div className="text-[11px] text-muted-foreground">
          {definition.purpose}
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto px-3 py-3">
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <Boxes className="size-3.5 text-[#5f6b7c]" />
              <span className="text-[12px] font-medium text-[#1c2127]">
                객체 타입
              </span>
            </div>
            <span className="font-mono text-[12px] text-muted-foreground tabular-nums">
              {objectTypeCount}
            </span>
          </div>
          <button
            type="button"
            className="flex w-full items-center justify-center gap-1.5 rounded border border-dashed border-[#c1c8cf] py-1.5 text-[11px] text-[#5f6b7c] hover:bg-muted/40"
          >
            <Plus className="size-3" />
            객체 세트 변수 추가
          </button>
        </div>

        <div className="space-y-1.5 border-t border-[#e4e9ed] pt-3">
          <div className="flex items-center gap-1.5">
            <FileText className="size-3.5 text-[#5f6b7c]" />
            <span className="text-[12px] font-medium text-[#1c2127]">
              {definition.page.name}
            </span>
          </div>
          <div className="space-y-0.5 pl-1.5">
            {definition.page.sections.map((section, index) => (
              <div key={section.id} className="space-y-0.5">
                <button
                  type="button"
                  onClick={() => onSelectSection(section.id)}
                  className={cn(
                    "flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[12px]",
                    selectedSectionId === section.id
                      ? "bg-[#e8f0fb] font-medium text-[#215db0]"
                      : "text-[#404854] hover:bg-muted/50",
                  )}
                >
                  <span className="text-[10px] text-[#8f99a8]">▸</span>
                  {section.title} {index + 1}
                </button>
                {section.widgets.map((widget) => (
                  <div
                    key={widget.id}
                    className="ml-5 flex items-center gap-1.5 py-0.5 text-[11px] text-[#5f6b7c]"
                  >
                    <span className="size-1.5 rounded-full bg-[#2d72d2]" />
                    {WIDGET_LABELS[widget.kind]}
                    {widget.objectApiName ? (
                      <span className="font-mono text-[10px] text-[#8f99a8]">
                        · {widget.objectApiName}
                      </span>
                    ) : null}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {uniqueVariables.length > 0 ? (
          <div className="space-y-1.5 border-t border-[#e4e9ed] pt-3">
            <div className="text-[11px] font-semibold tracking-wide text-[#5f6b7c] uppercase">
              Variables ({uniqueVariables.length})
            </div>
            <div className="flex flex-wrap gap-1">
              {uniqueVariables.map((name) => (
                <span
                  key={name}
                  className="rounded bg-[#eef1f4] px-1.5 py-0.5 font-mono text-[10px] text-[#404854]"
                >
                  {name} 객체 세트
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <div className="space-y-1 border-t border-[#e4e9ed] pt-3">
          <div className="text-[11px] font-semibold tracking-wide text-[#5f6b7c] uppercase">
            Capabilities
          </div>
          <CapabilityRow
            icon={<Zap className="size-3.5" />}
            label="Actions"
            count={counts.actionForm}
          />
          <CapabilityRow
            icon={<FunctionSquare className="size-3.5" />}
            label="Functions"
            count={0}
          />
        </div>
      </div>

      <div className="flex items-center gap-1.5 border-t border-[#e4e9ed] px-3 py-2.5">
        <History className="size-3.5 text-[#5f6b7c]" />
        <div className="min-w-0">
          <div className="text-[11px] font-medium text-[#1c2127]">History</div>
          <div className="truncate text-[10px] text-muted-foreground">
            {definition.savedAt
              ? `v${definition.version} · ${new Date(definition.savedAt).toLocaleString("ko-KR")}`
              : "저장 전 (로컬 초안)"}
          </div>
        </div>
      </div>
    </div>
  );
}
