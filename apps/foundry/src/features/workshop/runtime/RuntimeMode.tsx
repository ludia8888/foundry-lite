import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import {
  Bell,
  BriefcaseBusiness,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  CircleUserRound,
  Database,
  GitBranch,
  LayoutDashboard,
  Menu,
  Search,
  ShieldCheck,
  Sparkles,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties } from "react";

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

/** Canonical Workshop graph rendered as a customer-facing operational SaaS. */
export function RuntimeMode({ definition, objectViewsByApiName, actionViews, applicationId = null }: RuntimeModeProps) {
  const [state, dispatch] = useRuntimeStateReducer(initialVariableValues(definition.variables));
  const defaultPage = definition.pages.find((page) => page.isDefault) ?? definition.page;
  const [selectedPageId, setSelectedPageId] = useState(defaultPage.id);
  const [isNavigationOpen, setIsNavigationOpen] = useState(false);
  const selectedPage = definition.pages.find((page) => page.id === selectedPageId) ?? defaultPage;
  const bindings = { objectViewsByApiName, actionViews };
  return (
    <WorkshopRuntimeApplicationProvider applicationId={applicationId} definition={definition}>
      <RuntimeStateProvider value={state}>
        <RuntimeDispatchProvider value={dispatch}>
          <main data-workshop-runtime aria-label={`${definition.name} 업무 앱`} className="flex h-full min-h-0 overflow-hidden bg-[var(--workshop-canvas)] font-sans text-[var(--workshop-ink)]" style={themeVariables(definition.theme.preset)}>
            <RuntimeSidebar definition={definition} selectedPage={selectedPage} onSelect={setSelectedPageId} />
            <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
              <MobileProductBar definition={definition} isOpen={isNavigationOpen} onToggle={() => setIsNavigationOpen((current) => !current)} />
              {isNavigationOpen ? <MobileNavigation pages={definition.pages} selectedPage={selectedPage} onSelect={(pageId) => { setSelectedPageId(pageId); setIsNavigationOpen(false); }} /> : null}
              <DesktopProductBar definition={definition} />
              <div className="min-h-0 flex-1 overflow-auto pb-20 lg:pb-0">
                <PageContext definition={definition} page={selectedPage} actionCount={actionViews.length} />
                {definition.shell.navigation === "topbar" ? <TopNavigation pages={definition.pages} selectedPage={selectedPage} onSelect={setSelectedPageId} /> : null}
                <RuntimeHeaderSlots definition={definition} bindings={bindings} />
                <div className={cn("mx-auto w-full", definition.shell.pageWidth === "contained" && "max-w-[1360px]")} style={{ backgroundColor: selectedPage.backgroundColor }}>
                  <RuntimeSections sections={selectedPage.sections} bindings={bindings} />
                </div>
                <RuntimeOverlay overlays={definition.overlays} bindings={bindings} />
              </div>
              <MobileBottomNavigation pages={definition.pages} selectedPage={selectedPage} onSelect={setSelectedPageId} />
            </div>
          </main>
        </RuntimeDispatchProvider>
      </RuntimeStateProvider>
    </WorkshopRuntimeApplicationProvider>
  );
}

