import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import {
  Activity,
  BarChart3,
  BriefcaseBusiness,
  CheckCircle2,
  CircleUserRound,
  Database,
  GitBranch,
  LayoutDashboard,
  Menu,
  ShieldCheck,
  Wifi,
  X,
  type LucideIcon,
} from "lucide-react";
import { useState, type CSSProperties } from "react";

import { cn } from "@/lib/utils";

import {
  initialVariableValues,
  type AppDefinition,
  type AppOverlay,
  type AppPage,
  type AppSection,
  type AppThemePreset,
  type AppWidget,
} from "../lib/app-model";
import {
  RuntimeDispatchProvider,
  RuntimeStateProvider,
  useRuntimeDispatch,
  useRuntimeState,
  useRuntimeStateReducer,
} from "../lib/runtime-state";
import { WorkshopRuntimeApplicationProvider } from "./runtime-application-context";
import { WidgetRenderer } from "./widgets/registry";

interface RuntimeModeProps {
  definition: AppDefinition;
  objectViewsByApiName: Record<string, FoundryLiteOntologyObjectView>;
  actionViews: readonly FoundryLiteOntologyActionView[];
  applicationId?: string | null;
}

type RuntimeBindings = Pick<RuntimeModeProps, "objectViewsByApiName" | "actionViews">;

/** The canonical Workshop graph rendered as a responsive operational SaaS surface. */
export function RuntimeMode({
  definition,
  objectViewsByApiName,
  actionViews,
  applicationId = null,
}: RuntimeModeProps) {
  const [state, dispatch] = useRuntimeStateReducer(initialVariableValues(definition.variables));
  const defaultPage = definition.pages.find((page) => page.isDefault) ?? definition.page;
  const [selectedPageId, setSelectedPageId] = useState(defaultPage.id);
  const [isMobileNavigationOpen, setIsMobileNavigationOpen] = useState(false);
  const selectedPage = definition.pages.find((page) => page.id === selectedPageId) ?? defaultPage;
  const bindings = { objectViewsByApiName, actionViews };
  const isSidebar = definition.shell.navigation === "sidebar";
  return (
    <WorkshopRuntimeApplicationProvider applicationId={applicationId}>
      <RuntimeStateProvider value={state}>
        <RuntimeDispatchProvider value={dispatch}>
          <main
            aria-label="Workshop runtime canvas"
            className={cn(
              "flex h-full min-h-0 overflow-hidden bg-[var(--workshop-canvas)] text-[var(--workshop-ink)]",
              definition.shell.density === "compact" && "text-[13px]",
            )}
            style={themeVariables(definition.theme.preset)}
          >
            {isSidebar ? (
              <RuntimeSidebar definition={definition} selectedPage={selectedPage} onSelect={setSelectedPageId} />
            ) : null}
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
              <RuntimeMobileHeader
                definition={definition}
                isOpen={isMobileNavigationOpen}
                onToggle={() => setIsMobileNavigationOpen((current) => !current)}
              />
              {isMobileNavigationOpen ? (
                <RuntimeMobileNavigation
                  pages={definition.pages}
                  selectedPage={selectedPage}
                  onSelect={(pageId) => {
                    setSelectedPageId(pageId);
                    setIsMobileNavigationOpen(false);
                  }}
                />
              ) : null}
              <div className="min-h-0 flex-1 overflow-auto">
                <RuntimeContextBar definition={definition} page={selectedPage} />
                {!isSidebar ? (
                  <RuntimeTopNavigation pages={definition.pages} selectedPage={selectedPage} onSelect={setSelectedPageId} />
                ) : null}
                <RuntimeHeaderSlots definition={definition} bindings={bindings} />
                <div
                  className={cn("mx-auto w-full", definition.shell.pageWidth === "contained" && "max-w-[1280px]")}
                  style={{ backgroundColor: selectedPage.backgroundColor }}
                >
                  <RuntimeSections sections={selectedPage.sections} bindings={bindings} />
                </div>
                <RuntimeOverlay overlays={definition.overlays} bindings={bindings} />
              </div>
            </div>
          </main>
        </RuntimeDispatchProvider>
      </RuntimeStateProvider>
    </WorkshopRuntimeApplicationProvider>
  );
}

