import type { GenericObject, ResourceItem } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";

import type { StatusIntent } from "@/components/shared/StatusPill";

/**
 * 위젯 종류 (Palantir Workshop 카탈로그 대응). 전부 실 SDK 데이터에 바인딩된다.
 * - 표시: objectTable / objectList / objectDetail / objectSetTitle / links
 * - 시각화: metricCard / barChart / pieChart / timeline
 * - 필터·입력: filterList / objectDropdown / searchBar / stringSelector
 * - 액션·네비: buttonGroup / actionForm
 * - 콘텐츠: markdown / sectionHeader / divider
 * - AIP: aipChatbot
 */
export type WidgetKind =
  | "objectTable"
  | "objectList"
  | "objectDetail"
  | "objectSetTitle"
  | "links"
  | "objectLinks"
  | "metricCard"
  | "barChart"
  | "pieChart"
  | "timeline"
  | "kanban"
  | "calendar"
  | "statusTracker"
  | "pivotTable"
  | "filterList"
  | "objectDropdown"
  | "searchBar"
  | "stringSelector"
  | "buttonGroup"
  | "actionForm"
  | "markdown"
  | "sectionHeader"
  | "divider"
  | "aipChatbot";

export type AggregationMetric = "count" | "sum" | "avg" | "min" | "max";

/** Chart XY 차트 유형. */
export type ChartType = "bar" | "horizontalBar" | "line" | "area" | "scatter";

/** Metric Card 개별 지표 정의. */
export type MetricSpec = {
  label: string;
  metric: AggregationMetric;
  property?: string | null;
  unit?: string;
};

/** Metric Card 레이아웃 (Palantir: 카드/리스트/태그). */
export type MetricLayout = "card" | "list" | "tags";

/**
 * 위젯 인스턴스 설정. 위젯 종류에 따라 관련 필드만 사용한다(나머지는 무시).
 * 모든 바인딩은 온톨로지 apiName 기준이라 런타임에서 실 데이터로 해석된다.
 */
export type WidgetConfig = {
  /** 표시 제목(비우면 위젯 기본 라벨). */
  title?: string;
  /** 대상 객체 타입 apiName (테이블/리스트/상세/차트/필터). */
  objectApiName?: string | null;
  /** 단일 액션 apiName (actionForm). */
  actionApiName?: string | null;
  /** 다중 액션 apiName (buttonGroup). */
  actionApiNames?: string[];
  /** 실행 전에 명시적인 사람 확인 단계를 보여줄 고위험 액션 apiName. */
  humanApprovalActionApiNames?: string[];
  /** 표시할 속성 apiName 목록 (테이블 컬럼 / 상세 속성 / 필터 대상). */
  propertyApiNames?: string[];
  /** 집계 메트릭 (metricCard / 차트). */
  metric?: AggregationMetric;
  /** sum/avg/min/max 대상 속성. */
  metricProperty?: string | null;
  /** group-by 속성 (차트 축). */
  groupByProperty?: string | null;
  /** Chart XY 유형 (막대/가로막대/라인/영역/산점도). */
  chartType?: ChartType;
  /** 시리즈(누적·브레이크다운) 속성. 지정 시 시리즈별 누적 + 범례. */
  seriesProperty?: string | null;
  /** 타임라인 정렬 기준 날짜/타임스탬프 속성. */
  dateProperty?: string | null;
  /** 단일 선택 필터 대상 속성 (dropdown/stringSelector). */
  filterProperty?: string | null;
  /** 순회할 링크 타입 apiName (objectLinks). 선택 객체에서 이 관계를 따라간다. */
  linkTypeApiName?: string | null;
  /** 마크다운/헤더 본문. */
  text?: string;
  /** 메트릭 단위 접미사 (예: "건", "₩"). */
  unit?: string;
  /** Metric Card 다중 지표. 비우면 metric/metricProperty 단일 지표로 처리. */
  metrics?: MetricSpec[];
  /** Metric Card 레이아웃 (카드/리스트/태그). */
  metricLayout?: MetricLayout;
  /** 컨트롤 위젯이 값 변경 시 설정할 변수 id (dropdown/stringSelector). */
  setsVariableId?: string | null;
  /** 데이터 위젯이 변수값으로 거르는 조건: 각 property === 해당 변수값. */
  variableFilters?: VariableFilter[];
};