function RuntimeSidebar({ definition, selectedPage, onSelect }: { definition: AppDefinition; selectedPage: AppPage; onSelect: (pageId: string) => void }) {
  return (
    <aside className="hidden w-[272px] shrink-0 flex-col bg-[var(--workshop-nav)] text-white lg:flex">
      <div className="px-5 pb-5 pt-6"><div className="flex items-center gap-3"><BrandMark definition={definition} /><div className="min-w-0"><div className="truncate text-[15px] font-bold tracking-[-.015em]">{definition.theme.brandName}</div><div className="mt-0.5 truncate text-[11px] text-white/48">{definition.presentation.chrome.workspaceLabel}</div></div></div></div>
      <nav aria-label="업무 화면" className="min-h-0 flex-1 space-y-1 overflow-auto px-3">
        <div className="px-3 pb-2 pt-1 text-[10px] font-bold tracking-[.12em] text-white/35">업무 메뉴</div>
        {definition.pages.map((page) => {
          const Icon = pageIcon(page.intent);
          const isSelected = page.id === selectedPage.id;
          return <button key={page.id} type="button" onClick={() => onSelect(page.id)} className={cn("group flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-[13px] font-semibold transition", isSelected ? "bg-white text-[var(--workshop-nav)] shadow-[0_8px_24px_-18px_rgba(0,0,0,.8)]" : "text-white/62 hover:bg-white/8 hover:text-white")}><Icon className={cn("size-[18px]", isSelected ? "text-[var(--workshop-accent)]" : "text-white/42")} /><span className="min-w-0 flex-1 truncate">{page.name}</span>{isSelected ? <ChevronRight className="size-4 text-[var(--workshop-accent)]" /> : null}</button>;
        })}
      </nav>
      <div className="m-3 rounded-2xl border border-white/10 bg-white/6 p-4"><div className="flex items-start gap-3"><ShieldCheck className="mt-0.5 size-5 text-emerald-300" /><div><div className="text-[12px] font-semibold text-white/88">안전한 업무 실행</div><p className="mt-1 text-[10px] leading-4 text-white/46">{definition.product.trustCenter.approvalStatement}</p></div></div></div>
      <div className="flex items-center gap-3 border-t border-white/10 px-5 py-4"><CircleUserRound className="size-8 text-white/58" /><div className="min-w-0 flex-1"><div className="text-[12px] font-semibold">{definition.presentation.chrome.userLabel}</div><div className="mt-0.5 text-[10px] text-white/42">로그인 역할에 맞는 업무만 표시</div></div></div>
    </aside>
  );
}

function BrandMark({ definition }: { definition: AppDefinition }) {
  return <span className="flex size-10 shrink-0 items-center justify-center rounded-[13px] bg-[var(--workshop-accent)] text-[12px] font-black tracking-tight text-white shadow-lg shadow-black/15">{definition.theme.logoText || definition.name.slice(0, 2)}</span>;
}

function DesktopProductBar({ definition }: { definition: AppDefinition }) {
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();
  const searchRef = useRef<HTMLInputElement>(null);
  const [openPanel, setOpenPanel] = useState<"help" | "notifications" | "user" | null>(null);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        event.stopImmediatePropagation();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", focusSearch, true);
    return () => window.removeEventListener("keydown", focusSearch, true);
  }, []);
  return (
    <div className="relative hidden h-16 shrink-0 items-center border-b border-[var(--workshop-line)] bg-white px-7 lg:flex">
      <label className="flex h-10 w-full max-w-[420px] items-center gap-2 rounded-xl bg-[var(--workshop-subtle)] px-3 text-[#7b8798]"><Search className="size-4" /><span className="sr-only">전체 업무 검색</span><input data-workshop-runtime-search ref={searchRef} value={state.searchText} onChange={(event) => dispatch({ type: "setSearch", text: event.target.value })} className="min-w-0 flex-1 bg-transparent text-[12px] text-[#334155] outline-none placeholder:text-[#8a96a6]" placeholder="고객, 업무, 담당자 검색" /><kbd className="rounded-md border border-[var(--workshop-line)] bg-white px-1.5 py-0.5 text-[9px]">⌘ K</kbd></label>
      <div className="ml-auto flex items-center gap-1"><ChromeButton icon={CircleHelp} label={definition.presentation.chrome.helpLabel} isExpanded={openPanel === "help"} onClick={() => setOpenPanel((current) => current === "help" ? null : "help")} /><ChromeButton icon={Bell} label={definition.presentation.chrome.notificationLabel} isExpanded={openPanel === "notifications"} onClick={() => setOpenPanel((current) => current === "notifications" ? null : "notifications")} /><button type="button" aria-expanded={openPanel === "user"} onClick={() => setOpenPanel((current) => current === "user" ? null : "user")} className="ml-2 flex items-center gap-2 rounded-xl px-2 py-1.5 hover:bg-[var(--workshop-subtle)]"><CircleUserRound className="size-7 text-[#64748b]" /><span className="text-[12px] font-semibold text-[#334155]">{definition.presentation.chrome.userLabel}</span></button></div>
      {openPanel ? <ProductPopover kind={openPanel} definition={definition} onClose={() => setOpenPanel(null)} /> : null}
    </div>
  );
}

function ChromeButton({ icon: Icon, label, isExpanded, onClick }: { icon: LucideIcon; label: string; isExpanded?: boolean; onClick?: () => void }) {
  return <button type="button" aria-label={label} aria-expanded={isExpanded} onClick={onClick} className="relative rounded-xl p-2.5 text-[#64748b] hover:bg-[var(--workshop-subtle)] hover:text-[var(--workshop-ink)]"><Icon className="size-[18px]" /></button>;
}