function RuntimeSidebar({ definition, selectedPage, onSelect }: {
  definition: AppDefinition;
  selectedPage: AppPage;
  onSelect: (pageId: string) => void;
}) {
  return (
    <aside className="hidden w-[248px] shrink-0 flex-col border-r border-white/10 bg-[var(--workshop-nav)] text-white lg:flex">
      <div className="px-5 pt-5 pb-4">
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-xl bg-[var(--workshop-accent)] text-[12px] font-black tracking-tight shadow-lg shadow-black/15">
            {definition.theme.logoText || definition.name.slice(0, 2)}
          </span>
          <div className="min-w-0">
            <div className="truncate text-[13px] font-semibold">{definition.theme.brandName}</div>
            <div className="mt-0.5 text-[9px] font-semibold tracking-[.16em] text-white/45 uppercase">Operational workspace</div>
          </div>
        </div>
        <p className="mt-4 line-clamp-2 text-[11px] leading-5 text-white/55">{definition.purpose}</p>
      </div>
      <nav aria-label="업무 앱 페이지" className="min-h-0 flex-1 space-y-1 overflow-auto px-3 py-2">
        <div className="px-2 pb-2 text-[9px] font-bold tracking-[.16em] text-white/35 uppercase">업무 공간</div>
        {definition.pages.map((page) => {
          const Icon = pageIcon(page.intent);
          const isSelected = page.id === selectedPage.id;
          return (
            <button
              key={page.id}
              type="button"
              onClick={() => onSelect(page.id)}
              className={cn(
                "group flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-[12px] font-medium transition",
                isSelected ? "bg-white text-[var(--workshop-nav)] shadow-sm" : "text-white/65 hover:bg-white/8 hover:text-white",
              )}
            >
              <Icon className={cn("size-4", isSelected ? "text-[var(--workshop-accent)]" : "text-white/45 group-hover:text-white/75")} />
              <span className="min-w-0 flex-1 truncate">{page.name}</span>
              {page.isDefault ? <span className="size-1.5 rounded-full bg-[var(--workshop-accent)]" /> : null}
            </button>
          );
        })}
      </nav>
      <div className="border-t border-white/10 p-4">
        <div className="flex items-center gap-2 text-[10px] text-white/55"><ShieldCheck className="size-3.5 text-emerald-300" />권한과 Action이 보호됨</div>
        <div className="mt-3 flex items-center gap-2 rounded-lg bg-white/6 px-2.5 py-2">
          <CircleUserRound className="size-5 text-white/60" />
          <div className="min-w-0"><div className="truncate text-[10px] font-semibold text-white/85">현재 사용자</div><div className="text-[9px] text-white/40">업무 권한 적용 중</div></div>
        </div>
      </div>
    </aside>
  );
}

function RuntimeMobileHeader({ definition, isOpen, onToggle }: {
  definition: AppDefinition;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex h-14 shrink-0 items-center gap-3 border-b border-[var(--workshop-line)] bg-white px-4 lg:hidden">
      <button type="button" aria-label={isOpen ? "탐색 닫기" : "탐색 열기"} onClick={onToggle} className="rounded-lg border border-[var(--workshop-line)] p-2">{isOpen ? <X className="size-4" /> : <Menu className="size-4" />}</button>
      <span className="flex size-8 items-center justify-center rounded-lg bg-[var(--workshop-nav)] text-[10px] font-bold text-white">{definition.theme.logoText}</span>
      <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">{definition.theme.brandName}</span>
      <span className="flex items-center gap-1 text-[9px] font-semibold text-emerald-700"><Wifi className="size-3" /> LIVE</span>
    </div>
  );
}

function RuntimeMobileNavigation({ pages, selectedPage, onSelect }: {
  pages: AppPage[];
  selectedPage: AppPage;
  onSelect: (pageId: string) => void;
}) {
  return (
    <nav aria-label="모바일 업무 앱 페이지" className="absolute inset-x-0 top-14 z-40 border-b border-[var(--workshop-line)] bg-white p-3 shadow-xl lg:hidden">
      <div className="grid grid-cols-2 gap-2">
        {pages.map((page) => <button key={page.id} type="button" onClick={() => onSelect(page.id)} className={cn("rounded-lg border p-3 text-left text-[11px] font-medium", page.id === selectedPage.id ? "border-[var(--workshop-accent)] bg-[var(--workshop-accent-soft)] text-[var(--workshop-accent)]" : "border-[var(--workshop-line)]")}>{page.name}</button>)}
      </div>
    </nav>
  );
}

