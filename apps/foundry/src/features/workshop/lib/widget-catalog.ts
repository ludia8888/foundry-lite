import {
  Activity,
  AlignLeft,
  BarChart3,
  Bot,
  CalendarDays,
  Columns3,
  Filter,
  Heading,
  Hash,
  Link2,
  List,
  ListChecks,
  ListFilter,
  Minus,
  MousePointerClick,
  PanelRight,
  PieChart,
  Play,
  Search,
  Route,
  Table2,
  TableProperties,
  Type,
  type LucideIcon,
} from "lucide-react";

import type { WidgetConfig, WidgetKind } from "./app-model";

export type WidgetCategory =
  "display" | "visualization" | "input" | "action" | "content" | "aip";

/** 인스펙터가 렌더링하는 config 필드 종류. */
export type WidgetConfigField =
  | "title"
  | "object"
  | "linkType"
  | "action"
  | "actions"
  | "properties"
  | "columns"
  | "filterProperty"
  | "metric"
  | "metricProperty"
  | "groupBy"
  | "series"
  | "chartType"
  | "dateProperty"
  | "text"
  | "unit"
  | "metrics"
  | "metricLayout"
  | "setsVariable"
  | "variableFilter";

/** 위젯이 런타임 공유 상태와 맺는 관계. */
export type WidgetRole =
  | "source" /** 객체 목록 → 행 선택으로 selection을 쓴다 */
  | "filter" /** 필터/검색 → filter 상태를 쓴다 */
  | "consumer" /** 선택된 객체를 읽어 상세/액션을 보여준다 */
  | "aggregate" /** 필터된 집합을 집계한다 */
  | "content"; /** 정적 콘텐츠 */

export type WidgetDefinition = {
  kind: WidgetKind;
  label: string;
  description: string;
  icon: LucideIcon;
  category: WidgetCategory;
  role: WidgetRole;
  /** 인스펙터에 노출할 설정 필드. */
  fields: WidgetConfigField[];
  /** 툴바 레이아웃에 어울리는 소형 위젯 여부. */
  compact: boolean;
  /** 온톨로지 컨텍스트를 받아 기본 config를 생성한다(즉시 바인딩). */
  defaultConfig: (suggestion: WidgetSuggestion) => WidgetConfig;
};

/** 위젯 추가 시 자동 바인딩할 후보. */
export type WidgetSuggestion = {
  objectApiName: string | null;
  actionApiName: string | null;
  actionApiNames: string[];
  statusProperty: string | null;
  numericProperty: string | null;
  dateProperty: string | null;
};

export const WIDGET_CATEGORIES: ReadonlyArray<{
  id: WidgetCategory;
  label: string;
}> = [
  { id: "display", label: "업무 보기" },
  { id: "visualization", label: "현황 분석" },
  { id: "input", label: "검색·선택" },
  { id: "action", label: "업무 처리" },
  { id: "content", label: "안내" },
  { id: "aip", label: "AI 지원" },
];

