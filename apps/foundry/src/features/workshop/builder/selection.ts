import type {
  AppDefinition,
  AppOverlay,
  AppPage,
  AppSection,
  AppVariable,
  AppWidget,
} from "../lib/app-model";

/** 캔버스가 편집하는 컨테이너(페이지 또는 오버레이)의 공통 형태. */
export type BuilderContainer = {
  id: string;
  sections: AppSection[];
  backgroundColor: string;
};

/** 빌더에서 현재 편집 대상. */
export type BuilderSelection =
  | { type: "app" }
  | { type: "widget"; sectionId: string; widgetId: string }
  | { type: "section"; sectionId: string }
  | { type: "page"; pageId: string }
  | { type: "overlay"; overlayId: string }
  | { type: "header" }
  | { type: "variable"; variableId: string }
  | null;

export function findPage(
  definition: AppDefinition,
  pageId: string,
): AppPage | null {
  return definition.pages.find((page) => page.id === pageId) ?? null;
}

export function findOverlay(
  definition: AppDefinition,
  overlayId: string,
): AppOverlay | null {
  return (
    definition.overlays.find((overlay) => overlay.id === overlayId) ?? null
  );
}

export function findVariable(
  definition: AppDefinition,
  variableId: string,
): AppVariable | null {
  return (
    definition.variables.find((variable) => variable.id === variableId) ?? null
  );
}

/** 헤더 슬롯·페이지·오버레이의 모든 섹션을 순회. */
function allSections(definition: AppDefinition): AppSection[] {
  const { left, center, right } = definition.header.slots;
  return [
    left,
    center,
    right,
    ...definition.pages.flatMap((page) => page.sections),
    ...definition.overlays.flatMap((overlay) => overlay.sections),
  ];
}

export function findSection(
  definition: AppDefinition,
  sectionId: string,
): AppSection | null {
  return allSections(definition).find((item) => item.id === sectionId) ?? null;
}

export function findWidget(
  definition: AppDefinition,
  widgetId: string,
): AppWidget | null {
  for (const section of allSections(definition)) {
    const widget = section.widgets.find((item) => item.id === widgetId);
    if (widget) return widget;
  }
  return null;
}