/** 데이터 위젯의 변수 기반 필터 바인딩: property가 변수값과 같은 객체만 통과. */
export type VariableFilter = { property: string; variableId: string };

export type AppWidget = {
  id: string;
  kind: WidgetKind;
  config: WidgetConfig;
  /** Legacy single-page builder alias. Mirrors `config.objectApiName`. */
  objectApiName?: string | null;
  /** Legacy single-page builder alias. Mirrors `config.actionApiName`. */
  actionApiName?: string | null;
};

/** 섹션 레이아웃 (Palantir: Flow / Columns / Rows / Toolbar / Tabs). */
export type SectionLayout = "flow" | "columns" | "rows" | "toolbar" | "tabs";

export type SectionPadding = "none" | "compact" | "regular" | "large";
export type SectionBorder = "none" | "bordered" | "shadow";

export type SectionStyle = {
  background: string;
  padding: SectionPadding;
  border: SectionBorder;
};

export type AppSection = {
  id: string;
  title: string;
  layout: SectionLayout;
  style: SectionStyle;
  /** 12-column SaaS canvas width. Mobile always collapses to one column. */
  span: 3 | 4 | 6 | 8 | 9 | 12;
  widgets: AppWidget[];
};

export type AppLayoutDirection = "columns" | "rows";

export type AppPage = {
  id: string;
  name: string;
  pageId: string;
  isDefault: boolean;
  backgroundColor: string;
  /** Legacy single-page builder layout direction. */
  layoutDirection: AppLayoutDirection;
  /** Business intent used for navigation iconography and responsive composition. */
  intent: "workbench" | "overview" | "records" | "governance" | "evidence" | "relationships";
  sections: AppSection[];
};

export type AppThemePreset = "ocean" | "indigo" | "emerald" | "amber" | "graphite";
export type AppNavigation = "sidebar" | "topbar";
export type AppDensity = "comfortable" | "compact";
export type AppPageWidth = "wide" | "contained";

export type AppTheme = {
  preset: AppThemePreset;
  brandName: string;
  logoText: string;
};

export type AppShell = {
  navigation: AppNavigation;
  density: AppDensity;
  pageWidth: AppPageWidth;
  showContextBar: boolean;
};

/** 헤더 위젯 슬롯 (Palantir: 좌/중/우 3개 슬롯, 각 위젯 배치 가능). */
export type AppHeaderSlots = {
  left: AppSection;
  center: AppSection;
  right: AppSection;
};

export type AppHeader = {
  visible: boolean;
  title: string;
  slots: AppHeaderSlots;
};

/** 오버레이 종류 (Palantir: 측면 Drawer / 중앙 Modal). */
export type OverlayKind = "drawer" | "modal";

/** 오버레이: 페이지 위에 겹쳐 뜨는 컨테이너(드로어·모달). */
export type AppOverlay = {
  id: string;
  name: string;
  kind: OverlayKind;
  sections: AppSection[];
  /** 드로어 너비(px). 모달은 무시. */
  widthPx: number;
};

/** 앱 스코프 변수(파라미터) 유형. 위젯을 명시적으로 배선하는 값. */
export type VariableType = "string" | "number" | "boolean";

/**
 * 선언된 앱 변수. 컨트롤 위젯이 값을 쓰고, 데이터 위젯이 읽어 필터링한다.
 * 객체 타입에 종속되지 않아 서로 다른 위젯을 가로질러 배선할 수 있다.
 */
export type AppVariable = {
  id: string;
  name: string;
  type: VariableType;
  /** 초기값. null이면 미설정(전체). */
  defaultValue: string | number | boolean | null;
};

