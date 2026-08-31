import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { GripVertical, Plus, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import {
  BACKGROUND_SWATCHES,
  SECTION_BORDERS,
  SECTION_LAYOUTS,
  SECTION_PADDINGS,
  createWidget,
  type AggregationMetric,
  type AppDefinition,
  type AppPresentation,
  type AppSection,
  type AppVariable,
  type ChartType,
  type MetricLayout,
  type MetricSpec,
  type OverlayKind,
  type SectionLayout,
  type VariableFilter,
  type VariableType,
  type WidgetKind,
} from "../lib/app-model";
import { metricLabel } from "../lib/aggregate";
import {
  businessActionName,
  businessObjectTypeName,
  isTechnicalIdentifierProperty,
} from "../lib/business-display";
import {
  addWidget,
  removeOverlay,
  removePage,
  removeSection,
  removeVariable,
  removeWidget,
  renameOverlay,
  renamePage,
  renameVariable,
  setDefaultPage,
  setHeaderTitle,
  setHeaderVisible,
  setOverlayKind,
  setOverlayWidth,
  setPageBackground,
  setSectionLayout,
  setSectionSpan,
  setSectionStyle,
  setSectionTitle,
  setVariableDefault,
  setVariableType,
  setWidgetConfig,
} from "../lib/app-edit";
import {
  actionsForObject,
  buildWidgetSuggestion,
} from "../lib/ontology-context";
import {
  widgetDefinition,
  type WidgetConfigField,
} from "../lib/widget-catalog";
import { WidgetPalette } from "./WidgetPalette";
import { AppAppearancePanel } from "./AppAppearancePanel";
import {
  findOverlay,
  findPage,
  findSection,
  findVariable,
  findWidget,
  type BuilderSelection,
} from "./selection";

interface InspectorPanelProps {
  definition: AppDefinition;
  selection: BuilderSelection;
  objectViews: readonly FoundryLiteOntologyObjectView[];
  actionViews: readonly FoundryLiteOntologyActionView[];
  onChange: (definition: AppDefinition) => void;
  onSelect: (selection: BuilderSelection) => void;
}

const METRICS: AggregationMetric[] = ["count", "sum", "avg", "min", "max"];
const ALL_VALUE = "__all__";

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-[11px] font-medium text-[#404854]">{label}</span>
      {children}
    </label>
  );
}

