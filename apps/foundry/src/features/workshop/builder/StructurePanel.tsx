import {
  Braces,
  ChevronDown,
  ChevronRight,
  Database,
  FileText,
  Layers,
  LayoutGrid,
  PanelRight,
  PanelTop,
  Palette,
  Plus,
  Sparkles,
} from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

import {
  collectWidgets,
  type AppDefinition,
  type AppSection,
} from "../lib/app-model";
import { addOverlay, addPage, addVariable, setAppName } from "../lib/app-edit";
import { widgetDefinition } from "../lib/widget-catalog";
import type { BuilderSelection } from "./selection";

interface StructurePanelProps {
  definition: AppDefinition;
  activeContainerId: string;
  selection: BuilderSelection;
  onChange: (definition: AppDefinition) => void;
  onSelect: (selection: BuilderSelection) => void;
  onActiveContainer: (containerId: string) => void;
  onOpenTemplates: () => void;
}

/**
 * Palantir Workshop "Layout" 컨트롤 센터.
 * 헤더 → 페이지 → 오버레이 → 변수 계층 트리(섹션·위젯까지 확장).
 */
export function StructurePanel({
  definition,
  activeContainerId,
  selection,
  onChange,
  onSelect,
  onActiveContainer,
  onOpenTemplates,
}: StructurePanelProps) {
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set([activeContainerId]),
  );
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(
    () => new Set(),
  );

  const toggleExpand = (id: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleSection = (sectionId: string) =>
    setCollapsedSections((current) => {
      const next = new Set(current);
      if (next.has(sectionId)) next.delete(sectionId);
      else next.add(sectionId);
      return next;
    });

  const handleAddPage = () => {
    const result = addPage(definition);
    onChange(result.definition);
    onActiveContainer(result.page.id);
    setExpanded((current) => new Set(current).add(result.page.id));
    onSelect({ type: "page", pageId: result.page.id });
  };

  const handleAddOverlay = () => {
    const result = addOverlay(definition);
    onChange(result.definition);
    onActiveContainer(result.overlay.id);
    setExpanded((current) => new Set(current).add(result.overlay.id));
    onSelect({ type: "overlay", overlayId: result.overlay.id });
  };

  const handleAddVariable = () => {
    const result = addVariable(definition);
    onChange(result.definition);
    onSelect({ type: "variable", variableId: result.variable.id });
  };

  const renderSections = (sections: AppSection[]) =>
    sections.map((section) => {
      const isSectionCollapsed = collapsedSections.has(section.id);
      return (
        <div key={section.id}>
          <TreeRow
            depth={2}
            icon={LayoutGrid}
            label={section.title}
            isActive={
              selection?.type === "section" &&
              selection.sectionId === section.id
            }
            caret={
              section.widgets.length === 0
                ? "none"
                : isSectionCollapsed
                  ? "right"
                  : "down"
            }
            onCaret={() => toggleSection(section.id)}
            onClick={() => onSelect({ type: "section", sectionId: section.id })}
            trailing={
              <span className="text-[10px] text-[#8f99a8]">
                {layoutLabel(section.layout)}
              </span>
            }
          />
          {!isSectionCollapsed
            ? section.widgets.map((widget) => {
                const meta = widgetDefinition(widget.kind);
                return (
                  <TreeRow
                    key={widget.id}
                    depth={3}
                    icon={meta.icon}
                    label={widget.config.title || meta.label}
                    isActive={
                      selection?.type === "widget" &&
                      selection.widgetId === widget.id
                    }
                    onClick={() =>
                      onSelect({
                        type: "widget",
                        sectionId: section.id,
                        widgetId: widget.id,
                      })
                    }
                  />
                );
              })
            : null}
        </div>
      );
    });

  const variableNames = Array.from(
    new Set(
      collectWidgets(definition)
        .map((widget) => widget.config.objectApiName)
        .filter((name): name is string => Boolean(name)),
    ),
  );

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="space-y-3 border-b border-[#e4e9ed] p-4">
        <div><div className="text-[10px] font-bold text-[#8a96a6]">사용자 경험</div><div className="mt-0.5 text-[13px] font-bold text-[#172033]">화면과 메뉴</div></div>
        <input
          value={definition.name}
          onChange={(event) =>
            onChange(setAppName(definition, event.target.value))
          }
          className="w-full rounded-xl border border-[#e1e6eb] bg-[#f7f9fb] px-3 py-2 text-[13px] font-semibold text-[#1c2127] focus:border-[#6651c7] focus:outline-none"
        />
        <button
          type="button"
          onClick={onOpenTemplates}
          className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-[#eeeafd] py-2.5 text-[12px] font-bold text-[#634bb5] hover:bg-[#e9e1f9]"
        >
          <Sparkles className="size-3.5" /> AI FDE 추천 구성 적용
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto py-2">
        <GroupLabel icon={Layers} label="사용자 화면" />

        <TreeRow
          depth={0}
          icon={Palette}
          label="브랜드와 메뉴"
          isActive={selection?.type === "app"}
          onClick={() => onSelect({ type: "app" })}
          trailing={
            <span className="text-[10px] text-[#8f99a8]">
              {themeLabel(definition.theme.preset)}
            </span>
          }
        />

        <TreeRow
          depth={0}
          icon={PanelTop}
          label="헤더"
          isActive={selection?.type === "header"}
          onClick={() => onSelect({ type: "header" })}
          trailing={
            <span className="text-[10px] text-[#8f99a8]">
              {definition.header.visible ? "표시" : "숨김"}
            </span>
          }
        />

        {/* 페이지 트리 */}
        <SubLabel
          label="페이지"
          action={<AddButton onClick={handleAddPage} />}
        />
        {definition.pages.map((page) => (
          <div key={page.id}>
            <TreeRow
              depth={1}
              icon={FileText}
              label={page.name}
              isActive={
                selection?.type === "page" && selection.pageId === page.id
              }
              caret={expanded.has(page.id) ? "down" : "right"}
              onCaret={() => toggleExpand(page.id)}
              onClick={() => {
                onActiveContainer(page.id);
                onSelect({ type: "page", pageId: page.id });
              }}
              trailing={
                page.isDefault ? (
                  <span className="rounded bg-[#eef1f4] px-1 text-[9px] text-[#5f6b7c]">
                    기본
                  </span>
                ) : null
              }
            />
            {expanded.has(page.id) ? renderSections(page.sections) : null}
          </div>
        ))}

        {/* 오버레이 트리 */}
        <SubLabel
          label="보조 화면"
          action={<AddButton onClick={handleAddOverlay} />}
        />
        {definition.overlays.length === 0 ? (
          <p className="px-3 py-1 text-[11px] text-[#8f99a8]">
            필요할 때 열리는 상세·확인 화면을 추가할 수 있습니다.
          </p>
        ) : (
          definition.overlays.map((overlay) => (
            <div key={overlay.id}>
              <TreeRow
                depth={1}
                icon={PanelRight}
                label={overlay.name}
                isActive={
                  selection?.type === "overlay" &&
                  selection.overlayId === overlay.id
                }
                caret={expanded.has(overlay.id) ? "down" : "right"}
                onCaret={() => toggleExpand(overlay.id)}
                onClick={() => {
                  onActiveContainer(overlay.id);
                  onSelect({ type: "overlay", overlayId: overlay.id });
                }}
                trailing={
                  <span className="text-[10px] text-[#8f99a8]">
                    {overlay.kind === "modal" ? "가운데 확인창" : "옆 상세창"}
                  </span>
                }
              />
              {expanded.has(overlay.id)
                ? renderSections(overlay.sections)
                : null}
            </div>
          ))
        )}

        {/* 변수 */}
        <GroupLabel icon={Database} label="화면 간 연결" className="mt-3" />

        {/* 선언된 파라미터 */}
        <SubLabel
          label="공유 조건"
          action={<AddButton onClick={handleAddVariable} />}
        />
        {definition.variables.length === 0 ? (
          <p className="px-3 py-1 text-[11px] text-[#8f99a8]">
            여러 화면에서 함께 사용할 검색 조건을 추가할 수 있습니다.
          </p>
        ) : (
          definition.variables.map((variable) => (
            <TreeRow
              key={variable.id}
              depth={1}
              icon={Braces}
              label={variable.name}
              isActive={
                selection?.type === "variable" &&
                selection.variableId === variable.id
              }
              onClick={() =>
                onSelect({ type: "variable", variableId: variable.id })
              }
              trailing={
                <span className="text-[10px] text-[#8f99a8]">
                  {variableTypeLabel(variable.type)}
                </span>
              }
            />
          ))
        )}

        {/* 객체 세트 (위젯 바인딩에서 도출) */}
        <SubLabel label="연결된 업무 데이터" />
        {variableNames.length === 0 ? (
          <p className="px-3 py-1 text-[11px] text-[#8f99a8]">
            화면에 보여줄 업무 데이터가 아직 연결되지 않았습니다.
          </p>
        ) : (
          variableNames.map((name) => (
            <div
              key={name}
              className="flex items-center gap-2 px-3 py-1 text-[12px]"
            >
              <span className="flex size-4 items-center justify-center rounded bg-[#2d72d2]/10 text-[9px] font-bold text-[#2d72d2]">
                {name.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1 truncate text-[11px] font-semibold text-[#404854]">
                {definition.presentation.objectTypeNames[name] ?? "업무 데이터"}
              </span>
              <span className="text-[10px] text-emerald-600">연결됨</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function layoutLabel(layout: AppSection["layout"]): string {
  return {
    flow: "세로 배치",
    columns: "나란히",
    rows: "가로 흐름",
    toolbar: "요약 영역",
    tabs: "탭 구성",
  }[layout];
}

function variableTypeLabel(
  type: AppDefinition["variables"][number]["type"],
): string {
  return { string: "문자", number: "숫자", boolean: "예·아니요" }[type];
}

function themeLabel(preset: AppDefinition["theme"]["preset"]): string {
  return {
    ocean: "오션",
    indigo: "인디고",
    emerald: "에메랄드",
    amber: "앰버",
    graphite: "그래파이트",
  }[preset];
}

function GroupLabel({
  icon: Icon,
  label,
  className,
}: {
  icon: typeof Layers;
  label: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-1.5 px-3 pt-1 pb-1.5 text-[10px] font-semibold tracking-wide text-[#8f99a8] uppercase",
        className,
      )}
    >
      <Icon className="size-3" />
      {label}
    </div>
  );
}

function SubLabel({
  label,
  action,
}: {
  label: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mt-1 flex items-center justify-between px-3 pb-0.5">
      <span className="text-[10px] font-medium tracking-wide text-[#a7b1bd] uppercase">
        {label}
      </span>
      {action}
    </div>
  );
}

function AddButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex size-4 items-center justify-center rounded text-[#5f6b7c] hover:bg-[#e4e9ed]"
      aria-label="추가"
    >
      <Plus className="size-3" />
    </button>
  );
}

function TreeRow({
  depth,
  icon: Icon,
  label,
  isActive,
  caret,
  onCaret,
  onClick,
  trailing,
}: {
  depth: number;
  icon: typeof Layers;
  label: string;
  isActive?: boolean;
  caret?: "right" | "down" | "none";
  onCaret?: () => void;
  onClick: () => void;
  trailing?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-1 pr-2 text-left",
        isActive ? "bg-[#e8f0fb]" : "hover:bg-[#f0f2f5]",
      )}
      style={{ paddingLeft: `${8 + depth * 14}px` }}
    >
      {caret && caret !== "none" ? (
        <button
          type="button"
          aria-label="펼치기"
          onClick={(event) => {
            event.stopPropagation();
            onCaret?.();
          }}
          className="flex size-4 shrink-0 items-center justify-center rounded text-[#8f99a8] hover:text-[#404854]"
        >
          {caret === "down" ? (
            <ChevronDown className="size-3" />
          ) : (
            <ChevronRight className="size-3" />
          )}
        </button>
      ) : (
        <span className="size-4 shrink-0" />
      )}
      <button
        type="button"
        onClick={onClick}
        className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left"
      >
        <Icon
          className={cn(
            "size-3.5 shrink-0",
            isActive ? "text-[#215db0]" : "text-[#8f99a8]",
          )}
        />
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-[12px]",
            isActive ? "font-medium text-[#215db0]" : "text-[#1c2127]",
          )}
        >
          {label}
        </span>
      </button>
      {trailing ? <span className="shrink-0">{trailing}</span> : null}
    </div>
  );
}
