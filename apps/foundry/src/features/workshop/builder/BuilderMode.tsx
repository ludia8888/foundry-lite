import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
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
    }).map(() => createSection("Section"));
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
      <div className="grid h-full min-h-0 grid-cols-[260px_1fr_320px] divide-x divide-[#d5dce1]">
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
          objectViews={objectViews}
          actionViews={actionViews}
          selectedSectionId={currentSectionId}
          selectedWidgetId={selectedWidgetId}
          onSelectSection={(sectionId) =>
            select({ type: "section", sectionId })
          }
          onSelectWidget={(sectionId, widgetId) =>
            select({ type: "widget", sectionId, widgetId })
          }
          onAddWidget={handleAddWidget}
          onRemoveWidget={(_sectionId, widgetId) =>
            handleRemoveWidget(widgetId)
          }
          onBindWidgetObject={(_sectionId, widgetId, objectApiName) =>
            handleBindObject(widgetId, objectApiName)
          }
          onBindWidgetAction={(_sectionId, widgetId, actionApiName) =>
            handleBindAction(widgetId, actionApiName)
          }
          onApplyLayout={handleApplyLayout}
        />
        <InspectorPanel
          definition={definition}
          selection={selection}
          objectViews={objectViews}
          actionViews={actionViews}
          onChange={onChange}
          onSelect={select}
        />
      </div>
      <TemplateGallery
        open={isTemplateGalleryOpen}
        onOpenChange={setIsTemplateGalleryOpen}
        onPick={handleTemplatePick}
      />
    </>
  );
}
