import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { X } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

import {
  initialVariableValues,
  type AppDefinition,
  type AppOverlay,
  type AppSection,
  type AppWidget,
} from "../lib/app-model";
import {
  RuntimeDispatchProvider,
  RuntimeStateProvider,
  useRuntimeDispatch,
  useRuntimeState,
  useRuntimeStateReducer,
} from "../lib/runtime-state";
import { WidgetRenderer } from "./widgets/registry";
import { WorkshopRuntimeApplicationProvider } from "./runtime-application-context";

interface RuntimeModeProps {
  definition: AppDefinition;
  objectViewsByApiName: Record<string, FoundryLiteOntologyObjectView>;
  actionViews: readonly FoundryLiteOntologyActionView[];
  applicationId?: string | null;
}

type RuntimeBindings = Pick<RuntimeModeProps, "objectViewsByApiName" | "actionViews">;

/** Render the published Workshop contract with the same widget graph authored by the Builder. */
export function RuntimeMode({
  definition,
  objectViewsByApiName,
  actionViews,
  applicationId = null,
}: RuntimeModeProps) {
  const [state, dispatch] = useRuntimeStateReducer(initialVariableValues(definition.variables));
  const defaultPage = definition.pages.find((page) => page.isDefault) ?? definition.page;
  const [selectedPageId, setSelectedPageId] = useState(defaultPage.id);
  const selectedPage = definition.pages.find((page) => page.id === selectedPageId) ?? defaultPage;
  const bindings = { objectViewsByApiName, actionViews };
  return (
    <WorkshopRuntimeApplicationProvider applicationId={applicationId}>
      <RuntimeStateProvider value={state}>
        <RuntimeDispatchProvider value={dispatch}>
          <main aria-label="Workshop runtime canvas" className="h-full overflow-auto bg-[#f6f8fa]">
            <RuntimeHeader definition={definition} bindings={bindings} />
            <RuntimePageTabs
              pages={definition.pages}
              selectedPageId={selectedPage.id}
              onSelect={setSelectedPageId}
            />
            <RuntimeSections sections={selectedPage.sections} bindings={bindings} />
            <RuntimeOverlay overlays={definition.overlays} bindings={bindings} />
          </main>
        </RuntimeDispatchProvider>
      </RuntimeStateProvider>
    </WorkshopRuntimeApplicationProvider>
  );
}

function RuntimePageTabs({ pages, selectedPageId, onSelect }: {
  pages: AppDefinition["pages"];
  selectedPageId: string;
  onSelect(pageId: string): void;
}) {
  if (pages.length < 2) return null;
  return (
    <nav aria-label="Workshop 앱 페이지" className="flex gap-1 overflow-auto border-b border-[#d5dce1] bg-white px-4">
      {pages.map((page) => (
        <button
          key={page.id}
          type="button"
          onClick={() => onSelect(page.id)}
          className={cn(
            "whitespace-nowrap border-b-2 px-3 py-2 text-[12px] font-medium",
            page.id === selectedPageId
              ? "border-[#2d72d2] text-[#215db0]"
              : "border-transparent text-[#5f6b7c] hover:text-[#1c2127]",
          )}
        >
          {page.name}
        </button>
      ))}
    </nav>
  );
}

function RuntimeHeader({ definition, bindings }: { definition: AppDefinition; bindings: RuntimeBindings }) {
  if (!definition.header.visible) return null;
  const slots = definition.header.slots;
  return (
    <header className="border-b border-[#d5dce1] bg-white px-4 py-3">
      <h1 className="mb-2 text-[15px] font-semibold text-[#1c2127]">{definition.header.title}</h1>
      <div className="grid gap-3 lg:grid-cols-3">
        {[slots.left, slots.center, slots.right].map((section) => (
          <RuntimeSection key={section.id} section={section} bindings={bindings} isHeader />
        ))}
      </div>
    </header>
  );
}

function RuntimeSections({ sections, bindings }: { sections: AppSection[]; bindings: RuntimeBindings }) {
  return (
    <div className="grid content-start gap-3 p-4 lg:grid-cols-12">
      {sections.map((section) => (
        <div key={section.id} className="min-w-0 lg:col-span-12">
          <RuntimeSection section={section} bindings={bindings} />
        </div>
      ))}
    </div>
  );
}