function MobileProductBar({ definition, isOpen, onToggle }: { definition: AppDefinition; isOpen: boolean; onToggle: () => void }) {
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false);
  return <div className="relative flex h-16 shrink-0 items-center gap-3 border-b border-[var(--workshop-line)] bg-white px-4 lg:hidden"><button type="button" aria-label={isOpen ? "메뉴 닫기" : "메뉴 열기"} onClick={onToggle} className="rounded-xl p-2 text-[#475569] hover:bg-[var(--workshop-subtle)]">{isOpen ? <X className="size-5" /> : <Menu className="size-5" />}</button><BrandMark definition={definition} /><span className="min-w-0 flex-1 truncate text-[14px] font-bold">{definition.theme.brandName}</span><ChromeButton icon={Bell} label={definition.presentation.chrome.notificationLabel} isExpanded={isNotificationsOpen} onClick={() => setIsNotificationsOpen((current) => !current)} />{isNotificationsOpen ? <ProductPopover kind="notifications" definition={definition} onClose={() => setIsNotificationsOpen(false)} /> : null}</div>;
}

function ProductPopover({ kind, definition, onClose }: { kind: "help" | "notifications" | "user"; definition: AppDefinition; onClose: () => void }) {
  const title = kind === "help" ? "업무 도움말" : kind === "notifications" ? "알림" : "사용자와 권한";
  return <section aria-label={title} className="absolute right-3 top-[58px] z-50 w-[min(380px,calc(100vw-24px))] rounded-2xl border border-[var(--workshop-line)] bg-white p-4 shadow-[0_24px_70px_-28px_rgba(15,23,42,.55)] lg:right-7 lg:top-[58px]"><div className="flex items-center gap-3"><h2 className="min-w-0 flex-1 text-[14px] font-bold text-[var(--workshop-ink)]">{title}</h2><button type="button" aria-label="닫기" onClick={onClose} className="rounded-lg p-1.5 text-[#7b8798] hover:bg-[var(--workshop-subtle)]"><X className="size-4" /></button></div>{kind === "help" ? <ProductHelp definition={definition} /> : kind === "notifications" ? <ProductNotifications /> : <ProductAudiences definition={definition} />}</section>;
}

function ProductHelp({ definition }: { definition: AppDefinition }) {
  return <div className="mt-3 space-y-3"><p className="text-[11px] leading-5 text-[#64748b]">이 서비스는 {definition.product.capabilityGroups.map((group) => group.name).join(" · ") || "회사의 업무 흐름"}을 한곳에서 처리합니다.</p><div className="rounded-xl bg-[var(--workshop-subtle)] p-3"><div className="flex items-center gap-2 text-[11px] font-bold"><ShieldCheck className="size-4 text-emerald-600" />정보와 변경 보호</div><p className="mt-1.5 text-[10px] leading-4 text-[#64748b]">{definition.product.trustCenter.accessStatement}</p><p className="mt-1 text-[10px] leading-4 text-[#64748b]">{definition.product.trustCenter.auditStatement}</p></div></div>;
}

function ProductNotifications() {
  return <div className="mt-3 rounded-xl bg-[var(--workshop-subtle)] p-4 text-center"><Bell className="mx-auto size-5 text-[#94a3b8]" /><div className="mt-2 text-[11px] font-bold text-[#475569]">지금 표시할 새 알림이 없습니다</div><p className="mt-1 text-[10px] leading-4 text-[#7b8798]">승인 요청, 담당 업무 변경, 다가오는 기한이 생기면 이곳에 표시됩니다.</p></div>;
}

function ProductAudiences({ definition }: { definition: AppDefinition }) {
  return <div className="mt-3 space-y-2"><p className="text-[10px] leading-4 text-[#7b8798]">실제 화면과 업무 버튼은 로그인한 사용자의 역할을 기준으로 제한됩니다.</p>{definition.product.audiences.length ? definition.product.audiences.map((audience) => <div key={audience.id} className="rounded-xl border border-[var(--workshop-line)] p-3"><div className="text-[11px] font-bold text-[#334155]">{audience.name}</div><p className="mt-1 text-[10px] leading-4 text-[#7b8798]">{audience.summary}</p></div>) : <div className="rounded-xl bg-amber-50 p-3 text-[10px] text-amber-800">운영 전에 사용자 역할을 확인해야 합니다.</div>}</div>;
}

