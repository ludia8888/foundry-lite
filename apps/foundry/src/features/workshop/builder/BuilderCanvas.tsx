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
  type AppPresentation,
  type AppSection,
  type AppWidget,
  type WidgetKind,
} from "../lib/app-model";
import { businessActionName, businessObjectTypeName } from "../lib/business-display";
import { WidgetPalette } from "./WidgetPalette";

interface BuilderCanvasProps {
  page: AppPage;
  presentation: AppPresentation;
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

/** 사용자 화면 카드: 보이는 정보와 실행 업무를 비개발자 언어로 검토한다. */
function WidgetCard({
  widget,
  objectViews,
  actionViews,
  presentation,
  isSelected,
  onRemove,
  onSelect,
  onBindObject,
  onBindAction,
}: {
  widget: AppWidget;
  objectViews: FoundryLiteOntologyObjectView[];
  actionViews: FoundryLiteOntologyActionView[];
  presentation: AppPresentation;
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
        "rounded-xl bg-[#f7f9fb] p-3 text-left",
        isSelected
          ? "border-2 border-[#6651c7] ring-2 ring-[#6651c7]/10"
          : "border border-[#e1e6eb]",
      )}
    >
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          aria-label={`${WIDGET_LABELS[widget.kind]} 설정 열기`}
          onClick={(event) => {
            event.stopPropagation();
            onSelect();
          }}
          className="rounded-lg bg-white px-2 py-1 text-[11px] font-bold text-[#475569] shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-[#6651c7]/30"
        >
          {WIDGET_LABELS[widget.kind]}
        </button>
        <button
          type="button"
          aria-label="화면 요소 제거"
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
          className="mt-2 h-9 w-full rounded-lg border border-[#d5dce1] bg-white px-2 text-[12px] text-[#1c2127] focus:border-[#6651c7] focus:outline-none"
        >
          <option value="" disabled>
            보여줄 업무 선택
          </option>
          {objectViews.filter((view) => !view.displayName.startsWith("[LOG]")).map((view) => (
            <option key={view.apiName} value={view.apiName}>
              {businessObjectTypeName(view.apiName, view, presentation)}
            </option>
          ))}
        </select>
      ) : null}
      {needsAction ? (
        <select
          value={widget.config.actionApiName ?? ""}
          onChange={(event) => onBindAction(event.target.value)}
          className="mt-2 h-9 w-full rounded-lg border border-[#d5dce1] bg-white px-2 text-[12px] text-[#1c2127] focus:border-[#6651c7] focus:outline-none"
        >
          <option value="" disabled>
            실행할 업무 선택
          </option>
          {actionViews.map((view) => (
            <option key={view.apiName} value={view.apiName}>
              {businessActionName(view.apiName, view, presentation)}
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
          화면 배치 바꾸기
        </button>
      </PopoverTrigger>
      <PopoverContent align="center" className="w-64 p-2">
        <div className="px-1 pb-1.5">
          <div className="text-[12px] font-semibold text-[#1c2127]">
            정보 배치를 선택하세요
          </div>
          <div className="text-[11px] text-muted-foreground">
            업무량과 화면 크기에 맞는 구성을 선택합니다.
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
  presentation,
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
  presentation: AppPresentation;
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
        "flex min-h-[260px] cursor-pointer flex-col rounded-2xl bg-white p-4 shadow-[0_18px_44px_-36px_rgba(15,23,42,.7)]",
        sectionSpanClass(section.span),
        isSelected ? "border-2 border-[#6651c7]" : "border border-[#e1e6eb]",
      )}
    >
      <div className="mb-3 flex items-center gap-2 text-[12px] font-bold text-[#475569]">
        <span className="flex size-6 items-center justify-center rounded-lg bg-[#eeeafd] text-[10px] text-[#6651c7]">{index + 1}</span>
        {section.title || `화면 영역 ${index + 1}`}
      </div>

      <div className="flex-1 space-y-2">
        {section.widgets.map((widget) => (
          <WidgetCard
            key={widget.id}
            widget={widget}
            objectViews={objectViews}
            actionViews={actionViews}
            presentation={presentation}
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
              className="flex h-9 w-full items-center justify-center gap-1.5 rounded-xl border border-dashed border-[#9b8be1] bg-[#f7f5ff] text-[12px] font-bold text-[#6651c7] hover:bg-[#eeeafd]"
            >
              <Plus className="size-3.5" />
              보여줄 정보 추가
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
  presentation,
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
      <div className="grid gap-3 lg:grid-cols-12">
        {page.sections.map((section, index) => (
          <SectionColumn
            key={section.id}
            section={section}
            index={index}
            isSelected={selectedSectionId === section.id}
            objectViews={objectViews}
        actionViews={actionViews}
        presentation={presentation}
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

function sectionSpanClass(span: AppSection["span"]): string {
  return {
    3: "lg:col-span-3",
    4: "lg:col-span-4",
    6: "lg:col-span-6",
    8: "lg:col-span-8",
    9: "lg:col-span-9",
    12: "lg:col-span-12",
  }[span];
}