export const WIDGET_DEFINITIONS: Record<WidgetKind, WidgetDefinition> = {
  objectTable: {
    kind: "objectTable",
    label: "업무 목록",
    description: "정렬하고 선택할 수 있는 업무 목록을 보여줍니다.",
    icon: Table2,
    category: "display",
    role: "source",
    fields: ["title", "object", "columns", "variableFilter"],
    compact: false,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      propertyApiNames: [],
    }),
  },
  objectList: {
    kind: "objectList",
    label: "업무 카드 목록",
    description: "모바일에서도 보기 쉬운 카드 목록을 보여줍니다.",
    icon: List,
    category: "display",
    role: "source",
    fields: ["title", "object", "variableFilter"],
    compact: false,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  objectDetail: {
    kind: "objectDetail",
    label: "선택 업무 정보",
    description: "선택한 업무에 필요한 정보를 한곳에 보여줍니다.",
    icon: PanelRight,
    category: "display",
    role: "consumer",
    fields: ["title", "object", "properties"],
    compact: false,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  objectSetTitle: {
    kind: "objectSetTitle",
    label: "업무 범위 요약",
    description: "현재 보고 있는 업무 범위와 건수를 알려줍니다.",
    icon: Heading,
    category: "display",
    role: "aggregate",
    fields: ["title", "object", "text"],
    compact: true,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  links: {
    kind: "links",
    label: "연결된 업무",
    description: "선택한 업무와 연결된 다른 업무를 보여줍니다.",
    icon: Link2,
    category: "display",
    role: "consumer",
    fields: ["title", "object"],
    compact: false,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  objectLinks: {
    kind: "objectLinks",
    label: "관계 탐색",
    description: "연결 관계를 따라 관련 고객과 업무를 탐색합니다.",
    icon: Link2,
    category: "display",
    role: "consumer",
    fields: ["title", "object", "linkType"],
    compact: false,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName, linkTypeApiName: null }),
  },
  metricCard: {
    kind: "metricCard",
    label: "핵심 숫자",
    description: "건수, 합계, 평균 같은 핵심 숫자를 보여줍니다.",
    icon: Hash,
    category: "visualization",
    role: "aggregate",
    fields: ["title", "metricLayout", "object", "metrics", "variableFilter"],
    compact: true,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      metricLayout: "card",
      metric: "count",
      metricProperty: s.numericProperty,
    }),
  },
  barChart: {
    kind: "barChart",
    label: "비교 차트",
    description: "업무 그룹별 수치와 추이를 비교해 보여줍니다.",
    icon: BarChart3,
    category: "visualization",
    role: "aggregate",
    fields: [
      "title",
      "chartType",
      "object",
      "groupBy",
      "series",
      "metric",
      "metricProperty",
      "variableFilter",
    ],
    compact: false,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      chartType: "bar",
      groupByProperty: s.statusProperty,
      metric: "count",
      metricProperty: s.numericProperty,
    }),
  },
  pieChart: {
    kind: "pieChart",
    label: "비중 차트",
    description: "각 업무 상태나 분류가 차지하는 비중을 보여줍니다.",
    icon: PieChart,
    category: "visualization",
    role: "aggregate",
    fields: ["title", "object", "groupBy"],
    compact: false,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      groupByProperty: s.statusProperty,
    }),
  },
  timeline: {
    kind: "timeline",
    label: "진행 기록",
    description: "날짜 순서대로 업무 진행 기록을 보여줍니다.",
    icon: Activity,
    category: "visualization",
    role: "source",
    fields: ["title", "object", "dateProperty"],
    compact: false,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      dateProperty: s.dateProperty,
    }),
  },
  kanban: {
    kind: "kanban",
    label: "단계별 업무 보드",
    description: "상태별 업무를 열로 나누고 선택 가능한 카드로 보여줍니다.",
    icon: Columns3,
    category: "visualization",
    role: "source",
    fields: ["title", "object", "groupBy", "properties", "variableFilter"],
    compact: false,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      groupByProperty: s.statusProperty,
      propertyApiNames: [],
    }),
  },
  calendar: {
    kind: "calendar",
    label: "일정 캘린더",
    description: "날짜별 업무를 월간 캘린더와 일정 목록으로 보여줍니다.",
    icon: CalendarDays,
    category: "visualization",
    role: "source",
    fields: ["title", "object", "dateProperty", "properties", "variableFilter"],
    compact: false,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      dateProperty: s.dateProperty,
      propertyApiNames: [],
    }),
  },
  statusTracker: {
    kind: "statusTracker",
    label: "업무 흐름",
    description: "업무 상태별 건수와 흐름을 한 줄 운영 지표로 보여줍니다.",
    icon: Route,
    category: "visualization",
    role: "aggregate",
    fields: ["title", "object", "groupBy", "variableFilter"],
    compact: true,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      groupByProperty: s.statusProperty,
    }),
  },
  pivotTable: {
    kind: "pivotTable",
    label: "교차 분석표",
    description: "두 업무 기준을 교차해 개수·합계·평균을 비교합니다.",
    icon: TableProperties,
    category: "visualization",
    role: "aggregate",
    fields: [
      "title",
      "object",
      "groupBy",
      "series",
      "metric",
      "metricProperty",
      "variableFilter",
    ],
    compact: false,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      groupByProperty: s.statusProperty,
      seriesProperty: null,
      metric: "count",
      metricProperty: s.numericProperty,
    }),
  },
  filterList: {
    kind: "filterList",
    label: "빠른 필터",
    description: "상태와 담당자 같은 기준으로 업무를 빠르게 좁힙니다.",
    icon: Filter,
    category: "input",
    role: "filter",
    fields: ["title", "object", "properties"],
    compact: false,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      propertyApiNames: s.statusProperty ? [s.statusProperty] : [],
    }),
  },
  objectDropdown: {
    kind: "objectDropdown",
    label: "조건 선택",
    description: "하나의 기준을 선택해 모든 화면에 함께 적용합니다.",
    icon: ListFilter,
    category: "input",
    role: "filter",
    fields: ["title", "object", "filterProperty", "setsVariable"],
    compact: true,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      filterProperty: s.statusProperty,
    }),
  },
  searchBar: {
    kind: "searchBar",
    label: "업무 검색",
    description: "고객, 담당자, 업무 내용을 키워드로 찾습니다.",
    icon: Search,
    category: "input",
    role: "filter",
    fields: ["title", "object"],
    compact: true,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  stringSelector: {
    kind: "stringSelector",
    label: "빠른 조건 버튼",
    description: "자주 쓰는 조건을 버튼으로 골라 업무를 좁힙니다.",
    icon: ListChecks,
    category: "input",
    role: "filter",
    fields: ["title", "object", "filterProperty"],
    compact: true,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      filterProperty: s.statusProperty,
    }),
  },
  buttonGroup: {
    kind: "buttonGroup",
    label: "다음 업무",
    description: "선택한 업무에서 할 수 있는 다음 행동을 보여줍니다.",
    icon: MousePointerClick,
    category: "action",
    role: "consumer",
    fields: ["title", "object", "actions"],
    compact: true,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      actionApiNames: s.actionApiNames,
    }),
  },
  actionForm: {
    kind: "actionForm",
    label: "업무 처리 양식",
    description: "필요한 내용을 확인하고 안전하게 업무를 실행합니다.",
    icon: Play,
    category: "action",
    role: "consumer",
    fields: ["title", "object", "action"],
    compact: false,
    defaultConfig: (s) => ({
      objectApiName: s.objectApiName,
      actionApiName: s.actionApiName,
    }),
  },
  markdown: {
    kind: "markdown",
    label: "안내문",
    description: "사용자에게 제목과 설명을 보기 좋게 안내합니다.",
    icon: Type,
    category: "content",
    role: "content",
    fields: ["text"],
    compact: false,
    defaultConfig: () => ({
      text: "## 제목\n설명 텍스트를 입력하세요.",
    }),
  },
  sectionHeader: {
    kind: "sectionHeader",
    label: "영역 제목",
    description: "화면 영역의 제목을 분명하게 표시합니다.",
    icon: AlignLeft,
    category: "content",
    role: "content",
    fields: ["text"],
    compact: true,
    defaultConfig: () => ({ text: "섹션 제목" }),
  },
  divider: {
    kind: "divider",
    label: "구분선",
    description: "가로 구분선을 삽입합니다.",
    icon: Minus,
    category: "content",
    role: "content",
    fields: [],
    compact: true,
    defaultConfig: () => ({}),
  },
  aipChatbot: {
    kind: "aipChatbot",
    label: "AI 업무 도우미",
    description: "현재 업무 맥락을 이해하고 질문과 제안을 돕습니다.",
    icon: Bot,
    category: "aip",
    role: "content",
    fields: ["title"],
    compact: false,
    defaultConfig: () => ({ title: "AI 업무 도우미" }),
  },
};

export const WIDGET_LIST: readonly WidgetDefinition[] =
  Object.values(WIDGET_DEFINITIONS);

export function widgetDefinition(kind: WidgetKind): WidgetDefinition {
  return WIDGET_DEFINITIONS[kind];
}

export function widgetLabel(kind: WidgetKind): string {
  return WIDGET_DEFINITIONS[kind]?.label ?? kind;
}