export type AppDefinition = {
  name: string;
  purpose: string;
  theme: AppTheme;
  shell: AppShell;
  header: AppHeader;
  /** Legacy representative page alias. Mirrors the default page in `pages`. */
  page: AppPage;
  pages: AppPage[];
  overlays: AppOverlay[];
  /** 앱 스코프 변수(파라미터). 위젯 간 명시적 배선. */
  variables: AppVariable[];
  /** 저장 시각 (published evidence). */
  savedAt: string | null;
  /** 저장 시 부여되는 버전. */
  version: number;
};

/** FORMATTING 배경 스와치 (Palantir: 무색 + 파스텔/블루프린트 톤). */
export const BACKGROUND_SWATCHES: ReadonlyArray<{
  id: string;
  value: string;
  label: string;
}> = [
  { id: "none", value: "transparent", label: "없음" },
  { id: "gray", value: "#f0f2f5", label: "회색" },
  { id: "blue", value: "#eaf1fb", label: "파랑" },
  { id: "green", value: "#eaf6ee", label: "초록" },
  { id: "amber", value: "#fbf3e4", label: "앰버" },
  { id: "violet", value: "#f1ecfb", label: "보라" },
];

export const SECTION_PADDINGS: ReadonlyArray<{
  id: SectionPadding;
  label: string;
  px: string;
}> = [
  { id: "none", label: "없음", px: "0" },
  { id: "compact", label: "컴팩트", px: "12px" },
  { id: "regular", label: "보통", px: "20px" },
  { id: "large", label: "넓게", px: "32px" },
];

export const SECTION_BORDERS: ReadonlyArray<{
  id: SectionBorder;
  label: string;
}> = [
  { id: "none", label: "없음" },
  { id: "bordered", label: "테두리" },
  { id: "shadow", label: "그림자" },
];

export const SECTION_LAYOUTS: ReadonlyArray<{
  id: SectionLayout;
  label: string;
  description: string;
}> = [
  { id: "flow", label: "플로우", description: "위젯을 세로로 쌓습니다." },
  { id: "columns", label: "컬럼", description: "위젯을 좌우로 배치합니다." },
  {
    id: "rows",
    label: "로우",
    description: "위젯을 가로 스크롤 행으로 배치합니다.",
  },
  {
    id: "toolbar",
    label: "툴바",
    description: "작은 위젯(메트릭·버튼)을 가로 툴바로 배치합니다.",
  },
  { id: "tabs", label: "탭", description: "각 위젯을 탭 패널로 분리합니다." },
];

/** 레거시 builder 레이아웃 템플릿 팝오버 항목. */
export const LAYOUT_TEMPLATES: ReadonlyArray<{
  id: string;
  label: string;
  columns: number;
}> = [
  { id: "details", label: "상세", columns: 1 },
  { id: "grid", label: "그리드", columns: 2 },
  { id: "inbox", label: "인박스", columns: 3 },
  { id: "overview", label: "개요", columns: 2 },
  { id: "settings", label: "설정", columns: 1 },
];

export type PaletteItem = {
  kind: WidgetKind | string;
  label: string;
  description: string;
  group: "core" | "widget";
  isAvailable: boolean;
};

export const WIDGET_LABELS: Record<WidgetKind, string> = {
  objectTable: "객체 테이블",
  objectList: "객체 목록",
  objectDetail: "객체 상세",
  objectSetTitle: "객체 세트 제목",
  objectLinks: "관계 순회",
  links: "링크",
  metricCard: "메트릭 카드",
  barChart: "막대 차트",
  pieChart: "파이 차트",
  timeline: "타임라인",
  kanban: "칸반 보드",
  calendar: "업무 캘린더",
  statusTracker: "상태 추적기",
  pivotTable: "피벗 테이블",
  filterList: "필터 목록",
  objectDropdown: "객체 드롭다운",
  searchBar: "검색",
  stringSelector: "문자열 선택",
  buttonGroup: "버튼 그룹",
  actionForm: "액션 폼",
  markdown: "마크다운",
  sectionHeader: "섹션 헤더",
  divider: "구분선",
  aipChatbot: "AIP 챗봇",
};

