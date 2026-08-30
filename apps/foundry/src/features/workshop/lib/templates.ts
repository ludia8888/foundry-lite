import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import {
  BarChart3,
  CheckCircle2,
  LayoutDashboard,
  Radar,
  Search,
  type LucideIcon,
} from "lucide-react";

import {
  createHeaderSlots,
  createId,
  DEFAULT_APP_SHELL,
  DEFAULT_APP_PRESENTATION,
  DEFAULT_APP_THEME,
  defaultSectionStyle,
  type AppDefinition,
  type AppPage,
  type AppSection,
  type SectionLayout,
  type SectionStyle,
  type WidgetConfig,
  type WidgetKind,
} from "./app-model";
import { buildWidgetSuggestion } from "./ontology-context";
import { widgetDefinition, type WidgetSuggestion } from "./widget-catalog";

type TemplateContext = {
  suggestion: WidgetSuggestion;
};

export type WorkshopTemplate = {
  id: string;
  name: string;
  description: string;
  icon: LucideIcon;
  tags: string[];
  build: (
    objectViews: readonly FoundryLiteOntologyObjectView[],
    actionViews: readonly FoundryLiteOntologyActionView[],
  ) => AppDefinition;
};

function widget(
  kind: WidgetKind,
  ctx: TemplateContext,
  overrides: WidgetConfig = {},
) {
  return {
    id: createId("w"),
    kind,
    config: {
      ...widgetDefinition(kind).defaultConfig(ctx.suggestion),
      ...overrides,
    },
  };
}

function section(
  title: string,
  layout: SectionLayout,
  widgets: AppSection["widgets"],
  style?: Partial<SectionStyle>,
  span: AppSection["span"] = 12,
): AppSection {
  return {
    id: createId("sec"),
    title,
    layout,
    style: { ...defaultSectionStyle(), ...style },
    span,
    widgets,
  };
}

function page(
  name: string,
  isDefault: boolean,
  sections: AppSection[],
): AppPage {
  return {
    id: createId("page"),
    name,
    pageId: name.toLowerCase().replace(/[^a-z0-9가-힣]+/g, "-"),
    isDefault,
    backgroundColor: "transparent",
    layoutDirection: "columns",
    intent: isDefault ? "workbench" : "records",
    sections,
  };
}

function definition(
  name: string,
  purpose: string,
  pages: AppPage[],
): AppDefinition {
  const defaultPage = pages.find((candidate) => candidate.isDefault) ?? pages[0];
  return {
    name,
    purpose,
    theme: { ...DEFAULT_APP_THEME, brandName: name, logoText: name.slice(0, 2) },
    shell: { ...DEFAULT_APP_SHELL },
    presentation: structuredClone(DEFAULT_APP_PRESENTATION),
    header: { visible: true, title: name, slots: createHeaderSlots() },
    page: defaultPage,
    pages,
    overlays: [],
    variables: [],
    savedAt: null,
    version: 0,
  };
}