export function InspectorPanel({
  definition,
  selection,
  objectViews,
  actionViews,
  onChange,
  onSelect,
}: InspectorPanelProps) {
  if (!selection) {
    return (
      <InspectorShell title="화면 설정">
        <p className="text-[12px] leading-5 text-muted-foreground">
          가운데 미리보기에서 바꾸고 싶은 화면이나 정보를 선택하세요.
        </p>
      </InspectorShell>
    );
  }

  if (selection.type === "widget") {
    const widget = findWidget(definition, selection.widgetId);
    if (!widget) return <InspectorShell title="화면 요소" />;
    const definitionMeta = widgetDefinition(widget.kind);
    const objectView =
      objectViews.find(
        (view) => view.apiName === widget.config.objectApiName,
      ) ?? null;
    const patch = (next: Partial<typeof widget.config>) =>
      onChange(setWidgetConfig(definition, widget.id, next));

    return (
      <InspectorShell title={`${definitionMeta.label} 설정`}>
        {definitionMeta.fields.map((field) => (
          <WidgetField
            key={field}
            field={field}
            config={widget.config}
            objectView={objectView}
            objectViews={objectViews}
            actionViews={actionViews}
            variables={definition.variables}
            presentation={definition.presentation}
            onPatch={patch}
          />
        ))}
      </InspectorShell>
    );
  }

  if (selection.type === "app") {
    return (
      <AppAppearancePanel
        definition={definition}
        onChange={onChange}
      />
    );
  }

  if (selection.type === "section") {
    const section = findSection(definition, selection.sectionId);
    if (!section) return <InspectorShell title="섹션" />;
    return (
      <InspectorShell title="화면 영역 설정">
        <Field label="제목">
          <Input
            className="h-8 text-[12px]"
            value={section.title}
            onChange={(event) =>
              onChange(
                setSectionTitle(definition, section.id, event.target.value),
              )
            }
          />
        </Field>
        <Field label="레이아웃">
          <p className="mb-1.5 text-[11px] text-muted-foreground">
            이 영역 안에서 정보가 보이는 방식을 정합니다.
          </p>
          <div className="grid grid-cols-3 gap-1.5">
            {SECTION_LAYOUTS.map((layout) => {
              const isActive = section.layout === layout.id;
              return (
                <button
                  key={layout.id}
                  type="button"
                  title={layout.description}
                  onClick={() =>
                    onChange(
                      setSectionLayout(definition, section.id, layout.id),
                    )
                  }
                  className={cn(
                    "flex flex-col items-center gap-1 rounded-md border p-1.5",
                    isActive
                      ? "border-[#2d72d2] bg-[#e8f0fb]"
                      : "border-[#d5dce1] hover:bg-[#f6f8fa]",
                  )}
                >
                  <LayoutSchematic layout={layout.id} active={isActive} />
                  <span
                    className={cn(
                      "text-[10px]",
                      isActive
                        ? "font-medium text-[#215db0]"
                        : "text-[#404854]",
                    )}
                  >
                    {layout.label}
                  </span>
                </button>
              );
            })}
          </div>
        </Field>
        <Field label="화면 너비">
          <div className="grid grid-cols-3 gap-1">
            {([3, 4, 6, 8, 9, 12] as const).map((span) => (
              <button
                key={span}
                type="button"
                onClick={() =>
                  onChange(setSectionSpan(definition, section.id, span))
                }
                className={cn(
                  "rounded border px-1.5 py-1 text-[11px]",
                  section.span === span
                    ? "border-[#2d72d2] bg-[#e8f0fb] text-[#215db0]"
                    : "border-[#d5dce1] text-[#404854] hover:bg-[#f6f8fa]",
                )}
              >
                {span === 12 ? "전체" : `${span}/12`}
              </button>
            ))}
          </div>
        </Field>
        <Field label="배경">
          <SwatchRow
            value={section.style.background}
            onChange={(background) =>
              onChange(setSectionStyle(definition, section.id, { background }))
            }
          />
        </Field>
        <Field label="여백">
          <div className="grid grid-cols-4 gap-1">
            {SECTION_PADDINGS.map((padding) => (
              <button
                key={padding.id}
                type="button"
                onClick={() =>
                  onChange(
                    setSectionStyle(definition, section.id, {
                      padding: padding.id,
                    }),
                  )
                }
                className={cn(
                  "rounded border px-1 py-1 text-[10px]",
                  section.style.padding === padding.id
                    ? "border-[#2d72d2] bg-[#e8f0fb] text-[#215db0]"
                    : "border-[#d5dce1] text-[#404854] hover:bg-[#f6f8fa]",
                )}
              >
                {padding.label}
              </button>
            ))}
          </div>
        </Field>
        <Field label="테두리">
          <div className="grid grid-cols-3 gap-1">
            {SECTION_BORDERS.map((border) => (
              <button
                key={border.id}
                type="button"
                onClick={() =>
                  onChange(
                    setSectionStyle(definition, section.id, {
                      border: border.id,
                    }),
                  )
                }
                className={cn(
                  "rounded border px-1.5 py-1 text-[11px]",
                  section.style.border === border.id
                    ? "border-[#2d72d2] bg-[#e8f0fb] text-[#215db0]"
                    : "border-[#d5dce1] text-[#404854] hover:bg-[#f6f8fa]",
                )}
              >
                {border.label}
              </button>
            ))}
          </div>
        </Field>
        <button
          type="button"
          onClick={() => {
            onChange(removeSection(definition, section.id));
            onSelect(null);
          }}
          className="flex w-full items-center justify-center gap-1.5 rounded border border-[#d5dce1] py-1.5 text-[12px] text-[#cd4246] hover:bg-[#fbeaea]"
        >
          <Trash2 className="size-3.5" /> 섹션 삭제
        </button>
      </InspectorShell>
    );
  }

  if (selection.type === "header") {
    return (
      <InspectorShell title="앱 상단 설정">
        <label className="flex items-center gap-2 text-[12px] text-[#404854]">
          <input
            type="checkbox"
            checked={definition.header.visible}
            onChange={() =>
              onChange(setHeaderVisible(definition, !definition.header.visible))
            }
          />
          헤더 표시
        </label>
        <Field label="제목">
          <Input
            className="h-8 text-[12px]"
            value={definition.header.title}
            onChange={(event) =>
              onChange(setHeaderTitle(definition, event.target.value))
            }
          />
        </Field>
        <Field label="헤더에 보여줄 정보">
          <p className="mb-1.5 text-[11px] text-muted-foreground">
            화면 위쪽의 왼쪽·가운데·오른쪽에 핵심 숫자, 버튼, 검색을 배치합니다.
          </p>
          <div className="space-y-2">
            <HeaderSlotEditor
              label="좌측"
              slot={definition.header.slots.left}
              definition={definition}
              objectViews={objectViews}
              actionViews={actionViews}
              onChange={onChange}
              onSelect={onSelect}
            />
            <HeaderSlotEditor
              label="중앙"
              slot={definition.header.slots.center}
              definition={definition}
              objectViews={objectViews}
              actionViews={actionViews}
              onChange={onChange}
              onSelect={onSelect}
            />
            <HeaderSlotEditor
              label="우측"
              slot={definition.header.slots.right}
              definition={definition}
              objectViews={objectViews}
              actionViews={actionViews}
              onChange={onChange}
              onSelect={onSelect}
            />
          </div>
        </Field>
        <p className="text-[11px] text-muted-foreground">
          헤더는 모든 페이지 상단에 고정 표시되며, 페이지가 2개 이상이면 페이지
          탭이 함께 나타납니다.
        </p>
      </InspectorShell>
    );
  }

  if (selection.type === "overlay") {
    const overlay = findOverlay(definition, selection.overlayId);
    if (!overlay) return <InspectorShell title="보조 화면" />;
    const kinds: { id: OverlayKind; label: string }[] = [
      { id: "drawer", label: "옆 상세창" },
      { id: "modal", label: "가운데 확인창" },
    ];
    return (
      <InspectorShell title="보조 화면">
        <Field label="이름">
          <Input
            className="h-8 text-[12px]"
            value={overlay.name}
            onChange={(event) =>
              onChange(
                renameOverlay(definition, overlay.id, event.target.value),
              )
            }
          />
        </Field>
        <Field label="종류">
          <div className="grid grid-cols-2 gap-1">
            {kinds.map((kind) => (
              <button
                key={kind.id}
                type="button"
                onClick={() =>
                  onChange(setOverlayKind(definition, overlay.id, kind.id))
                }
                className={cn(
                  "rounded border px-1.5 py-1 text-[11px]",
                  overlay.kind === kind.id
                    ? "border-[#2d72d2] bg-[#e8f0fb] text-[#215db0]"
                    : "border-[#d5dce1] text-[#404854] hover:bg-[#f6f8fa]",
                )}
              >
                {kind.label}
              </button>
            ))}
          </div>
        </Field>
        {overlay.kind === "drawer" ? (
          <Field label="옆 상세창 너비">
            <Input
              type="number"
              className="h-8 text-[12px]"
              value={overlay.widthPx}
              onChange={(event) =>
                onChange(
                  setOverlayWidth(
                    definition,
                    overlay.id,
                    Number(event.target.value) || 420,
                  ),
                )
              }
            />
          </Field>
        ) : null}
        <p className="text-[11px] text-muted-foreground">
          사용자가 상세 내용이나 확인이 필요할 때 열리는 화면입니다. 현재 보고
          있는 업무와 검색 조건이 그대로 이어집니다.
        </p>
        <button
          type="button"
          onClick={() => {
            onChange(removeOverlay(definition, overlay.id));
            onSelect(null);
          }}
          className="flex w-full items-center justify-center gap-1.5 rounded border border-[#d5dce1] py-1.5 text-[12px] text-[#cd4246] hover:bg-[#fbeaea]"
        >
          <Trash2 className="size-3.5" /> 보조 화면 삭제
        </button>
      </InspectorShell>
    );
  }

  if (selection.type === "variable") {
    const variable = findVariable(definition, selection.variableId);
    if (!variable) return <InspectorShell title="화면 공유 조건" />;
    const types: { id: VariableType; label: string }[] = [
      { id: "string", label: "문자열" },
      { id: "number", label: "숫자" },
      { id: "boolean", label: "예·아니요" },
    ];
    return (
      <InspectorShell title="화면 공유 조건">
        <Field label="이름">
          <Input
            className="h-8 text-[12px]"
            value={variable.name}
            onChange={(event) =>
              onChange(
                renameVariable(definition, variable.id, event.target.value),
              )
            }
          />
        </Field>
        <Field label="유형">
          <div className="grid grid-cols-3 gap-1">
            {types.map((type) => (
              <button
                key={type.id}
                type="button"
                onClick={() =>
                  onChange(setVariableType(definition, variable.id, type.id))
                }
                className={cn(
                  "rounded border px-1.5 py-1 text-[11px]",
                  variable.type === type.id
                    ? "border-[#2d72d2] bg-[#e8f0fb] text-[#215db0]"
                    : "border-[#d5dce1] text-[#404854] hover:bg-[#f6f8fa]",
                )}
              >
                {type.label}
              </button>
            ))}
          </div>
        </Field>
        <Field label="기본값 (비우면 미설정 = 전체)">
          <Input
            className="h-8 text-[12px]"
            value={
              variable.defaultValue === null
                ? ""
                : String(variable.defaultValue)
            }
            onChange={(event) => {
              const text = event.target.value;
              const next =
                text === ""
                  ? null
                  : variable.type === "number"
                    ? Number(text)
                    : variable.type === "boolean"
                      ? text === "true"
                      : text;
              onChange(setVariableDefault(definition, variable.id, next));
            }}
          />
        </Field>
        <p className="text-[11px] text-muted-foreground">
          한 화면에서 고른 검색 조건을 다른 목록·숫자·차트에서도 함께
          사용하도록 연결합니다.
        </p>
        <button
          type="button"
          onClick={() => {
            onChange(removeVariable(definition, variable.id));
            onSelect(null);
          }}
          className="flex w-full items-center justify-center gap-1.5 rounded border border-[#d5dce1] py-1.5 text-[12px] text-[#cd4246] hover:bg-[#fbeaea]"
        >
          <Trash2 className="size-3.5" /> 공유 조건 삭제
        </button>
      </InspectorShell>
    );
  }

  const page = findPage(definition, selection.pageId);
  if (!page) return <InspectorShell title="페이지" />;
  return (
    <InspectorShell title="페이지" subtitle={page.pageId}>
      <Field label="이름">
        <Input
          aria-label="페이지 이름"
          className="h-8 text-[12px]"
          value={page.name}
          onChange={(event) =>
            onChange(renamePage(definition, page.id, event.target.value))
          }
        />
      </Field>
      <Field label="배경">
        <SwatchRow
          value={page.backgroundColor}
          onChange={(background) =>
            onChange(setPageBackground(definition, page.id, background))
          }
        />
      </Field>
      <label className="flex items-center gap-2 text-[12px] text-[#404854]">
        <input
          type="checkbox"
          checked={page.isDefault}
          onChange={() => onChange(setDefaultPage(definition, page.id))}
        />
        기본 페이지로 설정
      </label>
      {definition.pages.length > 1 ? (
        <button
          type="button"
          onClick={() => {
            onChange(removePage(definition, page.id));
            onSelect(null);
          }}
          className="flex w-full items-center justify-center gap-1.5 rounded border border-[#d5dce1] py-1.5 text-[12px] text-[#cd4246] hover:bg-[#fbeaea]"
        >
          <Trash2 className="size-3.5" /> 페이지 삭제
        </button>
      ) : null}
    </InspectorShell>
  );
}