function RuntimeContextBar({ definition, page }: { definition: AppDefinition; page: AppPage }) {
  const state = useRuntimeState();
  if (!definition.shell.showContextBar) return null;
  const filterCount = Object.keys(state.filters).length + (state.searchText ? 1 : 0);
  return (
    <header className="border-b border-[var(--workshop-line)] bg-white px-4 py-4 md:px-6">
      <div className="grid gap-3 md:flex md:flex-wrap md:items-center md:gap-x-5 md:gap-y-3">
        <div className="min-w-0 md:flex-1">
          <div className="flex items-center gap-2 text-[9px] font-bold tracking-[.14em] text-[var(--workshop-accent)] uppercase"><Activity className="size-3" /> Live work pulse</div>
          <h1 className="mt-1 truncate text-[20px] font-semibold tracking-[-.025em] text-[var(--workshop-ink)] md:text-[24px]">{page.name}</h1>
          <p className="mt-1 max-w-3xl truncate text-[11px] text-[#748195]">{definition.purpose}</p>
        </div>
        <div className="grid grid-cols-3 items-center gap-2 md:flex md:flex-wrap">
          <Pulse label="필터" value={filterCount ? `${filterCount}개 적용` : "전체"} />
          <Pulse label="선택" value={state.selectedObjectId ? "업무 선택됨" : "선택 전"} />
          <Pulse label="동기화" value={`v${definition.version}`} isLive />
        </div>
      </div>
    </header>
  );
}

function Pulse({ label, value, isLive = false }: { label: string; value: string; isLive?: boolean }) {
  return (
    <div className="min-w-0 rounded-lg border border-[var(--workshop-line)] bg-[var(--workshop-subtle)] px-2.5 py-2 md:min-w-[88px]">
      <div className="text-[8px] font-bold tracking-[.12em] text-[#8994a5] uppercase">{label}</div>
      <div className="mt-1 flex items-center gap-1.5 text-[10px] font-semibold text-[#465468]">{isLive ? <span className="size-1.5 rounded-full bg-emerald-500" /> : null}{value}</div>
    </div>
  );
}

function RuntimeTopNavigation({ pages, selectedPage, onSelect }: { pages: AppPage[]; selectedPage: AppPage; onSelect: (pageId: string) => void }) {
  return <nav aria-label="업무 앱 페이지" className="hidden gap-1 overflow-auto border-b border-[var(--workshop-line)] bg-white px-6 lg:flex">{pages.map((page) => <button key={page.id} type="button" onClick={() => onSelect(page.id)} className={cn("whitespace-nowrap border-b-2 px-3 py-3 text-[11px] font-semibold", page.id === selectedPage.id ? "border-[var(--workshop-accent)] text-[var(--workshop-accent)]" : "border-transparent text-[#657386] hover:text-[var(--workshop-ink)]")}>{page.name}</button>)}</nav>;
}

function RuntimeHeaderSlots({ definition, bindings }: { definition: AppDefinition; bindings: RuntimeBindings }) {
  if (!definition.header.visible) return null;
  const slots = Object.values(definition.header.slots).filter((section) => section.widgets.length > 0);
  if (slots.length === 0) return null;
  return <div className="grid gap-3 border-b border-[var(--workshop-line)] bg-white px-4 py-3 lg:grid-cols-3">{slots.map((section) => <RuntimeSection key={section.id} section={section} bindings={bindings} isHeader />)}</div>;
}

function RuntimeSections({ sections, bindings }: { sections: AppSection[]; bindings: RuntimeBindings }) {
  return <div className="grid content-start gap-4 p-4 md:p-6 lg:grid-cols-12">{sections.map((section) => <div key={section.id} className={cn("min-w-0", spanClass(section.span))}><RuntimeSection section={section} bindings={bindings} /></div>)}</div>;
}

function RuntimeSection({ section, bindings, isHeader = false }: { section: AppSection; bindings: RuntimeBindings; isHeader?: boolean }) {
  const [activeTab, setActiveTab] = useState(0);
  const widgets = section.layout === "tabs" ? section.widgets.slice(activeTab, activeTab + 1) : section.widgets;
  return (
    <section aria-label={section.title} className={cn(sectionClass(section), isHeader && "border-0 bg-transparent p-0 shadow-none")} style={{ backgroundColor: isHeader ? "transparent" : section.style.background }}>
      {section.title && !isHeader ? <h2 className="mb-3 text-[10px] font-bold tracking-[.11em] text-[#657386] uppercase">{section.title}</h2> : null}
      {section.layout === "tabs" ? <RuntimeTabs widgets={section.widgets} active={activeTab} onChange={setActiveTab} /> : null}
      <div className={widgetLayoutClass(section.layout)}>{widgets.map((widget) => <RuntimeWidget key={widget.id} widget={widget} bindings={bindings} />)}</div>
    </section>
  );
}

