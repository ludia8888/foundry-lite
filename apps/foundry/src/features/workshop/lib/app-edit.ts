import {
  createOverlay,
  createPage,
  createSection,
  createVariable,
  type AppDefinition,
  type AppOverlay,
  type AppPage,
  type AppSection,
  type AppVariable,
  type AppWidget,
  type OverlayKind,
  type SectionLayout,
  type SectionStyle,
  type VariableType,
  type WidgetConfig,
} from "./app-model";

/** 앱 정의를 불변 방식으로 갱신하는 순수 편집 함수들. */

function mapPages(
  definition: AppDefinition,
  updater: (page: AppPage) => AppPage,
): AppDefinition {
  return { ...definition, pages: definition.pages.map(updater) };
}

/** 헤더 슬롯·페이지·오버레이의 모든 섹션 배열에 매퍼를 적용한다. */
function mapAllSections(
  definition: AppDefinition,
  mapSections: (sections: AppSection[]) => AppSection[],
): AppDefinition {
  const { left, center, right } = definition.header.slots;
  // 매퍼가 필터일 수 있으므로 id로 되찾아 슬롯 3개를 항상 보존한다.
  const mappedSlots = mapSections([left, center, right]);
  const slots = {
    left: mappedSlots.find((section) => section.id === left.id) ?? left,
    center: mappedSlots.find((section) => section.id === center.id) ?? center,
    right: mappedSlots.find((section) => section.id === right.id) ?? right,
  };
  return {
    ...definition,
    header: { ...definition.header, slots },
    pages: definition.pages.map((page) => ({
      ...page,
      sections: mapSections(page.sections),
    })),
    overlays: definition.overlays.map((overlay) => ({
      ...overlay,
      sections: mapSections(overlay.sections),
    })),
  };
}

export function updatePage(
  definition: AppDefinition,
  pageId: string,
  updater: (page: AppPage) => AppPage,
): AppDefinition {
  return mapPages(definition, (page) =>
    page.id === pageId ? updater(page) : page,
  );
}

export function updateSection(
  definition: AppDefinition,
  sectionId: string,
  updater: (section: AppSection) => AppSection,
): AppDefinition {
  return mapAllSections(definition, (sections) =>
    sections.map((section) =>
      section.id === sectionId ? updater(section) : section,
    ),
  );
}

export function updateWidget(
  definition: AppDefinition,
  widgetId: string,
  updater: (widget: AppWidget) => AppWidget,
): AppDefinition {
  return mapAllSections(definition, (sections) =>
    sections.map((section) => ({
      ...section,
      widgets: section.widgets.map((widget) =>
        widget.id === widgetId ? updater(widget) : widget,
      ),
    })),
  );
}

export function setWidgetConfig(
  definition: AppDefinition,
  widgetId: string,
  patch: Partial<WidgetConfig>,
): AppDefinition {
  return updateWidget(definition, widgetId, (widget) => ({
    ...widget,
    config: { ...widget.config, ...patch },
  }));
}

export function addWidget(
  definition: AppDefinition,
  sectionId: string,
  widget: AppWidget,
): AppDefinition {
  return updateSection(definition, sectionId, (section) => ({
    ...section,
    widgets: [...section.widgets, widget],
  }));
}

export function removeWidget(
  definition: AppDefinition,
  widgetId: string,
): AppDefinition {
  return mapAllSections(definition, (sections) =>
    sections.map((section) => ({
      ...section,
      widgets: section.widgets.filter((widget) => widget.id !== widgetId),
    })),
  );
}

export function moveWidget(
  definition: AppDefinition,
  sectionId: string,
  widgetId: string,
  direction: -1 | 1,
): AppDefinition {
  return updateSection(definition, sectionId, (section) => {
    const index = section.widgets.findIndex((widget) => widget.id === widgetId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= section.widgets.length) {
      return section;
    }
    const widgets = [...section.widgets];
    const [moved] = widgets.splice(index, 1);
    widgets.splice(target, 0, moved);
    return { ...section, widgets };
  });
}

/** 앱 전체(헤더 슬롯·페이지·오버레이) 섹션을 순회. */
function everySection(definition: AppDefinition): AppSection[] {
  const { left, center, right } = definition.header.slots;
  return [
    left,
    center,
    right,
    ...definition.pages.flatMap((page) => page.sections),
    ...definition.overlays.flatMap((overlay) => overlay.sections),
  ];
}

