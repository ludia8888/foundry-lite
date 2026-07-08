import type { GenericObject } from "@foundry-lite/sdk";
import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { ArrowLeft, List, ListTree, RotateCw, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { operationsRunHref } from "@/lib/operations-links";
import { cn } from "@/lib/utils";

import { useObjectDetail } from "../hooks/use-object-detail";
import { useObjectLinks } from "../hooks/use-object-links";
import {
  formatPropertyValue,
  lineageEvidence,
  objectTitle,
  objectTypeIconClass,
  statusIntentFor,
  type ObjectRef,
} from "../lib/explorer-model";
import { ActionFormDialog } from "./ActionFormDialog";
import { LinksSection } from "./LinksSection";

type DetailTab = "overview" | "properties" | "links";

/** 인텐트 컬러 솔리드 액션 버튼 (레퍼런스 실측: 파랑/그린/오렌지/레드). */
const ACTION_BUTTON_COLORS = [
  "bg-[#2985bf] hover:bg-[#2274a8]",
  "bg-[#2b9f6c] hover:bg-[#24895c]",
  "bg-[#d98941] hover:bg-[#c67a35]",
  "bg-[#db4448] hover:bg-[#c93a3e]",
] as const;

interface ObjectDetailViewProps {
  objectRef: ObjectRef;
  objectView: FoundryLiteOntologyObjectView | null;
  canGoBack: boolean;
  onBack: () => void;
  onClose: () => void;
  onOpenLinked: (ref: ObjectRef) => void;
  onObjectsChanged: () => void;
}

function PropertiesGrid({
  object,
  propertyNames,
}: {
  object: GenericObject;
  propertyNames: string[];
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 md:divide-x md:divide-[#e4e9ed]">
      <div className="px-4 py-2">
        {propertyNames
          .filter((_, index) => index % 2 === 0)
          .map((name) => (
            <PropertyRow key={name} object={object} name={name} />
          ))}
      </div>
      <div className="px-4 py-2">
        {propertyNames
          .filter((_, index) => index % 2 === 1)
          .map((name) => (
            <PropertyRow key={name} object={object} name={name} />
          ))}
      </div>
    </div>
  );
}

function PropertyRow({
  object,
  name,
}: {
  object: GenericObject;
  name: string;
}) {
  const value = object.properties[name];
  return (
    <div className="flex items-center gap-4 py-2 text-[13px]">
      <span className="w-44 shrink-0 truncate text-[#5f6b7c]">{name}</span>
      {name === "status" && typeof value === "string" ? (
        <StatusPill intent={statusIntentFor(value)}>{value}</StatusPill>
      ) : (
        <span
          className={cn(
            "truncate text-[#1c2127]",
            typeof value === "number" && "tabular-nums",
          )}
        >
          {formatPropertyValue(value)}
        </span>
      )}
    </div>
  );
}

function ActionButtonsRow({
  actions,
  onRunAction,
}: {
  actions: FoundryLiteOntologyActionView[];
  onRunAction: (action: FoundryLiteOntologyActionView) => void;
}) {
  if (actions.length === 0) {
    return (
      <p className="text-[11px] text-muted-foreground">
        이 객체 타입에 연결된 액션이 없습니다.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2.5">
      {actions.map((action, index) => (
        <button
          key={action.apiName}
          type="button"
          className={cn(
            "h-8 rounded px-3.5 text-[13px] font-semibold text-white shadow-sm disabled:cursor-not-allowed disabled:opacity-50",
            ACTION_BUTTON_COLORS[index % ACTION_BUTTON_COLORS.length],
          )}
          disabled={!action.isEnabled}
          title={
            action.isEnabled ? undefined : "온톨로지에서 비활성화된 액션입니다."
          }
          onClick={() => onRunAction(action)}
        >
          {action.displayName}
        </button>
      ))}
    </div>
  );
}

function EvidenceCard({
  object,
  requestId,
}: {
  object: GenericObject;
  requestId: string | null;
}) {
  const lineage = lineageEvidence(object);
  return (
    <div className="rounded border border-[#dde3e9] bg-white shadow-[0_1px_2px_rgba(17,20,24,0.04)]">
      <div className="flex h-11 items-center gap-2 border-b border-[#e4e9ed] px-4">
        <ListTree className="size-4 text-[#5f6b7c]" />
        <span className="text-[14px] font-bold text-[#1c2127]">
          근거 (lineage)
        </span>
      </div>
      <div className="space-y-1 px-4 py-3 font-mono text-[11px] text-muted-foreground">
        <div>object_version=v{object.objectVersion}</div>
        {object.sourceDatasetVersionId ? (
          <div className="truncate">
            source_dataset_version={object.sourceDatasetVersionId}
          </div>
        ) : null}
        <div>lineage_edges={lineage.lineageCount}</div>
        {lineage.runIds.slice(0, 3).map((runId) => (
          <div key={runId} className="truncate">
            run_id=
            <Link
              to={operationsRunHref(runId)}
              title={runId}
              className="text-primary hover:underline"
            >
              {runId}
            </Link>
          </div>
        ))}
        {requestId ? <div>request_id={requestId}</div> : null}
      </div>
    </div>
  );
}

/** Object View: 헤더(아이콘+타이틀+브레드크럼+탭) + 인텐트 액션 행 + 속성 2컬럼 + 링크 섹션. */
export function ObjectDetailView({
  objectRef,
  objectView,
  canGoBack,
  onBack,
  onClose,
  onOpenLinked,
  onObjectsChanged,
}: ObjectDetailViewProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");
  const [pendingAction, setPendingAction] =
    useState<FoundryLiteOntologyActionView | null>(null);
  const detail = useObjectDetail(objectRef);
  const linksState = useObjectLinks(objectRef, objectView);
  const object = detail.object;
  const actions = objectView?.actions ?? [];
  const propertyNames = object ? Object.keys(object.properties) : [];

  const handleApplied = () => {
    void detail.reload();
    void linksState.reload();
    onObjectsChanged();
  };

  const tabs: Array<{ id: DetailTab; label: string; badge?: number }> = [
    { id: "overview", label: "개요" },
    { id: "properties", label: "속성" },
    { id: "links", label: "링크", badge: linksState.totalLinkedObjects },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-14 shrink-0 items-center gap-2 border-b border-[#d5dce1] bg-white px-3">
        <Button
          size="sm"
          variant="ghost"
          disabled={!canGoBack}
          onClick={onBack}
          aria-label="이전 객체로"
        >
          <ArrowLeft />
        </Button>
        <span
          className={cn(
            "flex size-9 items-center justify-center rounded text-[15px] font-bold text-white",
            objectTypeIconClass(objectRef.objectType),
          )}
        >
          {objectRef.objectType.slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0">
          <div className="truncate text-[14px] leading-tight font-bold text-[#1c2127]">
            {object ? objectTitle(object, objectView) : objectRef.objectId}
          </div>
          <div className="truncate font-mono text-[10px] leading-tight text-muted-foreground">
            {objectRef.objectType} › {objectRef.objectId}
            {object ? ` · v${object.objectVersion}` : null}
          </div>
        </div>
        <div className="ml-6 flex h-full items-stretch gap-5">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                "relative flex items-center gap-1.5 text-[13px]",
                activeTab === tab.id
                  ? "font-semibold text-[#215db0]"
                  : "font-medium text-[#1c2127] hover:text-[#215db0]",
              )}
            >
              {tab.label}
              {tab.badge !== undefined && tab.badge > 0 ? (
                <span className="rounded bg-[#edf0f2] px-1.5 text-[11px] font-semibold text-[#404854]">
                  {tab.badge}
                </span>
              ) : null}
              {activeTab === tab.id ? (
                <span className="absolute inset-x-0 bottom-0 h-[3px] rounded-t bg-[#2d72d2]" />
              ) : null}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void detail.reload()}
            aria-label="객체 새로고침"
          >
            <RotateCw />
          </Button>
          <Button size="sm" variant="outline" onClick={onClose}>
            <X />
            목록으로
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-[#f6f8fa] p-4">
        <div className="mx-auto max-w-[1240px] space-y-4">
          {detail.isLoading ? (
            <LoadingState rowCount={6} />
          ) : detail.error ? (
            <ErrorState
              error={detail.error}
              onRetry={() => void detail.reload()}
            />
          ) : object ? (
            <>
              {activeTab === "overview" ? (
                <>
                  <ActionButtonsRow
                    actions={actions}
                    onRunAction={setPendingAction}
                  />
                  <div className="rounded border border-[#dde3e9] bg-white shadow-[0_1px_2px_rgba(17,20,24,0.04)]">
                    <div className="flex h-11 items-center gap-2 border-b border-[#e4e9ed] px-4">
                      <List className="size-4 text-[#5f6b7c]" />
                      <span className="text-[14px] font-bold text-[#1c2127]">
                        속성
                      </span>
                    </div>
                    <PropertiesGrid
                      object={object}
                      propertyNames={propertyNames.slice(0, 6)}
                    />
                    <button
                      type="button"
                      className="w-full border-t border-[#e4e9ed] py-2.5 text-center text-[13px] font-medium text-[#215db0] hover:bg-[#f6f8fa]"
                      onClick={() => setActiveTab("properties")}
                    >
                      모두 보기...
                    </button>
                  </div>
                  <LinksSection
                    linksState={linksState}
                    onOpenLinked={onOpenLinked}
                  />
                </>
              ) : null}
              {activeTab === "properties" ? (
                <>
                  <div className="rounded border border-[#dde3e9] bg-white shadow-[0_1px_2px_rgba(17,20,24,0.04)]">
                    <div className="flex h-11 items-center gap-2 border-b border-[#e4e9ed] px-4">
                      <List className="size-4 text-[#5f6b7c]" />
                      <span className="text-[14px] font-bold text-[#1c2127]">
                        전체 속성 ({propertyNames.length})
                      </span>
                    </div>
                    <PropertiesGrid
                      object={object}
                      propertyNames={propertyNames}
                    />
                  </div>
                  <EvidenceCard object={object} requestId={detail.requestId} />
                </>
              ) : null}
              {activeTab === "links" ? (
                <LinksSection
                  linksState={linksState}
                  onOpenLinked={onOpenLinked}
                />
              ) : null}
            </>
          ) : null}
        </div>
      </div>
      <ActionFormDialog
        actionView={pendingAction}
        targetObject={object}
        isOpen={pendingAction !== null}
        onOpenChange={(isOpen) => {
          if (!isOpen) setPendingAction(null);
        }}
        onApplied={handleApplied}
      />
    </div>
  );
}
