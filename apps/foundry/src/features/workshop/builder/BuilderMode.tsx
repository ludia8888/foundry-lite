import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";

import {
  defaultSectionStyle,
  createWidget,
  type AppDefinition,
  type AppPage,
  type AppSection,
  type WidgetKind,
} from "../lib/app-model";
import { BuilderCanvas } from "./BuilderCanvas";
import { BuilderSettingsPanel } from "./BuilderSettingsPanel";
import { BuilderStructurePanel } from "./BuilderStructurePanel";

interface BuilderModeProps {
  definition: AppDefinition;
  objectViews: FoundryLiteOntologyObjectView[];
  actionViews: FoundryLiteOntologyActionView[];
  selectedSectionId: string | null;
  onSelectSection: (sectionId: string) => void;
  onChange: (definition: AppDefinition) => void;
}

function updateSection(
  page: AppPage,
  sectionId: string,
  updater: (section: AppSection) => AppSection,
): AppPage {
  return {
    ...page,
    sections: page.sections.map((section) =>
      section.id === sectionId ? updater(section) : section,
    ),
  };
}

/** 빌더 모드: 좌 구조 · 중앙 캔버스 · 우 설정 3-컬럼. 앱 정의는 불변 업데이트. */
export function BuilderMode({
  definition,
  objectViews,
  actionViews,
  selectedSectionId,
  onSelectSection,
  onChange,
}: BuilderModeProps) {
  const { page } = definition;

  const patchPage = (patch: Partial<AppPage>) => {
    onChange({ ...definition, page: { ...page, ...patch } });
  };

  const handleAddWidget = (sectionId: string, kind: WidgetKind) => {
    // 신규 위젯: 단일 객체 타입/액션이면 기본 바인딩해 곧바로 실동작하게 한다.
    const defaultObject =
      objectViews.length === 1 ? objectViews[0].apiName : null;
    const defaultAction =
      actionViews.length === 1 ? actionViews[0].apiName : null;
    const boundObject =
      kind === "actionForm" && defaultAction
        ? (actionViews.find((view) => view.apiName === defaultAction)
            ?.targetObjectApiName ?? defaultObject)
        : defaultObject;
    const widget = createWidget(
      kind,
      boundObject,
      kind === "actionForm" ? defaultAction : null,
    );
    patchPage({
      sections: updateSection(page, sectionId, (section) => ({
        ...section,
        widgets: [...section.widgets, widget],
      })).sections,
    });
    onSelectSection(sectionId);
  };

  const handleRemoveWidget = (sectionId: string, widgetId: string) => {
    patchPage({
      sections: updateSection(page, sectionId, (section) => ({
        ...section,
        widgets: section.widgets.filter((widget) => widget.id !== widgetId),
      })).sections,
    });
  };

  const handleBindObject = (
    sectionId: string,
    widgetId: string,
    objectApiName: string,
  ) => {
    patchPage({
      sections: updateSection(page, sectionId, (section) => ({
        ...section,
        widgets: section.widgets.map((widget) =>
          widget.id === widgetId ? { ...widget, objectApiName } : widget,
        ),
      })).sections,
    });
  };

  const handleBindAction = (
    sectionId: string,
    widgetId: string,
    actionApiName: string,
  ) => {
    const targetObjectApiName =
      actionViews.find((view) => view.apiName === actionApiName)
        ?.targetObjectApiName ?? null;
    patchPage({
      sections: updateSection(page, sectionId, (section) => ({
        ...section,
        widgets: section.widgets.map((widget) =>
          widget.id === widgetId
            ? {
                ...widget,
                actionApiName,
                objectApiName: targetObjectApiName ?? widget.objectApiName,
              }
            : widget,
        ),
      })).sections,
    });
  };

  const handleApplyLayout = (columns: number) => {
    const current = page.sections;
    if (columns === current.length) return;
    let sections = current;
    if (columns > current.length) {
      const additions = Array.from({ length: columns - current.length }).map(
        (_, index) => ({
          id: `sec-${Date.now()}-${index}`,
          title: "Section",
          layout: "flow" as const,
          style: defaultSectionStyle(),
          widgets: [],
        }),
      );
      sections = [...current, ...additions];
    } else {
      // 초과 섹션의 위젯을 마지막 유지 섹션으로 병합해 데이터 손실을 막는다.
      const kept = current.slice(0, columns);
      const overflowWidgets = current
        .slice(columns)
        .flatMap((section) => section.widgets);
      sections = kept.map((section, index) =>
        index === kept.length - 1
          ? { ...section, widgets: [...section.widgets, ...overflowWidgets] }
          : section,
      );
    }
    patchPage({ sections });
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-[240px_1fr_280px] divide-x divide-[#d5dce1]">
      <BuilderStructurePanel
        definition={definition}
        objectTypeCount={objectViews.length}
        selectedSectionId={selectedSectionId}
        onSelectSection={onSelectSection}
      />
      <BuilderCanvas
        page={page}
        objectViews={objectViews}
        actionViews={actionViews}
        selectedSectionId={selectedSectionId}
        onSelectSection={onSelectSection}
        onAddWidget={handleAddWidget}
        onRemoveWidget={handleRemoveWidget}
        onBindWidgetObject={handleBindObject}
        onBindWidgetAction={handleBindAction}
        onApplyLayout={handleApplyLayout}
      />
      <BuilderSettingsPanel page={page} onChange={patchPage} />
    </div>
  );
}
