import type { OntologyCatalog } from "@foundry-lite/sdk";
import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { useFoundryLiteProvidedOntologyResourceInsights } from "@foundry-lite/sdk/react";
import { ArrowLeftRight, KeyRound, Type, X, Zap } from "lucide-react";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";

import type { OntologyResourceRow } from "../lib/ontology-view";
import { RESOURCE_KIND_LABELS, truncateMiddle } from "../lib/ontology-view";
import { ResourceKindIcon } from "./ResourceTable";

const INSIGHT_RESOURCE_TYPES: Record<string, string> = {
  objectType: "object_type",
  linkType: "link_type",
  actionType: "action_type",
};

/** key-value 속성 그리드 한 행: 좌측 회색 라벨 + 좌측 정렬 값. */
function MetaRow({
  label,
  value,
  isMono = false,
}: {
  label: string;
  value: ReactNode;
  isMono?: boolean;
}) {
  return (
    <div className="grid grid-cols-[96px_1fr] items-start gap-2 py-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span
        className={
          isMono
            ? "min-w-0 truncate font-mono text-[11px]"
            : "min-w-0 truncate text-xs"
        }
      >
        {value}
      </span>
    </div>
  );
}

function recordCount(value: unknown): string {
  if (typeof value === "number" || typeof value === "string") {
    return String(value);
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.count === "number") return String(record.count);
    if (typeof record.total === "number") return String(record.total);
    return `${Object.keys(record).length}개 항목`;
  }
  return "—";
}

function detailMetaRows(
  row: OntologyResourceRow,
  objectView: FoundryLiteOntologyObjectView | null,
  catalog: OntologyCatalog | null,
): Array<{ label: string; value: ReactNode; isMono?: boolean }> {
  if (row.kind === "objectType" && objectView) {
    const backingDataset = objectView.objectType.backing.dataset;
    return [
      { label: "API 이름", value: objectView.apiName, isMono: true },
      { label: "설명", value: objectView.description ?? "설명 없음" },
      { label: "기본 키", value: objectView.primaryKeyProperty, isMono: true },
      {
        label: "타이틀 속성",
        value: objectView.objectType.titleProperty ?? "—",
        isMono: true,
      },
      {
        label: "백킹 데이터셋",
        value: typeof backingDataset === "string" ? backingDataset : "—",
        isMono: true,
      },
      { label: "온톨로지", value: "기본 온톨로지" },
    ];
  }
  if (row.kind === "linkType") {
    const link = catalog?.linkTypes.find(
      (item) => item.apiName === row.apiName,
    );
    return [
      { label: "API 이름", value: row.apiName, isMono: true },
      { label: "시작 객체", value: link?.fromObjectType ?? "—", isMono: true },
      { label: "대상 객체", value: link?.toObjectType ?? "—", isMono: true },
      { label: "카디널리티", value: link?.cardinality ?? "—", isMono: true },
      { label: "온톨로지", value: "기본 온톨로지" },
    ];
  }
  if (row.kind === "actionType") {
    return [
      { label: "API 이름", value: row.apiName, isMono: true },
      { label: "설명", value: row.subtitle ?? "설명 없음" },
      { label: "온톨로지", value: "기본 온톨로지" },
    ];
  }
  if (row.kind === "interface") {
    const item = (catalog?.interfaces ?? []).find(
      (candidate) => candidate.apiName === row.apiName,
    );
    return [
      { label: "API 이름", value: row.apiName, isMono: true },
      {
        label: "구현 객체",
        value: item ? item.implementedBy.join(", ") || "없음" : "—",
        isMono: true,
      },
      { label: "온톨로지", value: "기본 온톨로지" },
    ];
  }
  const fn = (catalog?.functionTypes ?? []).find(
    (candidate) => candidate.apiName === row.apiName,
  );
  return [
    { label: "API 이름", value: row.apiName, isMono: true },
    { label: "런타임", value: fn?.runtime ?? "—", isMono: true },
    { label: "입력", value: `${fn?.inputs.length ?? 0}개` },
    {
      label: "허용 역할",
      value: fn?.allowedRoles ? fn.allowedRoles.join(", ") : "전체",
      isMono: true,
    },
  ];
}

/** 의존 리소스 레코드에서 표시용 이름을 뽑는다. */
function dependentNames(
  records: ReadonlyArray<Record<string, unknown>>,
): string[] {
  return records.map((record) => {
    if (typeof record.displayName === "string") return record.displayName;
    if (typeof record.apiName === "string") return record.apiName;
    if (typeof record.name === "string") return record.name;
    return "알 수 없는 리소스";
  });
}

/** parameterSchema(JSON Schema)에서 파라미터 이름/타입 목록을 뽑는다. */
function actionParameterEntries(
  actionView: FoundryLiteOntologyActionView,
): Array<{ name: string; dataType: string }> {
  const properties = actionView.action.parameterSchema?.properties;
  if (!properties || typeof properties !== "object") return [];
  return Object.entries(properties as Record<string, unknown>).map(
    ([name, schema]) => {
      const dataType =
        schema && typeof schema === "object" && "type" in schema
          ? String((schema as Record<string, unknown>).type)
          : "unknown";
      return { name, dataType };
    },
  );
}

