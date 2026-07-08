import type { OntologyCatalog } from "@foundry-lite/sdk";
import type {
  OntologyDraftObjectType,
  OntologyDraftProperty,
} from "@foundry-lite/sdk/ontology-draft";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import { Eye, KeyRound, Pencil, PlusCircle } from "lucide-react";
import { type ReactNode, useState } from "react";

import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { truncateMiddle } from "../lib/ontology-view";
import { PropertyTypeIcon } from "./PropertyTypeIcon";

/** Overview 필드 한 행 (grid-cols-[128px_1fr], 라벨 회색 #9DA4AF). */
function MetaRow({
  label,
  children,
  isMono = false,
}: {
  label: string;
  children: ReactNode;
  isMono?: boolean;
}) {
  return (
    <div className="grid grid-cols-[128px_1fr] items-start gap-2 py-1.5">
      <span className="pt-0.5 text-xs text-[#9da4af]">{label}</span>
      <div
        className={isMono ? "min-w-0 font-mono text-[11px]" : "min-w-0 text-xs"}
      >
        {children}
      </div>
    </div>
  );
}

/** 우측 좁은 카드의 라벨↔값 행 (Status/Visibility/Index status/Edits). */
function StatusRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-2 py-1.5">
      <span className="text-xs text-[#9da4af]">{label}</span>
      <div className="text-xs">{children}</div>
    </div>
  );
}

interface ObjectTypeOverviewFormProps {
  draftObject: OntologyDraftObjectType;
  objectView: FoundryLiteOntologyObjectView | null;
  catalog: OntologyCatalog | null;
  versionLabel: string | null;
  /** 브랜치가 열려 있으면 인라인 편집을 허용한다. */
  isEditable: boolean;
  /** 선택된 브랜치가 없으면 인덱스가 아직 안 됨(경고 배지). */
  isIndexedOnBranch: boolean;
  onUpdateObjectType: (
    patch: Partial<Omit<OntologyDraftObjectType, "apiName">>,
  ) => void;
  onSelectProperty: (apiName: string) => void;
  onRequestNewProperty: () => void;
}

/**
 * Overview 섹션: 좌측 메타 카드(인라인 편집) + 우측 Status/Visibility 카드,
 * 하단 Properties · Action types 2열 카드.
 */
