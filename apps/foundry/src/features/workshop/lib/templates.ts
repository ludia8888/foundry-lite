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
): AppSection {
  return {
    id: createId("sec"),
    title,
    layout,
    style: { ...defaultSectionStyle(), ...style },
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
    name: "객체 탐색기",
    description: "필터·검색·테이블·상세로 객체를 탐색하는 기본 앱.",
    icon: Search,
    tags: ["표시", "필터"],
    build: (objectViews, actionViews) => {
      const ctx = {
        suggestion: buildWidgetSuggestion(objectViews, actionViews),
      };
      return definition("객체 탐색기", "객체를 필터·검색으로 탐색합니다", [
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
          ]),
        ]),
      ]);
    },
  },
  {
    id: "approval-queue",
    name: "승인 큐",
    description: "메트릭·필터·테이블·상세·액션 폼으로 승인 업무를 처리.",
    icon: CheckCircle2,
    tags: ["액션", "운영"],
    build: (objectViews, actionViews) => {
      const ctx = {
        suggestion: buildWidgetSuggestion(objectViews, actionViews),
      };
      return definition("승인 큐", "객체를 검토하고 액션으로 승인합니다", [
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
    description: "KPI 카드 + 막대·파이 차트 + 테이블의 운영 현황판.",
    icon: LayoutDashboard,
    tags: ["시각화", "KPI"],
    build: (objectViews, actionViews) => {
      const ctx = {
        suggestion: buildWidgetSuggestion(objectViews, actionViews),
      };
      const numeric = ctx.suggestion.numericProperty;
      return definition("운영 대시보드", "핵심 지표와 분포를 한눈에", [
        page("대시보드", true, [
          section(
            "KPI",
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
          section("레코드", "flow", [widget("objectTable", ctx)]),
        ]),
      ]);
    },
  },
  {
    id: "object-360",
    name: "객체 360",
    description: "리스트·상세·링크·타임라인으로 단일 객체를 360도 조망.",
    icon: Radar,
    tags: ["표시", "관계"],
    build: (objectViews, actionViews) => {
      const ctx = {
        suggestion: buildWidgetSuggestion(objectViews, actionViews),
      };
      return definition("객체 360", "객체와 연결·이력을 종합 조망합니다", [
        page("360", true, [
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
    description: "차트 중심 개요 페이지 + 레코드 상세 페이지의 멀티페이지 앱.",
    icon: BarChart3,
    tags: ["시각화", "멀티페이지"],
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
