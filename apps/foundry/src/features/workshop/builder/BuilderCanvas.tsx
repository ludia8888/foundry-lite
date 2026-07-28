import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { LayoutTemplate, Plus, X } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import {
  LAYOUT_TEMPLATES,
  WIDGET_LABELS,
  type AppPage,
  type AppSection,
  type AppWidget,
  type WidgetKind,
} from "../lib/app-model";
import { WidgetPalette } from "./WidgetPalette";

interface BuilderCanvasProps {
  page: AppPage;
  objectViews: FoundryLiteOntologyObjectView[];
  actionViews: FoundryLiteOntologyActionView[];
  selectedSectionId: string | null;
  selectedWidgetId: string | null;
  onSelectSection: (sectionId: string) => void;
  onSelectWidget: (sectionId: string, widgetId: string) => void;
  onAddWidget: (sectionId: string, kind: WidgetKind) => void;
  onRemoveWidget: (sectionId: string, widgetId: string) => void;
  onBindWidgetObject: (
    sectionId: string,
    widgetId: string,
    objectApiName: string,
  ) => void;
  onBindWidgetAction: (
    sectionId: string,
    widgetId: string,
    actionApiName: string,
  ) => void;
  onApplyLayout: (columns: number) => void;
}

/** 위젯 카드: 종류 배지 + 바인딩 셀렉트(객체 타입 / 액션) + 제거. */
function WidgetCard({
  widget,
  objectViews,
  actionViews,
  isSelected,
  onRemove,
  onSelect,
  onBindObject,
  onBindAction,
}: {
  widget: AppWidget;
  objectViews: FoundryLiteOntologyObjectView[];
  actionViews: FoundryLiteOntologyActionView[];
  isSelected: boolean;
  onRemove: () => void;
  onSelect: () => void;
  onBindObject: (objectApiName: string) => void;
  onBindAction: (actionApiName: string) => void;
}) {
  const needsObject =
    widget.kind === "objectTable" || widget.kind === "objectDetail";
  const needsAction = widget.kind === "actionForm";
  return (
    <div
      className={cn(
        "rounded border bg-[#f7f9fc] p-2 text-left shadow-[0_1px_1px_rgba(17,20,24,0.04)]",
        isSelected
          ? "border-[#2d72d2] ring-1 ring-[#2d72d2]"
          : "border-[#c9d3ea]",
      )}
    >
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          aria-label={`${WIDGET_LABELS[widget.kind]} 위젯 선택`}
          onClick={(event) => {
            event.stopPropagation();
            onSelect();
          }}
          className="rounded bg-[#2d72d2] px-1.5 py-px text-[10px] font-semibold text-white outline-none focus-visible:ring-2 focus-visible:ring-[#2d72d2]/40"
        >
          {WIDGET_LABELS[widget.kind]}
        </button>
        <button
          type="button"
          aria-label="위젯 제거"
          className="ml-auto rounded p-0.5 text-[#8f99a8] hover:bg-muted/60 hover:text-foreground"
          onClick={(event) => {
            event.stopPropagation();
            onRemove();
          }}
        >
          <X className="size-3" />
        </button>
      </div>
      {needsObject ? (
        <select
          value={widget.config.objectApiName ?? ""}
          onChange={(event) => onBindObject(event.target.value)}
          className="mt-1.5 h-7 w-full rounded border border-[#d5dce1] bg-white px-1.5 text-[11px] text-[#1c2127] focus:border-[#2d72d2] focus:outline-none"
        >
          <option value="" disabled>
            객체 타입 선택
          </option>
          {objectViews.map((view) => (
            <option key={view.apiName} value={view.apiName}>
              {view.displayName} ({view.apiName})
            </option>
          ))}
        </select>
      ) : null}
      {needsAction ? (
        <select
          value={widget.config.actionApiName ?? ""}
          onChange={(event) => onBindAction(event.target.value)}
          className="mt-1.5 h-7 w-full rounded border border-[#d5dce1] bg-white px-1.5 text-[11px] text-[#1c2127] focus:border-[#2d72d2] focus:outline-none"
        >
          <option value="" disabled>
            액션 선택
          </option>
          {actionViews.map((view) => (
            <option key={view.apiName} value={view.apiName}>
              {view.displayName} ({view.apiName})
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}

function LayoutTemplatePopover({
  onApply,
}: {
  onApply: (columns: number) => void;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex h-8 w-full items-center justify-center gap-1.5 rounded border border-[#d5dce1] bg-white text-[12px] font-medium text-[#404854] hover:bg-muted/40"
        >
          <LayoutTemplate className="size-3.5" />
          레이아웃 설정
        </button>
      </PopoverTrigger>
      <PopoverContent align="center" className="w-64 p-2">
        <div className="px-1 pb-1.5">
          <div className="text-[12px] font-semibold text-[#1c2127]">
            레이아웃 템플릿을 사용하세요
          </div>
          <div className="text-[11px] text-muted-foreground">
            호버로 레이아웃을 미리 봅니다
          </div>
        </div>
        <div className="grid grid-cols-5 gap-1">
          {LAYOUT_TEMPLATES.map((template) => (
            <button
              key={template.id}
              type="button"
              onClick={() => onApply(template.columns)}
              className="group flex flex-col items-center gap-1 rounded p-1.5 hover:bg-[#e8f0fb]"
            >
              <span className="flex h-7 w-full items-center justify-center gap-px rounded border border-[#c1c8cf] bg-white p-0.5">
                {Array.from({ length: template.columns }).map((_, index) => (
                  <span
                    key={index}
                    className="h-full flex-1 rounded-[1px] bg-[#c9d3ea]"
                  />
                ))}
              </span>
              <span className="text-[10px] text-[#5f6b7c]">
                {template.label}
              </span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function SectionColumn({
  section,
  index,
  isSelected,
  objectViews,
  actionViews,
  selectedWidgetId,
  onSelect,
  onSelectWidget,
  onAddWidget,
  onRemoveWidget,
  onBindObject,
  onBindAction,
  onApplyLayout,
}: {
  section: AppSection;
  index: number;
  isSelected: boolean;
  objectViews: FoundryLiteOntologyObjectView[];
  actionViews: FoundryLiteOntologyActionView[];
  selectedWidgetId: string | null;
  onSelect: () => void;
  onSelectWidget: (widgetId: string) => void;
  onAddWidget: (kind: WidgetKind) => void;
  onRemoveWidget: (widgetId: string) => void;
  onBindObject: (widgetId: string, objectApiName: string) => void;
  onBindAction: (widgetId: string, actionApiName: string) => void;
  onApplyLayout: (columns: number) => void;
}) {
  return (
    <div
      onClick={onSelect}
      className={cn(
        "flex min-h-[360px] flex-1 cursor-pointer flex-col rounded bg-white p-3",
        isSelected ? "border-2 border-[#2d72d2]" : "border border-[#dde3e9]",
      )}
    >
      <div className="mb-2 text-[12px] font-medium text-[#5f6b7c]">
        Section {index + 1}
      </div>

      <div className="flex-1 space-y-2">
        {section.widgets.map((widget) => (
          <WidgetCard
            key={widget.id}
            widget={widget}
            objectViews={objectViews}
            actionViews={actionViews}
            isSelected={selectedWidgetId === widget.id}
            onRemove={() => onRemoveWidget(widget.id)}
            onSelect={() => onSelectWidget(widget.id)}
            onBindObject={(objectApiName) =>
              onBindObject(widget.id, objectApiName)
            }
            onBindAction={(actionApiName) =>
              onBindAction(widget.id, actionApiName)
            }
          />
        ))}
      </div>

      <div
        className="mt-2 space-y-1.5"
        onClick={(event) => event.stopPropagation()}
      >
        <WidgetPalette
          onPick={onAddWidget}
          trigger={
            <button
              type="button"
              className="flex h-8 w-full items-center justify-center gap-1.5 rounded border-2 border-dashed border-[#2d72d2] bg-[#f2f7fd] text-[12px] font-medium text-[#215db0] hover:bg-[#e8f0fb]"
            >
              <Plus className="size-3.5" />
              위젯 추가
            </button>
          }
        />
        <LayoutTemplatePopover onApply={onApplyLayout} />
      </div>
    </div>
  );
}

/** 중앙 캔버스: 섹션 컬럼 배치 + 위젯 카드 + '+ 위젯 추가' 파란 아웃라인 + 레이아웃 팝오버. */
export function BuilderCanvas({
  page,
  objectViews,
  actionViews,
  selectedSectionId,
  selectedWidgetId,
  onSelectSection,
  onSelectWidget,
  onAddWidget,
  onRemoveWidget,
  onBindWidgetObject,
  onBindWidgetAction,
  onApplyLayout,
}: BuilderCanvasProps) {
  return (
    <div
      className="h-full min-h-0 overflow-auto p-4"
      style={{
        backgroundColor:
          page.backgroundColor === "transparent"
            ? "#f6f8fa"
            : page.backgroundColor,
      }}
    >
      <div
        className={cn(
          "flex gap-3",
          page.layoutDirection === "rows" ? "flex-col" : "flex-row",
        )}
      >
        {page.sections.map((section, index) => (
          <SectionColumn
            key={section.id}
            section={section}
            index={index}
            isSelected={selectedSectionId === section.id}
            objectViews={objectViews}
            actionViews={actionViews}
            selectedWidgetId={selectedWidgetId}
            onSelect={() => onSelectSection(section.id)}
            onSelectWidget={(widgetId) =>
              onSelectWidget(section.id, widgetId)
            }
            onAddWidget={(kind) => onAddWidget(section.id, kind)}
            onRemoveWidget={(widgetId) => onRemoveWidget(section.id, widgetId)}
            onBindObject={(widgetId, objectApiName) =>
              onBindWidgetObject(section.id, widgetId, objectApiName)
            }
            onBindAction={(widgetId, actionApiName) =>
              onBindWidgetAction(section.id, widgetId, actionApiName)
            }
            onApplyLayout={onApplyLayout}
          />
        ))}
      </div>
    </div>
  );
}