function MobileNavigation({ pages, selectedPage, onSelect }: { pages: AppPage[]; selectedPage: AppPage; onSelect: (pageId: string) => void }) {
  return <nav aria-label="전체 업무 메뉴" className="absolute inset-x-3 top-[68px] z-40 rounded-2xl border border-[var(--workshop-line)] bg-white p-3 shadow-2xl lg:hidden"><div className="grid grid-cols-2 gap-2">{pages.map((page) => { const Icon = pageIcon(page.intent); return <button key={page.id} type="button" onClick={() => onSelect(page.id)} className={cn("flex items-center gap-2 rounded-xl p-3 text-left text-[12px] font-semibold", page.id === selectedPage.id ? "bg-[var(--workshop-accent-soft)] text-[var(--workshop-accent)]" : "bg-[var(--workshop-subtle)] text-[#475569]")}><Icon className="size-4" />{page.name}</button>; })}</div></nav>;
}

function PageContext({ definition, page, actionCount }: { definition: AppDefinition; page: AppPage; actionCount: number }) {
  const state = useRuntimeState();
  const filterCount = Object.keys(state.filters).length + (state.searchText ? 1 : 0);
  const capabilityName = definition.product.capabilityGroups.find((group) =>
    group.pageIds.includes(page.pageId),
  )?.name ?? "오늘의 업무 흐름";
  return (
    <header className="bg-white px-4 pb-5 pt-5 md:px-7 md:pb-6 md:pt-7">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-end"><div className="min-w-0 flex-1"><div className="flex items-center gap-2 text-[11px] font-bold text-[var(--workshop-accent)]"><Sparkles className="size-4" />{capabilityName}</div><h1 className="mt-2 text-[26px] font-bold tracking-[-.045em] text-[var(--workshop-ink)] md:text-[32px]">{page.name}</h1><p className="mt-2 max-w-3xl text-[13px] leading-6 text-[#64748b]">{definition.purpose}</p></div><div className="grid grid-cols-3 gap-2 xl:w-[350px]"><Pulse label="현재 범위" value={filterCount ? `${filterCount}개 조건` : "전체 업무"} /><Pulse label="선택 상태" value={state.selectedObjectId ? "선택 완료" : "선택 전"} /><Pulse label="운영 상태" value="최신" isLive /></div></div>
      <DecisionRail isSelected={Boolean(state.selectedObjectId)} actionCount={actionCount} />
    </header>
  );
}

function DecisionRail({ isSelected, actionCount }: { isSelected: boolean; actionCount: number }) {
  return <div className="mt-5 grid overflow-hidden rounded-2xl border border-[var(--workshop-line)] bg-[var(--workshop-subtle)] md:grid-cols-3"><RailStep number="1" label="처리할 업무 선택" value={isSelected ? "선택됨" : "목록에서 선택"} isActive={!isSelected} isComplete={isSelected} /><RailStep number="2" label="다음 업무 실행" value={`${actionCount}개 업무 규칙 연결`} isActive={isSelected} /><RailStep number="3" label="변경 근거 보존" value="담당자·시각·결과 자동 기록" /></div>;
}

function RailStep({ number, label, value, isActive = false, isComplete = false }: { number: string; label: string; value: string; isActive?: boolean; isComplete?: boolean }) {
  return <div className={cn("flex items-center gap-3 border-b border-[var(--workshop-line)] px-4 py-3 last:border-b-0 md:border-b-0 md:border-r md:last:border-r-0", isActive && "bg-white shadow-[inset_0_3px_0_var(--workshop-accent)]")}><span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full text-[11px] font-bold", isComplete ? "bg-emerald-100 text-emerald-700" : isActive ? "bg-[var(--workshop-accent)] text-white" : "bg-white text-[#64748b]")}>{isComplete ? <CheckCircle2 className="size-4" /> : number}</span><div className="min-w-0"><div className="text-[11px] font-semibold text-[#334155]">{label}</div><div className="mt-0.5 truncate text-[10px] text-[#7c8899]">{value}</div></div></div>;
}

function Pulse({ label, value, isLive = false }: { label: string; value: string; isLive?: boolean }) {
  return <div className="rounded-xl bg-[var(--workshop-subtle)] px-3 py-2.5"><div className="text-[9px] font-bold tracking-[.08em] text-[#8a96a6]">{label}</div><div className="mt-1 flex items-center gap-1.5 truncate text-[11px] font-bold text-[#3e4c60]">{isLive ? <span className="size-1.5 rounded-full bg-emerald-500" /> : null}{value}</div></div>;
}

function TopNavigation({ pages, selectedPage, onSelect }: { pages: AppPage[]; selectedPage: AppPage; onSelect: (pageId: string) => void }) {
  return <nav aria-label="업무 화면" className="hidden gap-1 overflow-auto border-y border-[var(--workshop-line)] bg-white px-7 lg:flex">{pages.map((page) => <button key={page.id} type="button" onClick={() => onSelect(page.id)} className={cn("whitespace-nowrap border-b-2 px-3 py-3 text-[12px] font-semibold", page.id === selectedPage.id ? "border-[var(--workshop-accent)] text-[var(--workshop-accent)]" : "border-transparent text-[#64748b]")}>{page.name}</button>)}</nav>;
}

function MobileBottomNavigation({ pages, selectedPage, onSelect }: { pages: AppPage[]; selectedPage: AppPage; onSelect: (pageId: string) => void }) {
  return <nav aria-label="빠른 업무 메뉴" className="absolute inset-x-0 bottom-0 z-30 grid h-[68px] grid-flow-col border-t border-[var(--workshop-line)] bg-white/95 px-2 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">{pages.slice(0, 4).map((page) => { const Icon = pageIcon(page.intent); const selected = page.id === selectedPage.id; return <button key={page.id} type="button" onClick={() => onSelect(page.id)} className={cn("flex flex-col items-center justify-center gap-1 text-[9px] font-semibold", selected ? "text-[var(--workshop-accent)]" : "text-[#7b8798]")}><Icon className="size-5" /><span className="max-w-20 truncate">{page.name}</span></button>; })}</nav>;
}

function RuntimeHeaderSlots({ definition, bindings }: { definition: AppDefinition; bindings: RuntimeBindings }) {
  if (!definition.header.visible) return null;
  const slots = Object.values(definition.header.slots).filter((section) => section.widgets.length > 0);
  if (slots.length === 0) return null;
  return <div className="grid gap-4 border-t border-[var(--workshop-line)] bg-white px-4 py-4 lg:grid-cols-3 lg:px-7">{slots.map((section) => <RuntimeSection key={section.id} section={section} bindings={bindings} isHeader />)}</div>;
}

function RuntimeSections({ sections, bindings }: { sections: AppSection[]; bindings: RuntimeBindings }) {
  return <div className="grid content-start gap-4 p-4 md:p-7 lg:grid-cols-12">{sections.map((section) => <div key={section.id} className={cn("min-w-0", spanClass(section.span))}><RuntimeSection section={section} bindings={bindings} /></div>)}</div>;
}

function RuntimeSection({ section, bindings, isHeader = false }: { section: AppSection; bindings: RuntimeBindings; isHeader?: boolean }) {
  const [activeTab, setActiveTab] = useState(0);
  const widgets = section.layout === "tabs" ? section.widgets.slice(activeTab, activeTab + 1) : section.widgets;
  return <section aria-label={section.title} className={cn(sectionClass(section), isHeader && "border-0 bg-transparent p-0 shadow-none")} style={{ backgroundColor: isHeader ? "transparent" : section.style.background }}>{section.title && !isHeader ? <h2 className="mb-3 text-[12px] font-bold tracking-[-.01em] text-[#475569]">{section.title}</h2> : null}{section.layout === "tabs" ? <RuntimeTabs widgets={section.widgets} active={activeTab} onChange={setActiveTab} /> : null}<div className={widgetLayoutClass(section.layout)}>{widgets.map((widget) => <RuntimeWidget key={widget.id} widget={widget} bindings={bindings} />)}</div></section>;
}

function RuntimeTabs({ widgets, active, onChange }: { widgets: AppWidget[]; active: number; onChange: (index: number) => void }) {
  return <div role="tablist" className="mb-4 flex gap-1 overflow-auto border-b border-[var(--workshop-line)]">{widgets.map((widget, index) => <button key={widget.id} role="tab" aria-selected={active === index} onClick={() => onChange(index)} className={cn("whitespace-nowrap border-b-2 px-3 py-2.5 text-[12px] font-semibold", active === index ? "border-[var(--workshop-accent)] text-[var(--workshop-accent)]" : "border-transparent text-[#64748b]")}>{widget.config.title || "업무 보기"}</button>)}</div>;
}

function RuntimeWidget({ widget, bindings }: { widget: AppWidget; bindings: RuntimeBindings }) {
  return <div className={cn("min-w-0", widgetHeightClass(widget.kind))} data-workshop-widget={widget.kind}><WidgetRenderer widget={widget} {...bindings} /></div>;
}

function RuntimeOverlay({ overlays, bindings }: { overlays: AppOverlay[]; bindings: RuntimeBindings }) {
  const state = useRuntimeState(); const dispatch = useRuntimeDispatch(); const overlay = overlays.find((item) => item.id === state.openOverlayId); if (!overlay) return null;
  return <div role="dialog" aria-modal="true" aria-label={overlay.name} className="fixed inset-0 z-50 flex justify-end bg-[#08111f]/55 p-3 backdrop-blur-sm"><div className={cn("h-full max-w-full overflow-auto rounded-2xl bg-[var(--workshop-canvas)] shadow-2xl", overlay.kind === "modal" && "m-auto max-h-[85vh]")} style={{ width: overlay.widthPx }}><div className="sticky top-0 z-10 flex items-center border-b border-[var(--workshop-line)] bg-white px-5 py-4"><h2 className="text-[15px] font-bold">{overlay.name}</h2><button type="button" className="ml-auto rounded-xl p-2 hover:bg-[var(--workshop-subtle)]" aria-label="닫기" onClick={() => dispatch({ type: "closeOverlay" })}><X className="size-5" /></button></div><RuntimeSections sections={overlay.sections} bindings={bindings} /></div></div>;
}

function themeVariables(preset: AppThemePreset): CSSProperties {
  const palettes = { ocean: ["#087f8c", "#e7f6f7", "#102d35"], indigo: ["#4f46e5", "#efefff", "#202044"], emerald: ["#087f5b", "#e8f7f0", "#12352a"], amber: ["#b45309", "#fff4df", "#3f2615"], graphite: ["#475569", "#eef2f6", "#172033"] } as const;
  const [accent, accentSoft, nav] = palettes[preset];
  return { "--workshop-accent": accent, "--workshop-accent-soft": accentSoft, "--workshop-nav": nav, "--workshop-ink": "#162033", "--workshop-canvas": "#f3f5f7", "--workshop-subtle": "#f7f9fb", "--workshop-line": "#e1e6eb" } as CSSProperties;
}

function pageIcon(intent: AppPage["intent"]): LucideIcon { return { workbench: BriefcaseBusiness, overview: LayoutDashboard, records: Database, governance: CheckCircle2, evidence: ShieldCheck, relationships: GitBranch }[intent]; }
function spanClass(span: AppSection["span"]): string { return { 3: "lg:col-span-3", 4: "lg:col-span-4", 6: "lg:col-span-6", 8: "lg:col-span-8", 9: "lg:col-span-9", 12: "lg:col-span-12" }[span]; }
function sectionClass(section: AppSection): string { const padding = { none: "p-0", compact: "p-2", regular: "p-3 md:p-4", large: "p-5 md:p-6" }[section.style.padding]; const border = { none: "", bordered: "border border-[var(--workshop-line)]", shadow: "border border-[var(--workshop-line)] shadow-[0_18px_50px_-38px_rgba(15,23,42,.7)]" }[section.style.border]; return cn("rounded-2xl bg-white", padding, border); }
function widgetLayoutClass(layout: AppSection["layout"]): string { if (layout === "columns") return "grid gap-4 md:grid-cols-2 xl:grid-cols-3"; if (layout === "rows") return "grid gap-4 md:grid-flow-col md:auto-cols-[minmax(280px,1fr)] md:overflow-x-auto"; if (layout === "toolbar") return "grid gap-3 sm:grid-cols-2 xl:flex xl:flex-wrap xl:items-stretch"; return "grid gap-4"; }
function widgetHeightClass(kind: AppWidget["kind"]): string { if (["objectTable", "objectList", "timeline", "barChart", "pieChart", "kanban", "calendar", "pivotTable"].includes(kind)) return "min-h-0 md:min-h-[280px]"; if (["objectDetail", "actionForm", "aipChatbot"].includes(kind)) return "min-h-0 md:min-h-[200px]"; return "min-h-0"; }