/**
 * 위젯을 드래그로 이동한다.
 * targetSectionId 안에서 beforeWidgetId 앞에 삽입(같은 섹션 재정렬·다른 섹션 이동 모두).
 * beforeWidgetId가 null이면 섹션 끝에 추가한다.
 */
export function moveWidgetBefore(
  definition: AppDefinition,
  draggedWidgetId: string,
  targetSectionId: string,
  beforeWidgetId: string | null,
): AppDefinition {
  if (draggedWidgetId === beforeWidgetId) return definition;
  const dragged = everySection(definition)
    .flatMap((section) => section.widgets)
    .find((widget) => widget.id === draggedWidgetId);
  if (!dragged) return definition;

  return mapAllSections(definition, (sections) =>
    sections.map((section) => {
      const without = section.widgets.filter(
        (widget) => widget.id !== draggedWidgetId,
      );
      if (section.id !== targetSectionId) {
        return { ...section, widgets: without };
      }
      if (beforeWidgetId === null) {
        return { ...section, widgets: [...without, dragged] };
      }
      const index = without.findIndex((widget) => widget.id === beforeWidgetId);
      if (index < 0) return { ...section, widgets: [...without, dragged] };
      return {
        ...section,
        widgets: [...without.slice(0, index), dragged, ...without.slice(index)],
      };
    }),
  );
}

export function setSectionLayout(
  definition: AppDefinition,
  sectionId: string,
  layout: SectionLayout,
): AppDefinition {
  return updateSection(definition, sectionId, (section) => ({
    ...section,
    layout,
  }));
}

export function setSectionStyle(
  definition: AppDefinition,
  sectionId: string,
  patch: Partial<SectionStyle>,
): AppDefinition {
  return updateSection(definition, sectionId, (section) => ({
    ...section,
    style: { ...section.style, ...patch },
  }));
}

export function setSectionTitle(
  definition: AppDefinition,
  sectionId: string,
  title: string,
): AppDefinition {
  return updateSection(definition, sectionId, (section) => ({
    ...section,
    title,
  }));
}

export function addSection(
  definition: AppDefinition,
  containerId: string,
  layout: SectionLayout = "flow",
): { definition: AppDefinition; section: AppSection } {
  const section = createSection("Section", layout);
  const next: AppDefinition = {
    ...definition,
    pages: definition.pages.map((page) =>
      page.id === containerId
        ? { ...page, sections: [...page.sections, section] }
        : page,
    ),
    overlays: definition.overlays.map((overlay) =>
      overlay.id === containerId
        ? { ...overlay, sections: [...overlay.sections, section] }
        : overlay,
    ),
  };
  return { definition: next, section };
}

export function removeSection(
  definition: AppDefinition,
  sectionId: string,
): AppDefinition {
  return mapAllSections(definition, (sections) =>
    sections.filter((section) => section.id !== sectionId),
  );
}

export function addPage(definition: AppDefinition): {
  definition: AppDefinition;
  page: AppPage;
} {
  const page = createPage(`Page ${definition.pages.length + 1}`, false);
  return {
    definition: { ...definition, pages: [...definition.pages, page] },
    page,
  };
}

export function removePage(
  definition: AppDefinition,
  pageId: string,
): AppDefinition {
  if (definition.pages.length <= 1) return definition;
  const pages = definition.pages.filter((page) => page.id !== pageId);
  if (!pages.some((page) => page.isDefault) && pages[0]) {
    pages[0] = { ...pages[0], isDefault: true };
  }
  return { ...definition, pages };
}

export function renamePage(
  definition: AppDefinition,
  pageId: string,
  name: string,
): AppDefinition {
  return updatePage(definition, pageId, (page) => ({ ...page, name }));
}

export function setDefaultPage(
  definition: AppDefinition,
  pageId: string,
): AppDefinition {
  return mapPages(definition, (page) => ({
    ...page,
    isDefault: page.id === pageId,
  }));
}

export function setPageBackground(
  definition: AppDefinition,
  pageId: string,
  backgroundColor: string,
): AppDefinition {
  return updatePage(definition, pageId, (page) => ({
    ...page,
    backgroundColor,
  }));
}

export function setAppName(
  definition: AppDefinition,
  name: string,
): AppDefinition {
  return {
    ...definition,
    name,
    header: { ...definition.header, title: name },
  };
}