function InspectorShell({
  title,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex min-h-14 shrink-0 items-center border-b border-[#e4e9ed] px-4 py-3">
        <span className="text-[13px] font-bold text-[#1c2127]">
          {title}
        </span>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
        {children}
      </div>
    </div>
  );
}

/** 헤더 단일 슬롯 편집기: 위젯 추가(팔레트) + 배치된 위젯 목록·제거. */
function HeaderSlotEditor({
  label,
  slot,
  definition,
  objectViews,
  actionViews,
  onChange,
  onSelect,
}: {
  label: string;
  slot: AppSection;
  definition: AppDefinition;
  objectViews: readonly FoundryLiteOntologyObjectView[];
  actionViews: readonly FoundryLiteOntologyActionView[];
  onChange: (definition: AppDefinition) => void;
  onSelect: (selection: BuilderSelection) => void;
}) {
  const addToSlot = (kind: WidgetKind) => {
    const suggestion = buildWidgetSuggestion(objectViews, actionViews);
    const widget = createWidget(
      kind,
      widgetDefinition(kind).defaultConfig(suggestion),
    );
    onChange(addWidget(definition, slot.id, widget));
    onSelect({ type: "widget", sectionId: slot.id, widgetId: widget.id });
  };

  return (
    <div className="rounded border border-[#e4e9ed] p-2">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-[#404854]">{label}</span>
        <WidgetPalette
          onPick={addToSlot}
          trigger={
            <button
              type="button"
              className="flex items-center gap-1 rounded border border-[#d5dce1] px-1.5 py-0.5 text-[10px] text-[#404854] hover:bg-[#f6f8fa]"
            >
              <Plus className="size-3" /> 정보 추가
            </button>
          }
        />
      </div>
      {slot.widgets.length === 0 ? (
        <p className="mt-1.5 text-[10px] text-[#a7b1bd]">비어 있음</p>
      ) : (
        <div className="mt-1.5 space-y-1">
          {slot.widgets.map((widget) => {
            const meta = widgetDefinition(widget.kind);
            const Icon = meta.icon;
            return (
              <div
                key={widget.id}
                className="group flex items-center gap-1.5 rounded border border-[#e4e9ed] px-1.5 py-1"
              >
                <button
                  type="button"
                  onClick={() =>
                    onSelect({
                      type: "widget",
                      sectionId: slot.id,
                      widgetId: widget.id,
                    })
                  }
                  className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                >
                  <Icon className="size-3 shrink-0 text-[#8f99a8]" />
                  <span className="min-w-0 flex-1 truncate text-[11px] text-[#1c2127]">
                    {widget.config.title || meta.label}
                  </span>
                </button>
                <button
                  type="button"
                  aria-label="화면 요소 제거"
                  onClick={() => {
                    onChange(removeWidget(definition, widget.id));
                    onSelect(null);
                  }}
                  className="flex size-5 shrink-0 items-center justify-center rounded text-[#8f99a8] hover:bg-[#fbeaea] hover:text-[#cd4246]"
                >
                  <Trash2 className="size-3" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SwatchRow({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {BACKGROUND_SWATCHES.map((swatch) => (
        <button
          key={swatch.id}
          type="button"
          title={swatch.label}
          onClick={() => onChange(swatch.value)}
          className={cn(
            "size-6 rounded border",
            value === swatch.value
              ? "border-[#2d72d2] ring-1 ring-[#2d72d2]"
              : "border-[#d5dce1]",
          )}
          style={{
            background:
              swatch.value === "transparent" ? "#ffffff" : swatch.value,
          }}
        >
          {swatch.value === "transparent" ? (
            <span className="text-[9px] text-[#8f99a8]">∅</span>
          ) : null}
        </button>
      ))}
    </div>
  );
}

/** 섹션 레이아웃 미니 스키매틱 (Palantir 레이아웃 선택기 타일). */
function LayoutSchematic({
  layout,
  active,
}: {
  layout: SectionLayout;
  active: boolean;
}) {
  const line = active ? "bg-[#2d72d2]" : "bg-[#a7b1bd]";
  const box = active ? "border-[#2d72d2]" : "border-[#a7b1bd]";
  const frame =
    "flex h-7 w-full items-stretch justify-center gap-0.5 rounded-[3px] border bg-white p-1 " +
    box;

  if (layout === "columns") {
    return (
      <div className={frame}>
        <span className={cn("flex-1 rounded-[1px]", line)} />
        <span className={cn("flex-1 rounded-[1px]", line)} />
      </div>
    );
  }
  if (layout === "rows") {
    return (
      <div className={frame}>
        <span className={cn("w-2 rounded-[1px]", line)} />
        <span className={cn("w-2 rounded-[1px]", line)} />
        <span className={cn("w-2 rounded-[1px]", line)} />
      </div>
    );
  }
  if (layout === "tabs") {
    return (
      <div className={cn(frame, "flex-col gap-0.5")}>
        <span className="flex gap-0.5">
          <span className={cn("h-1 w-2.5 rounded-[1px]", line)} />
          <span className="h-1 w-2.5 rounded-[1px] bg-[#dbe1e8]" />
        </span>
        <span className={cn("flex-1 rounded-[1px]", line)} />
      </div>
    );
  }
  if (layout === "toolbar") {
    return (
      <div className={cn(frame, "items-center")}>
        <span className="flex w-full items-center gap-0.5">
          <span className={cn("h-2 w-2 rounded-[1px]", line)} />
          <span className={cn("h-2 flex-1 rounded-[1px]", line)} />
          <span className={cn("h-2 w-2 rounded-[1px]", line)} />
        </span>
      </div>
    );
  }
  // flow
  return (
    <div className={cn(frame, "flex-col gap-0.5")}>
      <span className={cn("h-1 w-full rounded-[1px]", line)} />
      <span className={cn("h-1 w-full rounded-[1px]", line)} />
      <span className={cn("h-1 w-full rounded-[1px]", line)} />
    </div>
  );
}

interface WidgetFieldProps {
  field: WidgetConfigField;
  config: import("../lib/app-model").WidgetConfig;
  objectView: FoundryLiteOntologyObjectView | null;
  objectViews: readonly FoundryLiteOntologyObjectView[];
  actionViews: readonly FoundryLiteOntologyActionView[];
  variables: readonly AppVariable[];
  presentation: AppPresentation;
  onPatch: (patch: Partial<import("../lib/app-model").WidgetConfig>) => void;
}

function WidgetField({
  field,
  config,
  objectView,
  objectViews,
  actionViews,
  variables,
  presentation,
  onPatch,
}: WidgetFieldProps) {
  const properties = (objectView?.properties ?? []).filter(
    (property) =>
      !isTechnicalIdentifierProperty(
        property.apiName,
        property.isPrimaryKey === true,
      ),
  );

  switch (field) {
    case "title":
      return (
        <Field label="제목">
          <Input
            className="h-8 text-[12px]"
            value={config.title ?? ""}
            placeholder="(기본 라벨)"
            onChange={(event) => onPatch({ title: event.target.value })}
          />
        </Field>
      );
    case "object":
      return (
        <Field label="보여줄 업무">
          <Select
            value={config.objectApiName ?? undefined}
            onValueChange={(value) => onPatch({ objectApiName: value })}
          >
            <SelectTrigger size="sm" className="h-8 w-full text-[12px]">
              <SelectValue placeholder="업무 선택" />
            </SelectTrigger>
            <SelectContent>
              {objectViews.filter((view) => !view.displayName.startsWith("[LOG]")).map((view) => (
                <SelectItem key={view.apiName} value={view.apiName}>
                  {businessObjectTypeName(view.apiName, view, presentation)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      );
    case "action": {
      const options = actionViews.filter(
        (view) => view.targetObjectApiName === config.objectApiName,
      );
      return (
        <Field label="실행할 업무">
          <Select
            value={config.actionApiName ?? undefined}
            onValueChange={(value) => onPatch({ actionApiName: value })}
          >
            <SelectTrigger size="sm" className="h-8 w-full text-[12px]">
              <SelectValue placeholder="업무 선택" />
            </SelectTrigger>
            <SelectContent>
              {options.map((view) => (
                <SelectItem key={view.apiName} value={view.apiName}>
                  {businessActionName(view.apiName, view, presentation)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      );
    }
    case "actions": {
      const options = actionsForObject(
        actionViews,
        config.objectApiName ?? null,
      );
      const selected = new Set(config.actionApiNames ?? []);
      return (
        <Field label="사용자가 할 수 있는 일">
          <div className="space-y-1 rounded border border-[#e4e9ed] p-2">
            {options.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">
                현재 연결된 업무가 없습니다.
              </p>
            ) : (
              options.map((apiName) => {
                const view = actionViews.find(
                  (item) => item.apiName === apiName,
                );
                return (
                  <label
                    key={apiName}
                    className="flex items-center gap-2 text-[12px]"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(apiName)}
                      onChange={() => {
                        const next = new Set(selected);
                        if (next.has(apiName)) next.delete(apiName);
                        else next.add(apiName);
                        onPatch({ actionApiNames: Array.from(next) });
                      }}
                    />
                    {businessActionName(apiName, view ?? null, presentation)}
                  </label>
                );
              })
            )}
          </div>
        </Field>
      );
    }
    case "properties": {
      const selected = new Set(config.propertyApiNames ?? []);
      return (
        <Field label="보여줄 정보 (비우면 전체)">
          <div className="max-h-40 space-y-1 overflow-auto rounded border border-[#e4e9ed] p-2">
            {properties.map((property) => (
              <label
                key={property.apiName}
                className="flex items-center gap-2 text-[12px]"
              >
                <input
                  type="checkbox"
                  checked={selected.has(property.apiName)}
                  onChange={() => {
                    const next = new Set(selected);
                    if (next.has(property.apiName))
                      next.delete(property.apiName);
                    else next.add(property.apiName);
                    onPatch({ propertyApiNames: Array.from(next) });
                  }}
                />
                <span className="truncate">{property.displayName}</span>
              </label>
            ))}
          </div>
        </Field>
      );
    }
    case "columns":
      return (
        <ColumnConfigEditor
          selected={config.propertyApiNames ?? []}
          properties={properties}
          onChange={(next) => onPatch({ propertyApiNames: next })}
        />
      );
    case "filterProperty":
      return (
        <PropertySelect
          label="필터 기준"
          value={config.filterProperty ?? null}
          properties={properties}
          onChange={(value) => onPatch({ filterProperty: value })}
        />
      );
    case "groupBy":
      return (
        <PropertySelect
          label="묶어서 볼 기준"
          value={config.groupByProperty ?? null}
          properties={properties}
          onChange={(value) => onPatch({ groupByProperty: value })}
        />
      );
    case "series":
      return (
        <PropertySelect
          label="비교할 두 번째 기준"
          value={config.seriesProperty ?? null}
          properties={properties}
          allowNone
          onChange={(value) => onPatch({ seriesProperty: value })}
        />
      );
    case "chartType":
      return (
        <Field label="차트 유형">
          <Select
            value={config.chartType ?? "bar"}
            onValueChange={(value) =>
              onPatch({ chartType: value as ChartType })
            }
          >
            <SelectTrigger size="sm" className="h-8 w-full text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="bar">막대</SelectItem>
              <SelectItem value="horizontalBar">가로막대</SelectItem>
              <SelectItem value="line">라인</SelectItem>
              <SelectItem value="area">영역 (누적)</SelectItem>
              <SelectItem value="scatter">산점도</SelectItem>
            </SelectContent>
          </Select>
        </Field>
      );
    case "dateProperty":
      return (
        <PropertySelect
          label="일정 날짜"
          value={config.dateProperty ?? null}
          properties={properties}
          onChange={(value) => onPatch({ dateProperty: value })}
        />
      );
    case "metricProperty":
      return (
        <PropertySelect
          label="계산할 숫자 정보"
          value={config.metricProperty ?? null}
          properties={properties}
          allowNone
          onChange={(value) => onPatch({ metricProperty: value })}
        />
      );
    case "metric":
      return (
        <Field label="집계">
          <Select
            value={config.metric ?? "count"}
            onValueChange={(value) =>
              onPatch({ metric: value as AggregationMetric })
            }
          >
            <SelectTrigger size="sm" className="h-8 w-full text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {METRICS.map((metric) => (
                <SelectItem key={metric} value={metric}>
                  {metricLabel(metric)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      );
    case "unit":
      return (
        <Field label="단위">
          <Input
            className="h-8 text-[12px]"
            value={config.unit ?? ""}
            placeholder="예: 건, ₩"
            onChange={(event) => onPatch({ unit: event.target.value })}
          />
        </Field>
      );
    case "metricLayout": {
      const current = config.metricLayout ?? "card";
      const options: { id: MetricLayout; label: string }[] = [
        { id: "card", label: "카드" },
        { id: "list", label: "리스트" },
        { id: "tags", label: "태그" },
      ];
      return (
        <Field label="레이아웃">
          <div className="grid grid-cols-3 gap-1">
            {options.map((option) => (
              <button
                key={option.id}
                type="button"
                onClick={() => onPatch({ metricLayout: option.id })}
                className={cn(
                  "rounded border px-1.5 py-1 text-[11px]",
                  current === option.id
                    ? "border-[#2d72d2] bg-[#e8f0fb] text-[#215db0]"
                    : "border-[#d5dce1] text-[#404854] hover:bg-[#f6f8fa]",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
        </Field>
      );
    }
    case "metrics": {
      const effective: MetricSpec[] =
        config.metrics && config.metrics.length > 0
          ? config.metrics
          : [
              {
                label: config.title || metricLabel(config.metric ?? "count"),
                metric: config.metric ?? "count",
                property: config.metricProperty ?? null,
                unit: config.unit,
              },
            ];
      return (
        <MetricsEditor
          metrics={effective}
          properties={properties}
          onChange={(next) => onPatch({ metrics: next })}
        />
      );
    }
    case "text":
      return (
        <Field label="텍스트">
          <Textarea
            className="min-h-24 text-[12px]"
            value={config.text ?? ""}
            onChange={(event) => onPatch({ text: event.target.value })}
          />
        </Field>
      );
    case "setsVariable":
      return (
        <Field label="연결할 공유 조건">
          {variables.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              왼쪽의 공유 조건에서 먼저 기준을 만들어 주세요.
            </p>
          ) : (
            <Select
              value={config.setsVariableId ?? ALL_VALUE}
              onValueChange={(value) =>
                onPatch({
                  setsVariableId: value === ALL_VALUE ? null : value,
                })
              }
            >
              <SelectTrigger size="sm" className="h-8 w-full text-[12px]">
                <SelectValue placeholder="공유 조건 선택" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_VALUE}>(없음 · 로컬 필터)</SelectItem>
                {variables.map((variable) => (
                  <SelectItem key={variable.id} value={variable.id}>
                    {variable.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      );
    case "variableFilter":
      return (
        <VariableFilterEditor
          filters={config.variableFilters ?? []}
          properties={properties}
          variables={variables}
          onChange={(next) => onPatch({ variableFilters: next })}
        />
      );
    default:
      return null;
  }
}

/** 여러 화면이 같은 선택 조건을 공유하도록 연결한다. */
function VariableFilterEditor({
  filters,
  properties,
  variables,
  onChange,
}: {
  filters: VariableFilter[];
  properties: FoundryLiteOntologyObjectView["properties"];
  variables: readonly AppVariable[];
  onChange: (filters: VariableFilter[]) => void;
}) {
  if (variables.length === 0) {
    return (
      <Field label="화면 공유 조건">
        <p className="text-[11px] text-muted-foreground">
          왼쪽의 공유 조건에서 먼저 기준을 만들어 주세요.
        </p>
      </Field>
    );
  }
  const update = (index: number, patch: Partial<VariableFilter>) =>
    onChange(
      filters.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    );
  const add = () =>
    onChange([
      ...filters,
      {
        property: properties[0]?.apiName ?? "",
        variableId: variables[0].id,
      },
    ]);
  const remove = (index: number) =>
    onChange(filters.filter((_, i) => i !== index));

  return (
    <Field label="화면 공유 조건 연결">
      <div className="space-y-2">
        {filters.map((filter, index) => (
          <div
            key={index}
            className="space-y-1.5 rounded border border-[#e4e9ed] p-2"
          >
            <div className="flex items-center gap-1">
              <Select
                value={filter.property || undefined}
                onValueChange={(value) => update(index, { property: value })}
              >
                <SelectTrigger size="sm" className="h-7 flex-1 text-[12px]">
                  <SelectValue placeholder="업무 정보" />
                </SelectTrigger>
                <SelectContent>
                  {properties.map((property) => (
                    <SelectItem key={property.apiName} value={property.apiName}>
                      {property.displayName}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <button
                type="button"
                aria-label="필터 제거"
                onClick={() => remove(index)}
                className="flex size-6 items-center justify-center rounded text-[#8f99a8] hover:bg-[#fbeaea] hover:text-[#cd4246]"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
            <Select
              value={filter.variableId}
              onValueChange={(value) => update(index, { variableId: value })}
            >
              <SelectTrigger size="sm" className="h-7 w-full text-[12px]">
                <SelectValue placeholder="공유 조건" />
              </SelectTrigger>
              <SelectContent>
                {variables.map((variable) => (
                  <SelectItem key={variable.id} value={variable.id}>
                    {variable.name}와 함께 사용
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ))}
        <button
          type="button"
          onClick={add}
          className="w-full rounded border border-dashed border-[#c5ccd3] py-1.5 text-[11px] text-[#5f6b7c] hover:border-[#2d72d2] hover:text-[#215db0]"
        >
          + 공유 조건 연결
        </button>
      </div>
    </Field>
  );
}

function PropertySelect({
  label,
  value,
  properties,
  allowNone,
  onChange,
}: {
  label: string;
  value: string | null;
  properties: FoundryLiteOntologyObjectView["properties"];
  allowNone?: boolean;
  onChange: (value: string | null) => void;
}) {
  return (
    <Field label={label}>
      <Select
        value={value ?? (allowNone ? ALL_VALUE : undefined)}
        onValueChange={(next) => onChange(next === ALL_VALUE ? null : next)}
      >
        <SelectTrigger size="sm" className="h-8 w-full text-[12px]">
          <SelectValue placeholder="업무 정보 선택" />
        </SelectTrigger>
        <SelectContent>
          {allowNone ? <SelectItem value={ALL_VALUE}>(없음)</SelectItem> : null}
          {properties.map((property) => (
            <SelectItem key={property.apiName} value={property.apiName}>
              {property.displayName}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}

/**
 * 객체 테이블 컬럼 구성 (Palantir Column Configuration 클론):
 * 순서 있는 선택 컬럼 리스트(드래그 재정렬) + 컬럼 추가 피커 + 모든 속성/초기화.
 * config.propertyApiNames(순서 = 표시 순서)를 편집한다. 빈 배열이면 런타임은 전체 표시.
 */
function ColumnConfigEditor({
  selected,
  properties,
  onChange,
}: {
  selected: string[];
  properties: FoundryLiteOntologyObjectView["properties"];
  onChange: (next: string[]) => void;
}) {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [query, setQuery] = useState("");

  const isExplicit = selected.length > 0;
  const columns = isExplicit
    ? selected
    : properties.map((property) => property.apiName);
  const nameFor = (apiName: string) =>
    properties.find((property) => property.apiName === apiName)?.displayName ??
    apiName;

  const available = useMemo(
    () =>
      properties
        .filter((property) => !columns.includes(property.apiName))
        .filter((property) =>
          `${property.displayName} ${property.apiName}`
            .toLowerCase()
            .includes(query.trim().toLowerCase()),
        ),
    [properties, columns, query],
  );

  const move = (from: number, to: number) => {
    if (from === to) return;
    const next = [...columns];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };

  return (
    <Field label="목록에 보여줄 정보">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[10px] text-muted-foreground">
          {isExplicit
            ? `${columns.length}개 정보`
            : "모든 정보 표시"}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onChange(properties.map((p) => p.apiName))}
            className="text-[11px] font-medium text-[#2d72d2] hover:underline"
          >
            모두 표시
          </button>
          {isExplicit ? (
            <button
              type="button"
              onClick={() => onChange([])}
              className="text-[11px] font-medium text-[#cd4246] hover:underline"
            >
              초기화
            </button>
          ) : null}
        </div>
      </div>

      <div className="space-y-1">
        {columns.map((apiName, index) => (
          <div
            key={apiName}
            draggable
            onDragStart={() => setDragIndex(index)}
            onDragOver={(event) => {
              event.preventDefault();
              if (dragIndex !== null && dragIndex !== index) {
                move(dragIndex, index);
                setDragIndex(index);
              }
            }}
            onDragEnd={() => setDragIndex(null)}
            className={cn(
              "group flex items-center gap-1.5 rounded border border-[#e4e9ed] bg-white px-1.5 py-1",
              dragIndex === index && "opacity-40",
            )}
          >
            <span className="shrink-0 cursor-grab text-[#c5ccd3] group-hover:text-[#8f99a8] active:cursor-grabbing">
              <GripVertical className="size-3.5" />
            </span>
            <span className="min-w-0 flex-1 truncate text-[12px] text-[#1c2127]">
              {nameFor(apiName)}
            </span>
            <button
              type="button"
              aria-label={`${nameFor(apiName)} 정보 제거`}
              onClick={() => onChange(columns.filter((c) => c !== apiName))}
              className="flex size-5 shrink-0 items-center justify-center rounded text-[#8f99a8] hover:bg-[#fbeaea] hover:text-[#cd4246]"
            >
              <X className="size-3" />
            </button>
          </div>
        ))}
      </div>

      {showPicker ? (
        <div className="mt-1.5 rounded border border-[#d5dce1]">
          <div className="border-b border-[#eef1f4] p-1.5">
            <Input
              autoFocus
              className="h-7 text-[12px]"
              value={query}
              placeholder="보여줄 정보 검색..."
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="max-h-40 overflow-auto p-1">
            {available.length === 0 ? (
              <p className="px-1.5 py-2 text-center text-[11px] text-[#8f99a8]">
                추가할 정보가 없습니다.
              </p>
            ) : (
              available.map((property) => (
                <button
                  key={property.apiName}
                  type="button"
                  onClick={() => {
                    onChange([...columns, property.apiName]);
                    setQuery("");
                  }}
                  className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-[#f0f4f9]"
                >
                  <Plus className="size-3 shrink-0 text-[#2d72d2]" />
                  <span className="min-w-0 flex-1 truncate text-[12px] text-[#1c2127]">
                    {property.displayName}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setShowPicker(true)}
          className="mt-1.5 flex w-full items-center justify-center gap-1 rounded border border-dashed border-[#c5ccd3] py-1.5 text-[11px] text-[#5f6b7c] hover:border-[#2d72d2] hover:text-[#215db0]"
        >
          <Plus className="size-3" /> 보여줄 정보 추가
        </button>
      )}
    </Field>
  );
}

function MetricsEditor({
  metrics,
  properties,
  onChange,
}: {
  metrics: MetricSpec[];
  properties: FoundryLiteOntologyObjectView["properties"];
  onChange: (metrics: MetricSpec[]) => void;
}) {
  const update = (index: number, patch: Partial<MetricSpec>) =>
    onChange(
      metrics.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    );
  const add = () =>
    onChange([...metrics, { label: "지표", metric: "count", property: null }]);
  const remove = (index: number) =>
    onChange(metrics.filter((_, i) => i !== index));

  return (
    <Field label="지표">
      <div className="space-y-2">
        {metrics.map((item, index) => (
          <div
            key={index}
            className="space-y-1.5 rounded border border-[#e4e9ed] p-2"
          >
            <div className="flex items-center gap-1">
              <Input
                className="h-7 flex-1 text-[12px]"
                value={item.label}
                placeholder="라벨"
                onChange={(event) =>
                  update(index, { label: event.target.value })
                }
              />
              <button
                type="button"
                aria-label="지표 제거"
                disabled={metrics.length <= 1}
                onClick={() => remove(index)}
                className="flex size-6 items-center justify-center rounded text-[#8f99a8] hover:bg-[#fbeaea] hover:text-[#cd4246] disabled:opacity-30"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
            <div className="flex gap-1">
              <Select
                value={item.metric}
                onValueChange={(value) =>
                  update(index, { metric: value as AggregationMetric })
                }
              >
                <SelectTrigger size="sm" className="h-7 flex-1 text-[12px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {METRICS.map((metric) => (
                    <SelectItem key={metric} value={metric}>
                      {metricLabel(metric)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {item.metric !== "count" ? (
                <Select
                  value={item.property ?? ALL_VALUE}
                  onValueChange={(value) =>
                    update(index, {
                      property: value === ALL_VALUE ? null : value,
                    })
                  }
                >
                  <SelectTrigger size="sm" className="h-7 flex-1 text-[12px]">
                    <SelectValue placeholder="업무 정보" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ALL_VALUE}>(없음)</SelectItem>
                    {properties.map((property) => (
                      <SelectItem
                        key={property.apiName}
                        value={property.apiName}
                      >
                        {property.displayName}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : null}
            </div>
          </div>
        ))}
        <button
          type="button"
          onClick={add}
          className="w-full rounded border border-dashed border-[#c5ccd3] py-1.5 text-[11px] text-[#5f6b7c] hover:border-[#2d72d2] hover:text-[#215db0]"
        >
          + 지표 추가
        </button>
      </div>
    </Field>
  );
}