export const WIDGET_PALETTE: readonly PaletteItem[] = [
  {
    kind: "objectTable",
    label: WIDGET_LABELS.objectTable,
    description: "객체 타입을 컴팩트 테이블로 렌더링합니다.",
    group: "core",
    isAvailable: true,
  },
  {
    kind: "objectDetail",
    label: WIDGET_LABELS.objectDetail,
    description: "선택한 객체의 속성을 key-value로 표시합니다.",
    group: "core",
    isAvailable: true,
  },
  {
    kind: "actionForm",
    label: WIDGET_LABELS.actionForm,
    description: "객체에 검증 가능한 액션을 실행합니다.",
    group: "core",
    isAvailable: true,
  },
  {
    kind: "metricCard",
    label: WIDGET_LABELS.metricCard,
    description: "집계 값을 하이라이트 카드로 보여줍니다.",
    group: "widget",
    isAvailable: false,
  },
  {
    kind: "barChart",
    label: WIDGET_LABELS.barChart,
    description: "속성 분포를 막대 차트로 시각화합니다.",
    group: "widget",
    isAvailable: false,
  },
  {
    kind: "objectDropdown",
    label: WIDGET_LABELS.objectDropdown,
    description: "객체 세트를 변수로 선택합니다.",
    group: "widget",
    isAvailable: false,
  },
  {
    kind: "buttonGroup",
    label: WIDGET_LABELS.buttonGroup,
    description: "라우팅·액션 버튼을 그룹으로 묶습니다.",
    group: "widget",
    isAvailable: false,
  },
  {
    kind: "aipChatbot",
    label: WIDGET_LABELS.aipChatbot,
    description: "온톨로지 컨텍스트 기반 대화형 위젯입니다.",
    group: "widget",
    isAvailable: false,
  },
];

const STORAGE_KEY = "foundry-lite:workshop:app-definition";

export const WORKSHOP_APP_RESOURCE_TYPE = "workshop_app";
export const WORKSHOP_APP_SOURCE_SURFACE = "workshop";
export const WORKSHOP_APP_SOURCE_REF = "default-workshop-app";
export const WORKSHOP_APP_METADATA_KIND =
  "foundry-lite.workshop.app-definition";
/** v3 = reusable SaaS shell/theme + responsive spans + operational visualizations. */
export const WORKSHOP_APP_METADATA_SCHEMA_VERSION = 3;

export const DEFAULT_APP_THEME: AppTheme = {
  preset: "ocean",
  brandName: "Foundry-lite",
  logoText: "FL",
};

export const DEFAULT_APP_SHELL: AppShell = {
  navigation: "sidebar",
  density: "comfortable",
  pageWidth: "wide",
  showContextBar: true,
};

let idCounter = 0;
export function createId(prefix: string): string {
  idCounter += 1;
  return `${prefix}-${Math.random().toString(16).slice(2, 8)}${idCounter.toString(16)}`;
}

export function defaultSectionStyle(): SectionStyle {
  return { background: "transparent", padding: "regular", border: "none" };
}

export function createSection(
  title: string,
  layout: SectionLayout = "flow",
  span: AppSection["span"] = 12,
): AppSection {
  return {
    id: createId("sec"),
    title,
    layout,
    style: defaultSectionStyle(),
    span,
    widgets: [],
  };
}

/** 헤더 좌/중/우 슬롯을 빈 섹션으로 생성. */
export function createHeaderSlots(): AppHeaderSlots {
  return {
    left: createSection("헤더 좌측"),
    center: createSection("헤더 중앙"),
    right: createSection("헤더 우측"),
  };
}

export function createPage(name: string, isDefault: boolean): AppPage {
  return {
    id: createId("page"),
    name,
    pageId: name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, ""),
    isDefault,
    backgroundColor: "transparent",
    layoutDirection: "columns",
    intent: "workbench",
    sections: [createSection("Section")],
  };
}