export function setHeaderVisible(
  definition: AppDefinition,
  visible: boolean,
): AppDefinition {
  return { ...definition, header: { ...definition.header, visible } };
}

export function setHeaderTitle(
  definition: AppDefinition,
  title: string,
): AppDefinition {
  return { ...definition, header: { ...definition.header, title } };
}

export function addOverlay(definition: AppDefinition): {
  definition: AppDefinition;
  overlay: AppOverlay;
} {
  const overlay = createOverlay(`Overlay ${definition.overlays.length + 1}`);
  return {
    definition: { ...definition, overlays: [...definition.overlays, overlay] },
    overlay,
  };
}

function updateOverlay(
  definition: AppDefinition,
  overlayId: string,
  updater: (overlay: AppOverlay) => AppOverlay,
): AppDefinition {
  return {
    ...definition,
    overlays: definition.overlays.map((overlay) =>
      overlay.id === overlayId ? updater(overlay) : overlay,
    ),
  };
}

export function removeOverlay(
  definition: AppDefinition,
  overlayId: string,
): AppDefinition {
  return {
    ...definition,
    overlays: definition.overlays.filter((overlay) => overlay.id !== overlayId),
  };
}

export function renameOverlay(
  definition: AppDefinition,
  overlayId: string,
  name: string,
): AppDefinition {
  return updateOverlay(definition, overlayId, (overlay) => ({
    ...overlay,
    name,
  }));
}

export function setOverlayKind(
  definition: AppDefinition,
  overlayId: string,
  kind: OverlayKind,
): AppDefinition {
  return updateOverlay(definition, overlayId, (overlay) => ({
    ...overlay,
    kind,
  }));
}

export function setOverlayWidth(
  definition: AppDefinition,
  overlayId: string,
  widthPx: number,
): AppDefinition {
  return updateOverlay(definition, overlayId, (overlay) => ({
    ...overlay,
    widthPx,
  }));
}

export function addVariable(definition: AppDefinition): {
  definition: AppDefinition;
  variable: AppVariable;
} {
  const variable = createVariable(`변수 ${definition.variables.length + 1}`);
  return {
    definition: {
      ...definition,
      variables: [...definition.variables, variable],
    },
    variable,
  };
}

function updateVariable(
  definition: AppDefinition,
  variableId: string,
  updater: (variable: AppVariable) => AppVariable,
): AppDefinition {
  return {
    ...definition,
    variables: definition.variables.map((variable) =>
      variable.id === variableId ? updater(variable) : variable,
    ),
  };
}

export function renameVariable(
  definition: AppDefinition,
  variableId: string,
  name: string,
): AppDefinition {
  return updateVariable(definition, variableId, (variable) => ({
    ...variable,
    name,
  }));
}

export function setVariableType(
  definition: AppDefinition,
  variableId: string,
  type: VariableType,
): AppDefinition {
  return updateVariable(definition, variableId, (variable) => ({
    ...variable,
    type,
  }));
}

export function setVariableDefault(
  definition: AppDefinition,
  variableId: string,
  defaultValue: string | number | boolean | null,
): AppDefinition {
  return updateVariable(definition, variableId, (variable) => ({
    ...variable,
    defaultValue,
  }));
}

/** 변수를 삭제하고, 위젯 config의 참조(setsVariableId·variableFilters)도 정리한다. */
export function removeVariable(
  definition: AppDefinition,
  variableId: string,
): AppDefinition {
  const withoutVariable: AppDefinition = {
    ...definition,
    variables: definition.variables.filter(
      (variable) => variable.id !== variableId,
    ),
  };
  return mapAllSections(withoutVariable, (sections) =>
    sections.map((section) => ({
      ...section,
      widgets: section.widgets.map((widget) => {
        const config = widget.config;
        const references =
          config.setsVariableId === variableId ||
          (config.variableFilters ?? []).some(
            (filter) => filter.variableId === variableId,
          );
        if (!references) return widget;
        return {
          ...widget,
          config: {
            ...config,
            setsVariableId:
              config.setsVariableId === variableId
                ? null
                : config.setsVariableId,
            variableFilters: (config.variableFilters ?? []).filter(
              (filter) => filter.variableId !== variableId,
            ),
          },
        };
      }),
    })),
  );
}
