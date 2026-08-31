import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { CheckCircle2, MonitorSmartphone, Sparkles, UsersRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  createSection,
  createWidget,
  type AppDefinition,
  type AppPage,
  type WidgetKind,
} from "../lib/app-model";
import {
  addWidget,
  removeWidget,
  replaceContainerSections,
  setWidgetConfig,
} from "../lib/app-edit";
import { buildWidgetSuggestion } from "../lib/ontology-context";
import { templateById } from "../lib/templates";
import { widgetDefinition } from "../lib/widget-catalog";
import { BuilderCanvas } from "./BuilderCanvas";
import { InspectorPanel } from "./InspectorPanel";
import {
  findOverlay,
  findPage,
  type BuilderSelection,
} from "./selection";
import { StructurePanel } from "./StructurePanel";
import { TemplateGallery } from "./TemplateGallery";

interface BuilderModeProps {
  definition: AppDefinition;
  objectViews: FoundryLiteOntologyObjectView[];
  actionViews: FoundryLiteOntologyActionView[];
  selectedSectionId: string | null;
  onSelectSection: (sectionId: string) => void;
  onChange: (definition: AppDefinition) => void;
}

function canvasPage(
  definition: AppDefinition,
  activeContainerId: string,
): AppPage {
  const page = findPage(definition, activeContainerId);
  if (page) return page;
  const overlay = findOverlay(definition, activeContainerId);
  if (!overlay) return definition.page;
  return {
    id: overlay.id,
    name: overlay.name,
    pageId: overlay.id,
    isDefault: false,
    backgroundColor: "transparent",
    layoutDirection: "columns",
    intent: "workbench",
    sections: overlay.sections,
  };
}

function resizedSections(page: AppPage, count: number) {
  if (count >= page.sections.length) {
    const additions = Array.from({
      length: count - page.sections.length,
    }).map(() => createSection("업무 영역"));
    return [...page.sections, ...additions];
  }
  const kept = page.sections.slice(0, count);
  const overflow = page.sections
    .slice(count)
    .flatMap((section) => section.widgets);
  return kept.map((section, index) =>
    index === kept.length - 1
      ? { ...section, widgets: [...section.widgets, ...overflow] }
      : section,
  );
}