export function createWidget(
  kind: WidgetKind,
  configOrObjectApiName: WidgetConfig | string | null = {},
  actionApiName: string | null = null,
): AppWidget {
  const config =
    typeof configOrObjectApiName === "string" || configOrObjectApiName === null
      ? { objectApiName: configOrObjectApiName, actionApiName }
      : configOrObjectApiName;
  return widgetWithAliases({ id: createId("w"), kind, config });
}

export function createOverlay(
  name: string,
  kind: OverlayKind = "drawer",
): AppOverlay {
  return {
    id: createId("ovl"),
    name,
    kind,
    sections: [createSection("Section")],
    widthPx: 420,
  };
}

export function createVariable(
  name: string,
  type: VariableType = "string",
): AppVariable {
  return { id: createId("var"), name, type, defaultValue: null };
}

/** 변수 기본값으로 런타임 초기 변수 맵을 만든다. */
export function initialVariableValues(
  variables: readonly AppVariable[],
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const variable of variables) values[variable.id] = variable.defaultValue;
  return values;
}

export function createEmptyAppDefinition(): AppDefinition {
  const page = createPage("Home", true);
  return {
    name: "새 Workshop 앱",
    purpose: "온톨로지 객체·액션을 조합한 운영 앱",
    theme: { ...DEFAULT_APP_THEME },
    shell: { ...DEFAULT_APP_SHELL },
    header: {
      visible: true,
      title: "새 Workshop 앱",
      slots: createHeaderSlots(),
    },
    page,
    pages: [page],
    overlays: [],
    variables: [],
    savedAt: null,
    version: 0,
  };
}

/** Keep the legacy representative page alias synchronized with canonical pages. */
export function withAppPages(
  definition: AppDefinition,
  pages: AppPage[],
): AppDefinition {
  const representative =
    pages.find((page) => page.isDefault) ??
    pages.find((page) => page.id === definition.page.id) ??
    pages[0] ??
    definition.page;
  return { ...definition, page: representative, pages };
}

/** Replace one page while preserving the `page`/`pages` compatibility invariant. */
export function replaceAppPage(
  definition: AppDefinition,
  nextPage: AppPage,
): AppDefinition {
  const hasPage = definition.pages.some((page) => page.id === nextPage.id);
  const pages = hasPage
    ? definition.pages.map((page) =>
        page.id === nextPage.id ? nextPage : page,
      )
    : [...definition.pages, nextPage];
  return withAppPages({ ...definition, page: nextPage }, pages);
}

export function loadAppDefinition(): AppDefinition {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return createEmptyAppDefinition();
    const migrated = migrateAppDefinition(JSON.parse(raw));
    return migrated ?? createEmptyAppDefinition();
  } catch {
    return createEmptyAppDefinition();
  }
}

/** 보조 로컬 캐시: 서버 저장 실패·오프라인에서도 마지막 상태를 복구한다. */
export function saveAppDefinition(definition: AppDefinition): AppDefinition {
  const synchronized = replaceAppPage(definition, definition.page);
  const next: AppDefinition = {
    ...synchronized,
    savedAt: new Date().toISOString(),
    version: synchronized.version + 1,
  };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* localStorage 미지원 환경에서도 화면 상태는 유지 */
  }
  return next;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * 저장된 정의를 현재 스키마(v3)로 정규화한다.
 * v1/v2 contracts gain safe shell, theme, intent, and responsive-span defaults.
 */