export function ObjectTypeOverviewForm({
  draftObject,
  objectView,
  catalog,
  versionLabel,
  isEditable,
  isIndexedOnBranch,
  onUpdateObjectType,
  onSelectProperty,
  onRequestNewProperty,
}: ObjectTypeOverviewFormProps) {
  const backingLabel = describeBacking(draftObject);
  const actions = objectView?.actions ?? [];

  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-[1fr_340px]">
        <div className="rounded border bg-card p-4">
          <MetaRow label="이름 (Display name)">
            <InlineText
              value={draftObject.displayName ?? draftObject.apiName}
              placeholder="표시 이름"
              isEditable={isEditable}
              onCommit={(value) =>
                onUpdateObjectType({ displayName: value || null })
              }
            />
          </MetaRow>
          <MetaRow label="설명">
            <InlineTextarea
              value={draftObject.description ?? ""}
              placeholder="설명 없음"
              isEditable={isEditable}
              onCommit={(value) =>
                onUpdateObjectType({ description: value || null })
              }
            />
          </MetaRow>
          <MetaRow label="기본 키" isMono>
            <span className="inline-flex items-center gap-1">
              <KeyRound className="size-3 text-[#7d6bc4]" />
              {draftObject.primaryKey}
            </span>
          </MetaRow>
          <MetaRow label="타이틀 속성" isMono>
            {draftObject.titleProperty ?? "—"}
          </MetaRow>
          <MetaRow label="백킹 데이터소스" isMono>
            {backingLabel}
          </MetaRow>
          <MetaRow label="온톨로지">기본 온톨로지</MetaRow>
          <MetaRow label="API 이름" isMono>
            {draftObject.apiName}
          </MetaRow>
          <Separator className="my-2" />
          <MetaRow label="RID" isMono>
            {objectView
              ? truncateMiddle(
                  `ri.ontology.main.object-type.${objectView.apiName}`,
                  40,
                )
              : "—"}
          </MetaRow>
          <MetaRow label="ID" isMono>
            {objectView
              ? truncateMiddle(`generated-${objectView.apiName}`, 40)
              : draftObject.apiName}
          </MetaRow>
        </div>

        <div className="space-y-3">
          <div className="rounded border bg-card p-4">
            <StatusRow label="Status">
              <span className="inline-flex items-center rounded bg-[#eff0f2] px-2 py-0.5 text-xs text-[#404854]">
                활성
              </span>
            </StatusRow>
            <StatusRow label="Visibility">
              <span className="inline-flex items-center gap-1 rounded bg-[#ecf0fa] px-2 py-0.5 text-xs text-[#325caa]">
                <Eye className="size-3" />
                보통
              </span>
            </StatusRow>
            <StatusRow label="Index status">
              {isIndexedOnBranch ? (
                <span className="inline-flex items-center rounded bg-[#eff0f2] px-2 py-0.5 text-xs text-[#404854]">
                  색인됨
                </span>
              ) : (
                <span className="inline-flex items-center rounded bg-[#f4e7d6] px-2 py-0.5 text-xs text-[#8b5923]">
                  브랜치에 색인 안 됨
                </span>
              )}
            </StatusRow>
            <StatusRow label="Edits">
              <span className="inline-flex items-center rounded bg-[#eff0f2] px-2 py-0.5 text-xs text-[#8a9099]">
                비활성
              </span>
            </StatusRow>
          </div>
          <div className="rounded border bg-card p-4">
            <MetaRow label="버전" isMono>
              {versionLabel ?? "—"}
            </MetaRow>
            <MetaRow label="버전 ID" isMono>
              {catalog ? truncateMiddle(catalog.ontologyVersionId, 34) : "—"}
            </MetaRow>
          </div>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <PropertiesCard
          properties={draftObject.properties}
          primaryKey={draftObject.primaryKey}
          titleProperty={draftObject.titleProperty ?? null}
          onSelectProperty={onSelectProperty}
          onRequestNew={onRequestNewProperty}
        />
        <div className="rounded border bg-card">
          <CardHeader
            title="액션 타입"
            count={actions.length}
            onRequestNew={null}
          />
          <Separator />
          <div className="p-3">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">
                References {draftObject.displayName ?? draftObject.apiName}
              </span>
              <span className="rounded bg-[#eff0f2] px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                {actions.length}
              </span>
            </div>
            {actions.length === 0 ? (
              <p className="py-2 text-xs text-muted-foreground">
                이 객체 타입을 참조하는 액션 타입이 없습니다.
              </p>
            ) : (
              <ul className="space-y-1">
                {actions.map((action) => (
                  <li
                    key={action.apiName}
                    className="flex items-center gap-2 py-1 text-xs"
                  >
                    <Pencil className="size-3.5 text-primary" />
                    <span className="min-w-0 flex-1 truncate">
                      {action.displayName}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 카드 헤더: 제목 + 카운트 배지 + 우측 'New' 파랑 링크. */
function CardHeader({
  title,
  count,
  onRequestNew,
}: {
  title: string;
  count: number;
  onRequestNew: (() => void) | null;
}) {
  return (
    <div className="flex h-11 items-center gap-2 px-3">
      <span className="text-[13px] font-semibold">{title}</span>
      <span className="rounded bg-[#eff0f2] px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
        {count}
      </span>
      {onRequestNew ? (
        <button
          type="button"
          onClick={onRequestNew}
          className="ml-auto flex items-center gap-1 text-xs font-medium text-[#5d88c5] hover:underline"
        >
          <PlusCircle className="size-3.5" />
          New
        </button>
      ) : null}
    </div>
  );
}

function PropertiesCard({
  properties,
  primaryKey,
  titleProperty,
  onSelectProperty,
  onRequestNew,
}: {
  properties: OntologyDraftProperty[];
  primaryKey: string;
  titleProperty: string | null;
  onSelectProperty: (apiName: string) => void;
  onRequestNew: () => void;
}) {
  const pinned = properties.filter(
    (property) =>
      property.apiName === primaryKey || property.apiName === titleProperty,
  );
  const rest = properties.filter(
    (property) =>
      property.apiName !== primaryKey && property.apiName !== titleProperty,
  );
  return (
    <div className="rounded border bg-card">
      <CardHeader
        title="속성"
        count={properties.length}
        onRequestNew={onRequestNew}
      />
      <Separator />
      <ul className="p-1.5">
        {pinned.map((property) => (
          <PropertyRow
            key={property.apiName}
            property={property}
            primaryKey={primaryKey}
            titleProperty={titleProperty}
            onSelect={onSelectProperty}
          />
        ))}
        {pinned.length > 0 && rest.length > 0 ? (
          <li className="my-1">
            <Separator />
          </li>
        ) : null}
        {rest.map((property) => (
          <PropertyRow
            key={property.apiName}
            property={property}
            primaryKey={primaryKey}
            titleProperty={titleProperty}
            onSelect={onSelectProperty}
          />
        ))}
      </ul>
    </div>
  );
}

function PropertyRow({
  property,
  primaryKey,
  titleProperty,
  onSelect,
}: {
  property: OntologyDraftProperty;
  primaryKey: string;
  titleProperty: string | null;
  onSelect: (apiName: string) => void;
}) {
  const isPrimaryKey = property.apiName === primaryKey;
  const isTitle = property.apiName === titleProperty;
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(property.apiName)}
        className="flex w-full items-center gap-2 rounded px-1.5 py-1.5 text-left hover:bg-muted/60"
      >
        <PropertyTypeIcon dataType={property.type} />
        <span className="min-w-0 flex-1 truncate text-xs">
          {property.displayName ?? property.apiName}
        </span>
        {isTitle ? (
          <span className="rounded bg-[#e1f2ef] px-1.5 py-0.5 text-[10px] font-medium text-[#0f766e]">
            Title
          </span>
        ) : null}
        {isPrimaryKey ? (
          <span className="rounded bg-[#e6e1f5] px-1.5 py-0.5 text-[10px] font-medium text-[#5b4a9e]">
            Primary key
          </span>
        ) : null}
        {property.classification ? (
          <span className="rounded bg-[#f4e7d6] px-1.5 py-0.5 text-[10px] font-medium text-[#8b5923]">
            분류
          </span>
        ) : null}
      </button>
    </li>
  );
}

/** 인라인 편집 텍스트: 읽기 모드 hover 시 연필, 클릭 시 Input으로 전환. */
function InlineText({
  value,
  placeholder,
  isEditable,
  onCommit,
}: {
  value: string;
  placeholder: string;
  isEditable: boolean;
  onCommit: (value: string) => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  if (!isEditable) {
    return <span>{value || placeholder}</span>;
  }
  if (isEditing) {
    return (
      <Input
        autoFocus
        value={draft}
        placeholder={placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          setIsEditing(false);
          if (draft.trim() !== value) onCommit(draft.trim());
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setDraft(value);
            setIsEditing(false);
          }
        }}
        className="h-7 text-xs"
      />
    );
  }
  return (
    <button
      type="button"
      onClick={() => {
        setDraft(value);
        setIsEditing(true);
      }}
      className="group flex w-full items-center gap-1.5 text-left"
    >
      <span className={cn(!value && "text-muted-foreground")}>
        {value || placeholder}
      </span>
      <Pencil className="size-3 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  );
}

function InlineTextarea({
  value,
  placeholder,
  isEditable,
  onCommit,
}: {
  value: string;
  placeholder: string;
  isEditable: boolean;
  onCommit: (value: string) => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  if (!isEditable) {
    return (
      <span className={cn(!value && "text-muted-foreground")}>
        {value || placeholder}
      </span>
    );
  }
  if (isEditing) {
    return (
      <Textarea
        autoFocus
        value={draft}
        placeholder={placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          setIsEditing(false);
          if (draft.trim() !== value) onCommit(draft.trim());
        }}
        className="min-h-16 text-xs"
      />
    );
  }
  return (
    <button
      type="button"
      onClick={() => {
        setDraft(value);
        setIsEditing(true);
      }}
      className="group flex w-full items-start gap-1.5 text-left"
    >
      <span className={cn("min-w-0", !value && "text-muted-foreground")}>
        {value || placeholder}
      </span>
      <Pencil className="mt-0.5 size-3 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  );
}

function describeBacking(objectType: OntologyDraftObjectType): string {
  const backing = objectType.backing;
  const datasources = backing.datasources;
  if (Array.isArray(datasources) && datasources.length > 0) {
    return datasources
      .map((item) =>
        typeof item === "object" && item !== null && "dataset" in item
          ? String((item as { dataset?: unknown }).dataset ?? "—")
          : "—",
      )
      .join(", ");
  }
  const dataset = backing.dataset;
  return typeof dataset === "string" ? dataset : "—";
}