/** Full Workshop builder: hierarchy, multimode canvas, templates, and typed inspector. */
export function BuilderMode({
  definition,
  objectViews,
  actionViews,
  selectedSectionId,
  onSelectSection,
  onChange,
}: BuilderModeProps) {
  const [activeContainerId, setActiveContainerId] = useState(
    definition.page.id,
  );
  const [selection, setSelection] = useState<BuilderSelection>(
    { type: "page", pageId: definition.page.id },
  );
  const [isTemplateGalleryOpen, setIsTemplateGalleryOpen] = useState(false);
  const page = useMemo(
    () => canvasPage(definition, activeContainerId),
    [activeContainerId, definition],
  );

  useEffect(() => {
    const exists =
      definition.pages.some((candidate) => candidate.id === activeContainerId) ||
      definition.overlays.some(
        (candidate) => candidate.id === activeContainerId,
      );
    if (!exists) {
      setActiveContainerId(definition.page.id);
      setSelection({ type: "page", pageId: definition.page.id });
    }
  }, [activeContainerId, definition]);

  const select = (next: BuilderSelection) => {
    setSelection(next);
    if (next?.type === "section") onSelectSection(next.sectionId);
    if (next?.type === "widget") onSelectSection(next.sectionId);
  };

  const handleAddWidget = (sectionId: string, kind: WidgetKind) => {
    const suggestion = buildWidgetSuggestion(objectViews, actionViews);
    const widget = createWidget(
      kind,
      widgetDefinition(kind).defaultConfig(suggestion),
    );
    onChange(addWidget(definition, sectionId, widget));
    select({ type: "widget", sectionId, widgetId: widget.id });
  };

  const handleRemoveWidget = (widgetId: string) => {
    onChange(removeWidget(definition, widgetId));
    if (selection?.type === "widget" && selection.widgetId === widgetId) {
      select(null);
    }
  };

  const handleBindObject = (widgetId: string, objectApiName: string) => {
    onChange(setWidgetConfig(definition, widgetId, { objectApiName }));
  };

  const handleBindAction = (widgetId: string, actionApiName: string) => {
    const targetObjectApiName =
      actionViews.find((view) => view.apiName === actionApiName)
        ?.targetObjectApiName ?? null;
    onChange(
      setWidgetConfig(definition, widgetId, {
        actionApiName,
        objectApiName: targetObjectApiName,
      }),
    );
  };

  const handleApplyLayout = (count: number) => {
    if (count === page.sections.length) return;
    onChange(
      replaceContainerSections(
        definition,
        activeContainerId,
        resizedSections(page, count),
      ),
    );
  };

  const handleTemplatePick = (templateId: string) => {
    const template = templateById(templateId);
    if (!template) return;
    const next = template.build(objectViews, actionViews);
    const defaultPage = next.page;
    onChange(next);
    setActiveContainerId(defaultPage.id);
    select({ type: "page", pageId: defaultPage.id });
    const firstSection = defaultPage.sections[0];
    if (firstSection) onSelectSection(firstSection.id);
  };

  const selectedWidgetId =
    selection?.type === "widget" ? selection.widgetId : null;
  const currentSectionId =
    selection?.type === "section"
      ? selection.sectionId
      : selection?.type === "widget"
        ? selection.sectionId
        : selectedSectionId;

  return (
    <>
      <div className="flex h-full min-h-0 flex-col bg-[#f3f5f7]">
        <BusinessReviewHeader definition={definition} objectCount={objectViews.length} actionCount={actionViews.length} />
        <div className="grid min-h-0 flex-1 grid-cols-[288px_1fr_352px] divide-x divide-[#e1e6eb]">
          <StructurePanel
            definition={definition}
            activeContainerId={activeContainerId}
            selection={selection}
            onChange={onChange}
            onSelect={select}
            onActiveContainer={setActiveContainerId}
            onOpenTemplates={() => setIsTemplateGalleryOpen(true)}
          />
          <BuilderCanvas
            page={page}
            presentation={definition.presentation}
            objectViews={objectViews}
            actionViews={actionViews}
            selectedSectionId={currentSectionId}
            selectedWidgetId={selectedWidgetId}
            onSelectSection={(sectionId) => select({ type: "section", sectionId })}
            onSelectWidget={(sectionId, widgetId) => select({ type: "widget", sectionId, widgetId })}
            onAddWidget={handleAddWidget}
            onRemoveWidget={(_sectionId, widgetId) => handleRemoveWidget(widgetId)}
            onBindWidgetObject={(_sectionId, widgetId, objectApiName) => handleBindObject(widgetId, objectApiName)}
            onBindWidgetAction={(_sectionId, widgetId, actionApiName) => handleBindAction(widgetId, actionApiName)}
            onApplyLayout={handleApplyLayout}
          />
          <InspectorPanel definition={definition} selection={selection} objectViews={objectViews} actionViews={actionViews} onChange={onChange} onSelect={select} />
        </div>
      </div>
      <TemplateGallery
        open={isTemplateGalleryOpen}
        onOpenChange={setIsTemplateGalleryOpen}
        onPick={handleTemplatePick}
      />
    </>
  );
}

function BusinessReviewHeader({ definition, objectCount, actionCount }: { definition: AppDefinition; objectCount: number; actionCount: number }) {
  const roles = definition.presentation.roles;
  return (
    <div className="shrink-0 border-b border-[#e1e6eb] bg-white px-5 py-4">
      <div className="flex items-center gap-5">
        <div className="flex size-10 items-center justify-center rounded-2xl bg-[#eeeafd] text-[#6651c7]"><Sparkles className="size-5" /></div>
        <div className="min-w-0 flex-1"><div className="text-[11px] font-bold text-[#6651c7]">AI FDE 검토</div><h1 className="mt-0.5 truncate text-[17px] font-bold tracking-[-.025em] text-[#172033]">누가 무엇을 보고, 어떤 일을 할지 확인하세요</h1></div>
        <ReviewSignal icon={UsersRound} label="사용자" value={roles.length ? roles.slice(0, 2).join(" · ") : "역할 확인 필요"} />
        <ReviewSignal icon={MonitorSmartphone} label="화면" value={`${definition.pages.length}개 · 모바일 포함`} />
        <ReviewSignal icon={CheckCircle2} label="업무 연결" value={`${objectCount}개 개념 · ${actionCount}개 행동`} isReady={objectCount > 0 && actionCount > 0} />
      </div>
    </div>
  );
}

function ReviewSignal({ icon: Icon, label, value, isReady = false }: { icon: typeof UsersRound; label: string; value: string; isReady?: boolean }) {
  return <div className="hidden min-w-[150px] items-center gap-2.5 rounded-xl bg-[#f7f9fb] px-3 py-2 xl:flex"><Icon className={isReady ? "size-4 text-emerald-600" : "size-4 text-[#718096]"} /><div className="min-w-0"><div className="text-[9px] font-bold text-[#8a96a6]">{label}</div><div className="mt-0.5 max-w-36 truncate text-[11px] font-semibold text-[#3e4c60]">{value}</div></div></div>;
}