export function migrateAppDefinition(value: unknown): AppDefinition | null {
  if (!isRecord(value)) return null;
  if (typeof value.name !== "string") return null;

  const headerSlots =
    isRecord(value.header) && isRecord(value.header.slots)
      ? migrateHeaderSlots(value.header.slots)
      : createHeaderSlots();
  const header: AppHeader = isRecord(value.header)
    ? {
        visible: value.header.visible !== false,
        title:
          typeof value.header.title === "string"
            ? value.header.title
            : value.name,
        slots: headerSlots,
      }
    : { visible: true, title: value.name, slots: headerSlots };

  const overlays = Array.isArray(value.overlays)
    ? value.overlays
        .map(migrateOverlay)
        .filter((overlay): overlay is AppOverlay => overlay !== null)
    : [];

  const variables = migrateVariables(value.variables);
  const theme = migrateTheme(value.theme, value.name);
  const shell = migrateShell(value.shell);

  // v2: pages[] 존재
  if (Array.isArray(value.pages)) {
    const pages = value.pages
      .map(migratePage)
      .filter((page): page is AppPage => page !== null);
    if (pages.length === 0) return null;
    const page = pages.find((candidate) => candidate.isDefault) ?? pages[0];
    return {
      name: value.name,
      purpose: typeof value.purpose === "string" ? value.purpose : "",
      theme,
      shell,
      header,
      page,
      pages,
      overlays,
      variables,
      savedAt: typeof value.savedAt === "string" ? value.savedAt : null,
      version: typeof value.version === "number" ? value.version : 0,
    };
  }

  // v1: 단일 page
  if (isRecord(value.page)) {
    const migratedPage = migratePage(value.page);
    if (!migratedPage) return null;
    const page = { ...migratedPage, isDefault: true };
    return {
      name: value.name,
      purpose: typeof value.purpose === "string" ? value.purpose : "",
      theme,
      shell,
      header,
      page,
      pages: [page],
      overlays,
      variables,
      savedAt: typeof value.savedAt === "string" ? value.savedAt : null,
      version: typeof value.version === "number" ? value.version : 0,
    };
  }

  return null;
}

function migrateVariables(value: unknown): AppVariable[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(migrateVariable)
    .filter((variable): variable is AppVariable => variable !== null);
}

function migrateVariable(value: unknown): AppVariable | null {
  if (!isRecord(value)) return null;
  const type: VariableType =
    value.type === "number"
      ? "number"
      : value.type === "boolean"
        ? "boolean"
        : "string";
  const raw = value.defaultValue;
  const defaultValue =
    typeof raw === "string" ||
    typeof raw === "number" ||
    typeof raw === "boolean"
      ? raw
      : null;
  return {
    id: typeof value.id === "string" ? value.id : createId("var"),
    name: typeof value.name === "string" ? value.name : "변수",
    type,
    defaultValue,
  };
}

function migrateHeaderSlots(value: Record<string, unknown>): AppHeaderSlots {
  const slot = (raw: unknown, title: string): AppSection =>
    migrateSection(raw) ?? createSection(title);
  return {
    left: slot(value.left, "헤더 좌측"),
    center: slot(value.center, "헤더 중앙"),
    right: slot(value.right, "헤더 우측"),
  };
}

function migrateOverlay(value: unknown): AppOverlay | null {
  if (!isRecord(value)) return null;
  const rawSections = Array.isArray(value.sections) ? value.sections : [];
  const sections = rawSections
    .map(migrateSection)
    .filter((section): section is AppSection => section !== null);
  return {
    id: typeof value.id === "string" ? value.id : createId("ovl"),
    name: typeof value.name === "string" ? value.name : "Overlay",
    kind: value.kind === "modal" ? "modal" : "drawer",
    widthPx: typeof value.widthPx === "number" ? value.widthPx : 420,
    sections: sections.length > 0 ? sections : [createSection("Section")],
  };
}

function migratePage(value: unknown): AppPage | null {
  if (!isRecord(value)) return null;
  const rawSections = Array.isArray(value.sections) ? value.sections : [];
  const sections = rawSections
    .map(migrateSection)
    .filter((section): section is AppSection => section !== null);
  return {
    id: typeof value.id === "string" ? value.id : createId("page"),
    name: typeof value.name === "string" ? value.name : "Page",
    pageId: typeof value.pageId === "string" ? value.pageId : "page",
    isDefault: value.isDefault === true,
    backgroundColor:
      typeof value.backgroundColor === "string"
        ? value.backgroundColor
        : "transparent",
    layoutDirection: value.layoutDirection === "rows" ? "rows" : "columns",
    intent: isPageIntent(value.intent) ? value.intent : "workbench",
    sections: sections.length > 0 ? sections : [createSection("Section")],
  };
}