function RuntimeSection({ section, bindings, isHeader = false }: {
  section: AppSection;
  bindings: RuntimeBindings;
  isHeader?: boolean;
}) {
  const [activeTab, setActiveTab] = useState(0);
  const widgets = section.layout === "tabs" ? section.widgets.slice(activeTab, activeTab + 1) : section.widgets;
  return (
    <section
      aria-label={section.title}
      className={cn(sectionClass(section), isHeader && "border-0 bg-transparent p-0 shadow-none")}
      style={{ backgroundColor: isHeader ? "transparent" : section.style.background }}
    >
      {section.title && !isHeader ? <h2 className="mb-2 text-[12px] font-semibold text-[#404854]">{section.title}</h2> : null}
      {section.layout === "tabs" ? <RuntimeTabs widgets={section.widgets} active={activeTab} onChange={setActiveTab} /> : null}
      <div className={widgetLayoutClass(section.layout)}>
        {widgets.map((widget) => <RuntimeWidget key={widget.id} widget={widget} bindings={bindings} />)}
      </div>
    </section>
  );
}

function RuntimeTabs({ widgets, active, onChange }: { widgets: AppWidget[]; active: number; onChange: (index: number) => void }) {
  return (
    <div role="tablist" className="mb-3 flex gap-1 border-b border-[#d5dce1]">
      {widgets.map((widget, index) => (
        <button
          key={widget.id}
          role="tab"
          aria-selected={active === index}
          onClick={() => onChange(index)}
          className={cn("border-b-2 px-3 py-1.5 text-[11px]", active === index ? "border-[#2d72d2] text-[#215db0]" : "border-transparent text-[#5f6b7c]")}
        >
          {widget.config.title || widget.kind}
        </button>
      ))}
    </div>
  );
}

function RuntimeWidget({ widget, bindings }: { widget: AppWidget; bindings: RuntimeBindings }) {
  return (
    <div className={cn("min-w-0", widgetHeightClass(widget.kind))} data-workshop-widget={widget.kind}>
      <WidgetRenderer widget={widget} {...bindings} />
    </div>
  );
}

function RuntimeOverlay({ overlays, bindings }: { overlays: AppOverlay[]; bindings: RuntimeBindings }) {
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();
  const overlay = overlays.find((item) => item.id === state.openOverlayId);
  if (!overlay) return null;
  return (
    <div role="dialog" aria-modal="true" aria-label={overlay.name} className="fixed inset-0 z-50 flex justify-end bg-black/30 p-4">
      <div className={cn("h-full overflow-auto rounded bg-[#f6f8fa] shadow-xl", overlay.kind === "modal" && "m-auto max-h-[85vh]")} style={{ width: overlay.widthPx }}>
        <div className="flex items-center border-b bg-white px-3 py-2">
          <h2 className="text-sm font-semibold">{overlay.name}</h2>
          <button className="ml-auto rounded p-1 hover:bg-muted" aria-label="오버레이 닫기" onClick={() => dispatch({ type: "closeOverlay" })}><X className="size-4" /></button>
        </div>
        <RuntimeSections sections={overlay.sections} bindings={bindings} />
      </div>
    </div>
  );
}

function sectionClass(section: AppSection): string {
  const padding = { none: "p-0", compact: "p-2", regular: "p-4", large: "p-6" }[section.style.padding];
  const border = { none: "", bordered: "border border-[#d5dce1]", shadow: "border border-[#e4e9ed] shadow-sm" }[section.style.border];
  return cn("rounded bg-white", padding, border);
}

function widgetLayoutClass(layout: AppSection["layout"]): string {
  if (layout === "columns") return "grid gap-3 md:grid-cols-2 xl:grid-cols-3";
  if (layout === "toolbar") return "flex flex-wrap items-start gap-2";
  return "grid gap-3";
}

function widgetHeightClass(kind: AppWidget["kind"]): string {
  if (["objectTable", "objectList", "timeline", "barChart", "pieChart"].includes(kind)) return "min-h-[280px]";
  if (["objectDetail", "actionForm", "aipChatbot"].includes(kind)) return "min-h-[220px]";
  return "min-h-[80px]";
}