export const WORKSHOP_TEMPLATES: readonly WorkshopTemplate[] = [
  {
    id: "object-explorer",
    name: "업무 탐색",
    description: "검색, 목록, 상세 화면으로 고객과 업무를 찾습니다.",
    icon: Search,
    tags: ["업무 보기", "검색"],
    build: (objectViews, actionViews) => {
      const ctx = {
        suggestion: buildWidgetSuggestion(objectViews, actionViews),
      };
      return definition("업무 탐색", "고객과 업무를 빠르게 찾아 확인합니다", [
        page("탐색", true, [
          section(
            "헤더",
            "toolbar",
            [widget("objectSetTitle", ctx), widget("searchBar", ctx)],
            { background: "#f0f2f5", padding: "compact" },
          ),
          section("본문", "columns", [
            widget("filterList", ctx),
            widget("objectTable", ctx),
            widget("objectDetail", ctx),
          ], undefined, 12),
        ]),
      ]);
    },
  },
  {
    id: "approval-queue",
    name: "승인함",
    description: "승인 대기 업무를 검토하고 안전하게 처리합니다.",
    icon: CheckCircle2,
    tags: ["업무 처리", "운영"],
    build: (objectViews, actionViews) => {
      const ctx = {
        suggestion: buildWidgetSuggestion(objectViews, actionViews),
      };
      return definition("승인함", "대기 중인 업무를 검토하고 승인합니다", [
        page("승인", true, [
          section(
            "요약",
            "toolbar",
            [
              widget("metricCard", ctx, { title: "전체", metric: "count" }),
              widget("objectDropdown", ctx),
              widget("searchBar", ctx),
            ],
            { background: "#eaf1fb", padding: "compact" },
          ),
          section("작업", "columns", [
            widget("objectTable", ctx),
            widget("kanban", ctx),
            widget("objectDetail", ctx),
            widget("actionForm", ctx),
          ]),
        ]),
      ]);
    },
  },
  {
    id: "ops-dashboard",
    name: "운영 대시보드",
    description: "핵심 숫자와 차트, 업무 목록을 한눈에 봅니다.",
    icon: LayoutDashboard,
    tags: ["현황 분석", "핵심 숫자"],
    build: (objectViews, actionViews) => {
      const ctx = {
        suggestion: buildWidgetSuggestion(objectViews, actionViews),
      };
      const numeric = ctx.suggestion.numericProperty;
      return definition("운영 대시보드", "핵심 지표와 분포를 한눈에", [
        page("대시보드", true, [
          section(
            "핵심 현황",
            "flow",
            [
              widget("metricCard", ctx, {
                title: "핵심 지표",
                metricLayout: "card",
                metrics: [
                  { label: "건수", metric: "count" },
                  { label: "합계", metric: "sum", property: numeric },
                  { label: "평균", metric: "avg", property: numeric },
                ],
              }),
            ],
            { padding: "compact" },
          ),
          section("분포", "columns", [
            widget("barChart", ctx),
            widget("pieChart", ctx),
          ]),
          section("운영 흐름", "columns", [
            widget("statusTracker", ctx),
            widget("pivotTable", ctx),
          ]),
          section("업무 목록", "flow", [widget("objectTable", ctx)]),
        ]),
      ]);
    },
  },
  {
    id: "object-360",
    name: "업무 전체 보기",
    description: "한 업무의 상세, 관계, 진행 기록을 함께 확인합니다.",
    icon: Radar,
    tags: ["업무 보기", "관계"],
    build: (objectViews, actionViews) => {
      const ctx = {
        suggestion: buildWidgetSuggestion(objectViews, actionViews),
      };
      return definition("업무 전체 보기", "업무와 연결 관계, 진행 기록을 함께 봅니다", [
        page("상세 보기", true, [
          section(
            "헤더",
            "toolbar",
            [widget("objectSetTitle", ctx), widget("objectDropdown", ctx)],
            { background: "#f1ecfb", padding: "compact" },
          ),
          section("본문", "columns", [
            widget("objectList", ctx),
            widget("objectDetail", ctx),
            widget("links", ctx),
          ]),
          section("이력", "flow", [widget("timeline", ctx)]),
        ]),
      ]);
    },
  },
  {
    id: "analytics-overview",
    name: "분석 개요",
    description: "현황 차트와 상세 업무 화면을 나누어 제공합니다.",
    icon: BarChart3,
    tags: ["현황 분석", "여러 화면"],
    build: (objectViews, actionViews) => {
      const ctx = {
        suggestion: buildWidgetSuggestion(objectViews, actionViews),
      };
      const numeric = ctx.suggestion.numericProperty;
      return definition("분석 개요", "차트 개요와 레코드 상세를 분리한 앱", [
        page("개요", true, [
          section(
            "지표",
            "flow",
            [
              widget("metricCard", ctx, {
                title: "핵심 지표",
                metricLayout: "card",
                metrics: [
                  { label: "건수", metric: "count" },
                  { label: "합계", metric: "sum", property: numeric },
                  { label: "평균", metric: "avg", property: numeric },
                ],
              }),
            ],
            { padding: "compact" },
          ),
          section("차트", "columns", [
            widget("barChart", ctx),
            widget("pieChart", ctx),
          ]),
        ]),
        page("레코드", false, [
          section("탐색", "toolbar", [widget("searchBar", ctx)], {
            padding: "compact",
          }),
          section("데이터", "columns", [
            widget("objectTable", ctx),
            widget("objectDetail", ctx),
          ]),
        ]),
      ]);
    },
  },
];

export function templateById(id: string): WorkshopTemplate | null {
  return WORKSHOP_TEMPLATES.find((template) => template.id === id) ?? null;
}
