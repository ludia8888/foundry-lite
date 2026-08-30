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
  { id: "display", label: "표시" },
  { id: "visualization", label: "시각화" },
  { id: "input", label: "필터·입력" },
  { id: "action", label: "액션·네비" },
  { id: "content", label: "콘텐츠" },
  { id: "aip", label: "AIP" },
];

export const WIDGET_DEFINITIONS: Record<WidgetKind, WidgetDefinition> = {
  objectTable: {
    kind: "objectTable",
    label: "객체 테이블",
    description: "객체 타입을 정렬·선택 가능한 테이블로 렌더링합니다.",
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
    label: "객체 리스트",
    description: "객체를 카드형 리스트로 표시하고 선택할 수 있습니다.",
    icon: List,
    category: "display",
    role: "source",
    fields: ["title", "object", "variableFilter"],
    compact: false,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  objectDetail: {
    kind: "objectDetail",
    label: "객체 상세",
    description: "선택한 객체의 속성을 key-value로 표시합니다.",
    icon: PanelRight,
    category: "display",
    role: "consumer",
    fields: ["title", "object", "properties"],
    compact: false,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  objectSetTitle: {
    kind: "objectSetTitle",
    label: "객체 세트 제목",
    description: "현재 객체 집합의 개수를 제목/카운트로 표시합니다.",
    icon: Heading,
    category: "display",
    role: "aggregate",
    fields: ["title", "object", "text"],
    compact: true,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  links: {
    kind: "links",
    label: "링크",
    description: "선택 객체의 연결(관계) 객체를 표시합니다.",
    icon: Link2,
    category: "display",
    role: "consumer",
    fields: ["title", "object"],
    compact: false,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  objectLinks: {
    kind: "objectLinks",
    label: "관계 순회",
    description: "선택 객체에서 링크를 따라가 실제 연결된 객체를 표시합니다.",
    icon: Link2,
    category: "display",
    role: "consumer",
    fields: ["title", "object", "linkType"],
    compact: false,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName, linkTypeApiName: null }),
  },
  metricCard: {
    kind: "metricCard",
    label: "메트릭 카드",
    description: "집계 값을 KPI 카드/리스트/태그로 보여줍니다 (다중 지표).",
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
    label: "Chart XY",
    description: "카테고리×시리즈 집계를 막대·라인·영역·산점도로 시각화합니다.",
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
    label: "파이 차트",
    description: "속성별 비중을 파이 차트로 시각화합니다.",
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
    label: "타임라인",
    description: "객체를 날짜 속성 기준 시간 순으로 나열합니다.",
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
    label: "칸반 보드",
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
    label: "업무 캘린더",
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
    label: "상태 추적기",
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
    label: "피벗 테이블",
    description: "두 업무 기준을 교차해 개수·합계·평균을 표로 비교합니다.",
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
    label: "필터 리스트",
    description: "속성별 값 분포를 필터 패싯으로 렌더링합니다.",
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
    label: "객체 드롭다운",
    description: "단일 속성 값을 드롭다운으로 선택해 필터·변수를 설정합니다.",
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
    label: "검색 바",
    description: "객체 속성 전체를 대상으로 키워드 검색합니다.",
    icon: Search,
    category: "input",
    role: "filter",
    fields: ["title", "object"],
    compact: true,
    defaultConfig: (s) => ({ objectApiName: s.objectApiName }),
  },
  stringSelector: {
    kind: "stringSelector",
    label: "문자열 셀렉터",
    description: "속성 값을 토글 칩으로 선택해 필터링합니다.",
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
    label: "버튼 그룹",
    description: "선택 객체에 실행할 액션 버튼들을 묶습니다.",
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
    label: "액션 폼",
    description: "선택 객체에 검증 가능한 액션을 폼으로 실행합니다.",
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
    label: "마크다운",
    description: "서식 있는 텍스트/설명을 표시합니다.",
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
    label: "섹션 헤더",
    description: "굵은 제목 라벨을 표시합니다.",
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
    label: "AIP 챗봇",
    description: "온톨로지 컨텍스트 기반 대화형 어시스턴트입니다.",
    icon: Bot,
    category: "aip",
    role: "content",
    fields: ["title"],
    compact: false,
    defaultConfig: () => ({ title: "AIP Assistant" }),
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