function RuntimeTabs({ widgets, active, onChange }: { widgets: AppWidget[]; active: number; onChange: (index: number) => void }) {
  return <div role="tablist" className="mb-3 flex gap-1 border-b border-[var(--workshop-line)]">{widgets.map((widget, index) => <button key={widget.id} role="tab" aria-selected={active === index} onClick={() => onChange(index)} className={cn("border-b-2 px-3 py-2 text-[10px] font-semibold", active === index ? "border-[var(--workshop-accent)] text-[var(--workshop-accent)]" : "border-transparent text-[#657386]")}>{widget.config.title || widget.kind}</button>)}</div>;
}

function RuntimeWidget({ widget, bindings }: { widget: AppWidget; bindings: RuntimeBindings }) {
  return <div className={cn("min-w-0", widgetHeightClass(widget.kind))} data-workshop-widget={widget.kind}><WidgetRenderer widget={widget} {...bindings} /></div>;
}

function RuntimeOverlay({ overlays, bindings }: { overlays: AppOverlay[]; bindings: RuntimeBindings }) {
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();
  const overlay = overlays.find((item) => item.id === state.openOverlayId);
  if (!overlay) return null;
  return (
    <div role="dialog" aria-modal="true" aria-label={overlay.name} className="fixed inset-0 z-50 flex justify-end bg-[#08111f]/55 p-3 backdrop-blur-sm">
      <div className={cn("h-full max-w-full overflow-auto rounded-2xl bg-[var(--workshop-canvas)] shadow-2xl", overlay.kind === "modal" && "m-auto max-h-[85vh]")} style={{ width: overlay.widthPx }}>
        <div className="sticky top-0 z-10 flex items-center border-b border-[var(--workshop-line)] bg-white px-4 py-3"><h2 className="text-[13px] font-semibold">{overlay.name}</h2><button type="button" className="ml-auto rounded-lg p-1.5 hover:bg-[var(--workshop-subtle)]" aria-label="오버레이 닫기" onClick={() => dispatch({ type: "closeOverlay" })}><X className="size-4" /></button></div>
        <RuntimeSections sections={overlay.sections} bindings={bindings} />
      </div>
    </div>
  );
}

function themeVariables(preset: AppThemePreset): CSSProperties {
  const palettes = {
    ocean: ["#0b7285", "#e6f6f8", "#0d2b36"],
    indigo: ["#4f46e5", "#eeedff", "#1e1b4b"],
    emerald: ["#087f5b", "#e7f7f0", "#12372a"],
    amber: ["#b45309", "#fff4df", "#422006"],
    graphite: ["#475569", "#eef2f6", "#111827"],
  } as const;
  const [accent, accentSoft, nav] = palettes[preset];
  return { "--workshop-accent": accent, "--workshop-accent-soft": accentSoft, "--workshop-nav": nav, "--workshop-ink": "#172033", "--workshop-canvas": "#f4f6f8", "--workshop-subtle": "#f7f9fb", "--workshop-line": "#dde3e9" } as CSSProperties;
}

function pageIcon(intent: AppPage["intent"]): LucideIcon {
  return { workbench: BriefcaseBusiness, overview: LayoutDashboard, records: Database, governance: CheckCircle2, evidence: ShieldCheck, relationships: GitBranch }[intent] ?? BarChart3;
}

function spanClass(span: AppSection["span"]): string {
  return { 3: "lg:col-span-3", 4: "lg:col-span-4", 6: "lg:col-span-6", 8: "lg:col-span-8", 9: "lg:col-span-9", 12: "lg:col-span-12" }[span];
}

function sectionClass(section: AppSection): string {
  const padding = { none: "p-0", compact: "p-2", regular: "p-3 md:p-4", large: "p-5 md:p-6" }[section.style.padding];
  const border = { none: "", bordered: "border border-[var(--workshop-line)]", shadow: "border border-[var(--workshop-line)] shadow-[0_10px_32px_-24px_rgba(15,23,42,.45)]" }[section.style.border];
  return cn("rounded-xl bg-white", padding, border);
}

function widgetLayoutClass(layout: AppSection["layout"]): string {
  if (layout === "columns") return "grid gap-3 md:grid-cols-2 xl:grid-cols-3";
  if (layout === "rows") return "grid auto-cols-[minmax(260px,1fr)] grid-flow-col gap-3 overflow-x-auto";
  if (layout === "toolbar") return "flex flex-wrap items-stretch gap-2";
  return "grid gap-3";
}

function widgetHeightClass(kind: AppWidget["kind"]): string {
  if (["objectTable", "objectList", "timeline", "barChart", "pieChart", "kanban", "calendar", "pivotTable"].includes(kind)) return "min-h-[300px]";
  if (["objectDetail", "actionForm", "aipChatbot"].includes(kind)) return "min-h-[220px]";
  return "min-h-[76px]";
}