/** 다크 그레이 헤더 미니 카드 (Input/Rules 다이어그램 노드). */
function DiagramNode({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0 flex-1 border border-[#c9ced6] bg-card shadow-sm">
      <div className="bg-[#738191] px-2.5 py-1.5 text-xs font-semibold text-white">
        {title}
      </div>
      {children}
    </div>
  );
}

/** 액션 타입 개요: Input/Rules 미니 다이어그램 카드. */
function ActionOverviewCard({
  actionView,
}: {
  actionView: FoundryLiteOntologyActionView;
}) {
  const parameters = actionParameterEntries(actionView);
  return (
    <div className="rounded border bg-card">
      <div className="border-b p-3 text-xs font-semibold">액션 타입 개요</div>
      <div className="bg-[#eeeff2] p-3">
        <div className="flex items-start gap-3">
          <DiagramNode title="입력">
            {parameters.length === 0 ? (
              <div className="px-2.5 py-2 text-[11px] text-muted-foreground">
                파라미터 없음
              </div>
            ) : (
              <ul className="divide-y">
                {parameters.map((parameter) => (
                  <li
                    key={parameter.name}
                    className="flex items-center gap-1.5 px-2.5 py-1.5"
                  >
                    <Type className="size-3 shrink-0 text-primary" />
                    <span className="min-w-0 flex-1 truncate text-xs">
                      {parameter.name}
                    </span>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {parameter.dataType}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </DiagramNode>
          <DiagramNode title="규칙">
            <div className="px-2.5 py-2">
              <div className="flex items-center gap-1.5 text-xs">
                <span className="font-semibold">수정</span>
                <span className="flex size-4 items-center justify-center rounded bg-primary text-white">
                  <Zap className="size-2.5" />
                </span>
                <span className="min-w-0 truncate">
                  {actionView.targetObjectApiName}
                </span>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                파라미터 {actionView.parameterCount}개로 대상 객체를
                업데이트합니다.
              </p>
            </div>
          </DiagramNode>
        </div>
      </div>
    </div>
  );
}

interface ResourceDetailPanelProps {
  row: OntologyResourceRow;
  objectView: FoundryLiteOntologyObjectView | null;
  actionView: FoundryLiteOntologyActionView | null;
  catalog: OntologyCatalog | null;
  versionLabel: string | null;
  onClose: () => void;
}

/** 우측 상세 패널: 컬러 사각 아이콘+타이틀, key-value 그리드, 미니 다이어그램, 의존 리소스. */
export function ResourceDetailPanel({
  row,
  objectView,
  actionView,
  catalog,
  versionLabel,
  onClose,
}: ResourceDetailPanelProps) {
  const insightResourceType = INSIGHT_RESOURCE_TYPES[row.kind] ?? null;
  const insights = useFoundryLiteProvidedOntologyResourceInsights(
    insightResourceType ?? "object_type",
    insightResourceType ? row.apiName : null,
    { windowDays: 30 },
  );

  const dependentActionTypes = dependentNames(
    insights.dependents?.actionTypes ?? [],
  );
  const dependentLinkTypes = dependentNames(
    insights.dependents?.linkTypes ?? [],
  );
  const dependentCount =
    dependentActionTypes.length + dependentLinkTypes.length;

  return (
    <div className="w-[400px] shrink-0 space-y-3">
      <div className="rounded border bg-card">
        <div className="flex items-start gap-2.5 p-3">
          <ResourceKindIcon kind={row.kind} className="size-9 rounded-[4px]" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-bold">
              {row.displayName}
            </div>
            <div className="text-xs text-muted-foreground">
              {RESOURCE_KIND_LABELS[row.kind]}
              {row.kind === "objectType" && objectView
                ? ` · 속성 ${objectView.propertyCount}개`
                : null}
            </div>
          </div>
          <Button
            size="icon"
            variant="ghost"
            className="size-6"
            onClick={onClose}
            aria-label="상세 패널 닫기"
          >
            <X />
          </Button>
        </div>
        <Separator />
        <div className="px-3 py-1.5">
          {detailMetaRows(row, objectView, catalog).map((meta) => (
            <MetaRow key={meta.label} {...meta} />
          ))}
        </div>
        <Separator />
        <div className="px-3 py-1.5">
          <MetaRow
            label="상태"
            value={
              row.status === "experimental" ? (
                <span className="inline-flex items-center rounded bg-[#f8f1ea] px-2 py-0.5 text-xs text-[#8b5923]">
                  실험적
                </span>
              ) : (
                <span className="inline-flex items-center rounded bg-[#eff0f2] px-2 py-0.5 text-xs text-[#404854]">
                  활성
                </span>
              )
            }
          />
          <MetaRow label="버전" value={versionLabel ?? "—"} isMono />
          <MetaRow
            label="버전 ID"
            value={
              catalog ? truncateMiddle(catalog.ontologyVersionId, 26) : "—"
            }
            isMono
          />
        </div>
      </div>

      {row.kind === "actionType" && actionView ? (
        <ActionOverviewCard actionView={actionView} />
      ) : null}

      {row.kind === "objectType" && objectView ? (
        <ObjectPropertyCard objectView={objectView} />
      ) : null}

      {insightResourceType ? (
        <div className="rounded border bg-card">
          <div className="flex items-center gap-2 border-b p-3">
            <span className="text-xs font-semibold">의존 리소스</span>
            <span className="rounded bg-[#eff0f2] px-1.5 py-0.5 font-mono text-[11px] text-foreground/80">
              {insights.isLoading ? "…" : dependentCount}
            </span>
          </div>
          {insights.isLoading ? (
            <Skeleton className="m-3 h-16 w-auto" />
          ) : insights.error ? (
            <div className="p-3">
              <ErrorState error={insights.error} onRetry={insights.reload} />
            </div>
          ) : (
            <>
              <ul className="p-1.5">
                <DependentCategoryRow
                  icon={<Zap className="size-3.5 text-muted-foreground" />}
                  label="액션 타입"
                  items={dependentActionTypes}
                />
                <DependentCategoryRow
                  icon={
                    <ArrowLeftRight className="size-3.5 text-muted-foreground" />
                  }
                  label="링크 타입"
                  items={dependentLinkTypes}
                />
              </ul>
              <Separator />
              <div className="px-3 py-1.5">
                <MetaRow
                  label="액션 실행"
                  value={recordCount(insights.usage?.actionRuns)}
                  isMono
                />
                <MetaRow
                  label="인덱스 실행"
                  value={recordCount(insights.usage?.indexRuns)}
                  isMono
                />
                <MetaRow
                  label="감사 이벤트"
                  value={recordCount(insights.usage?.auditEvents)}
                  isMono
                />
                {insights.notes.length > 0 ? (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {insights.notes[0]} (최근 {insights.windowDays ?? 30}일)
                  </p>
                ) : null}
              </div>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** 의존 리소스 카테고리 행: 아이콘 + 라벨 + 카운트 배지 + 항목 이름. */
function DependentCategoryRow({
  icon,
  label,
  items,
}: {
  icon: ReactNode;
  label: string;
  items: readonly string[];
}) {
  return (
    <li className="rounded px-1.5 py-1.5">
      <div className="flex items-center gap-2">
        {icon}
        <span className="min-w-0 flex-1 truncate text-xs">{label}</span>
        <span className="min-w-6 rounded bg-[#eff0f2] px-1.5 py-0.5 text-center font-mono text-[11px] text-foreground/80">
          {items.length}
        </span>
      </div>
      {items.length > 0 ? (
        <ul className="mt-1 space-y-0.5 pl-6">
          {items.map((item) => (
            <li key={item} className="truncate text-xs text-primary">
              {item}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function ObjectPropertyCard({
  objectView,
}: {
  objectView: FoundryLiteOntologyObjectView;
}) {
  return (
    <div className="rounded border bg-card">
      <div className="flex items-center gap-2 p-3 pb-2">
        <span className="text-xs font-semibold">속성</span>
        <span className="rounded bg-[#eff0f2] px-1.5 py-0.5 font-mono text-[11px] text-foreground/80">
          {objectView.propertyCount}
        </span>
      </div>
      <Separator />
      <ul className="max-h-56 overflow-auto p-1.5">
        {objectView.properties.map((property) => (
          <li
            key={property.apiName}
            className="flex items-center gap-2 rounded px-1.5 py-1 hover:bg-muted/60"
          >
            <Type className="size-3 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate text-xs">
              {property.displayName}
            </span>
            <span className="font-mono text-[11px] text-muted-foreground">
              {property.dataType}
            </span>
            {property.apiName === objectView.primaryKeyProperty ? (
              <StatusPill intent="info">
                <KeyRound className="size-3" />
                기본 키
              </StatusPill>
            ) : null}
            {property.classification ? (
              <StatusPill intent="warning">분류</StatusPill>
            ) : null}
          </li>
        ))}
      </ul>
      {objectView.actions.length > 0 ? (
        <>
          <Separator />
          <div className="p-3 pt-2">
            <div className="section-label mb-1.5">액션 타입</div>
            <ul className="space-y-1">
              {objectView.actions.map((action) => (
                <li
                  key={action.apiName}
                  className="flex items-center gap-2 text-xs"
                >
                  <ResourceKindIcon
                    kind="actionType"
                    className="size-4 rounded-[3px]"
                  />
                  <span className="min-w-0 flex-1 truncate">
                    {action.displayName}
                  </span>
                  {action.isEnabled ? null : (
                    <StatusPill intent="neutral">비활성</StatusPill>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </>
      ) : null}
    </div>
  );
}