function migrateSection(value: unknown): AppSection | null {
  if (!isRecord(value)) return null;
  const rawWidgets = Array.isArray(value.widgets) ? value.widgets : [];
  const widgets = rawWidgets
    .map(migrateWidget)
    .filter((widget): widget is AppWidget => widget !== null);
  const layout = isSectionLayout(value.layout) ? value.layout : "flow";
  const style = isRecord(value.style)
    ? {
        background:
          typeof value.style.background === "string"
            ? value.style.background
            : "transparent",
        padding: isSectionPadding(value.style.padding)
          ? value.style.padding
          : "regular",
        border: isSectionBorder(value.style.border)
          ? value.style.border
          : "none",
      }
    : defaultSectionStyle();
  return {
    id: typeof value.id === "string" ? value.id : createId("sec"),
    title: typeof value.title === "string" ? value.title : "Section",
    layout,
    style,
    span: isSectionSpan(value.span) ? value.span : 12,
    widgets,
  };
}

function migrateTheme(value: unknown, appName: string): AppTheme {
  const record = isRecord(value) ? value : {};
  return {
    preset: isThemePreset(record.preset) ? record.preset : DEFAULT_APP_THEME.preset,
    brandName: typeof record.brandName === "string" ? record.brandName : appName,
    logoText: typeof record.logoText === "string" ? record.logoText.slice(0, 3) : appName.slice(0, 2),
  };
}

function migrateShell(value: unknown): AppShell {
  const record = isRecord(value) ? value : {};
  return {
    navigation: record.navigation === "topbar" ? "topbar" : "sidebar",
    density: record.density === "compact" ? "compact" : "comfortable",
    pageWidth: record.pageWidth === "contained" ? "contained" : "wide",
    showContextBar: record.showContextBar !== false,
  };
}

function isThemePreset(value: unknown): value is AppThemePreset {
  return ["ocean", "indigo", "emerald", "amber", "graphite"].includes(String(value));
}

function isPageIntent(value: unknown): value is AppPage["intent"] {
  return ["workbench", "overview", "records", "governance", "evidence", "relationships"].includes(String(value));
}

function isSectionSpan(value: unknown): value is AppSection["span"] {
  return [3, 4, 6, 8, 9, 12].includes(Number(value));
}

function migrateWidget(value: unknown): AppWidget | null {
  if (!isRecord(value)) return null;
  if (typeof value.kind !== "string") return null;
  // v2: config 존재
  if (isRecord(value.config)) {
    return widgetWithAliases({
      id: typeof value.id === "string" ? value.id : createId("w"),
      kind: value.kind as WidgetKind,
      config: value.config as WidgetConfig,
    });
  }
  // v1: objectApiName/actionApiName 평면 → config로 승격
  const config: WidgetConfig = {};
  if (typeof value.objectApiName === "string")
    config.objectApiName = value.objectApiName;
  if (typeof value.actionApiName === "string")
    config.actionApiName = value.actionApiName;
  return widgetWithAliases({
    id: typeof value.id === "string" ? value.id : createId("w"),
    kind: value.kind as WidgetKind,
    config,
  });
}

function widgetWithAliases(widget: {
  id: string;
  kind: WidgetKind;
  config: WidgetConfig;
}): AppWidget {
  return {
    ...widget,
    objectApiName: widget.config.objectApiName ?? null,
    actionApiName: widget.config.actionApiName ?? null,
  };
}

function isSectionLayout(value: unknown): value is SectionLayout {
  return (
    value === "flow" ||
    value === "columns" ||
    value === "rows" ||
    value === "toolbar" ||
    value === "tabs"
  );
}

function isSectionPadding(value: unknown): value is SectionPadding {
  return (
    value === "none" ||
    value === "compact" ||
    value === "regular" ||
    value === "large"
  );
}

function isSectionBorder(value: unknown): value is SectionBorder {
  return value === "none" || value === "bordered" || value === "shadow";
}

export function findWorkshopAppResource(
  resources: readonly ResourceItem[],
  sourceRef: string = WORKSHOP_APP_SOURCE_REF,
): ResourceItem | null {
  return (
    resources.find(
      (resource) =>
        resource.sourceSurface === WORKSHOP_APP_SOURCE_SURFACE &&
        resource.sourceRef === sourceRef &&
        resource.resourceType === WORKSHOP_APP_RESOURCE_TYPE,
    ) ?? null
  );
}

/** 저장된 리소스 metadata → AppDefinition (v1/v2 모두 마이그레이션해 수용). */
export function appDefinitionFromResource(
  resource: ResourceItem,
): AppDefinition | null {
  const metadata = resource.metadata;
  if (metadata.kind !== WORKSHOP_APP_METADATA_KIND) return null;
  return migrateAppDefinition(metadata.definition);
}

export function resourceMetadataForAppDefinition(
  definition: AppDefinition,
): Record<string, unknown> {
  return {
    kind: WORKSHOP_APP_METADATA_KIND,
    schemaVersion: WORKSHOP_APP_METADATA_SCHEMA_VERSION,
    definition,
    publishedVersion: definition.version,
    publishedAt: definition.savedAt,
  };
}

/** 앱 전체 또는 단일 페이지 위젯을 flat하게 반환. */
export function collectWidgets(target: AppDefinition | AppPage): AppWidget[] {
  if ("pages" in target) {
    return target.pages.flatMap((page) =>
      page.sections.flatMap((section) => section.widgets),
    );
  }
  return collectPageWidgets(target);
}

export function collectPageWidgets(page: AppPage): AppWidget[] {
  return page.sections.flatMap((section) => section.widgets);
}

/** 최소 업무 앱 성립 여부: 테이블/리스트 + 상세 + 액션이 배치됨. */
export function isRunnableApp(target: AppDefinition | AppPage): boolean {
  const kinds = new Set(collectWidgets(target).map((widget) => widget.kind));
  const hasList = kinds.has("objectTable") || kinds.has("objectList");
  const hasDetail = kinds.has("objectDetail");
  const hasAction = kinds.has("actionForm") || kinds.has("buttonGroup");
  return hasList && hasDetail && hasAction;
}

/** 레거시 builder capability 카운트. */
export function widgetCounts(page: AppPage): Record<WidgetKind, number> {
  const counts = Object.fromEntries(
    Object.keys(WIDGET_LABELS).map((kind) => [kind, 0]),
  ) as Record<WidgetKind, number>;
  for (const widget of collectPageWidgets(page)) counts[widget.kind] += 1;
  return counts;
}

export function statusIntentOf(status: unknown): StatusIntent {
  if (status === "APPROVED" || status === "ACTIVE") return "success";
  if (status === "REVIEW" || status === "PENDING") return "warning";
  if (status === "REJECTED" || status === "BLOCKED") return "danger";
  if (status === "NEW") return "info";
  return "neutral";
}

const NUMERIC_KEYS = new Set([
  "amount",
  "margin",
  "riskScore",
  "revenue",
  "population",
  "beds",
]);

/** 셀·속성 값 표시 포맷. */
export function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString("en-US");
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function isNumericProperty(name: string, value: unknown): boolean {
  return typeof value === "number" || NUMERIC_KEYS.has(name);
}

/** 테이블 컬럼 후보: config 지정 → 객체 뷰 속성 순서 → 첫 객체 키. */
export function tableColumnNames(
  objectView: FoundryLiteOntologyObjectView | null,
  objects: readonly GenericObject[],
  configured?: string[],
): string[] {
  if (configured && configured.length > 0) return configured;
  if (objectView && objectView.properties.length > 0) {
    return objectView.properties.map((property) => property.apiName);
  }
  const first = objects[0];
  return first ? Object.keys(first.properties) : [];
}

/** 객체 타이틀: title/name 속성 → primary key → objectId. */
export function objectTitleOf(
  object: GenericObject,
  objectView: FoundryLiteOntologyObjectView | null,
): string {
  const props = object.properties;
  for (const key of ["title", "name", "displayName"]) {
    const value = props[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  const pk = objectView?.primaryKeyProperty;
  if (pk && props[pk] !== undefined) return String(props[pk]);
  return object.objectId;
}
