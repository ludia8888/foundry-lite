import type { GenericObject } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import {
  formatCellValue,
  objectTitleOf,
  statusIntentOf,
} from "../lib/app-model";
import { RuntimeActionForm } from "./RuntimeActionForm";

type DetailTab = "overview" | "properties";

/** 상세 패널 헤더 아이콘 컬러 (레퍼런스 실측: 오렌지 계열 객체 배지). */
const ICON_COLOR = "bg-[#c87619]";

interface RuntimeDetailPanelProps {
  object: GenericObject;
  objectView: FoundryLiteOntologyObjectView | null;
  onClose: () => void;
  onApplied: () => void;
}

/**
 * 런타임 우측 상세 패널: 헤더(아이콘+타이틀+탭) + 그린 액션 버튼 + 속성 key-value + 액션 폼.
 */
export function RuntimeDetailPanel({
  object,
  objectView,
  onClose,
  onApplied,
}: RuntimeDetailPanelProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");
  const [activeActionApiName, setActiveActionApiName] = useState<string | null>(
    null,
  );
  const actions = objectView?.actions ?? [];
  const activeAction =
    actions.find((action) => action.apiName === activeActionApiName) ?? null;
  const propertyNames = Object.keys(object.properties);
  const title = objectTitleOf(object, objectView);

  // 선택 객체가 바뀌면 액션 폼을 닫고 탭을 초기화한다.
  useEffect(() => {
    setActiveActionApiName(null);
    setActiveTab("overview");
  }, [object.objectId]);

  const tabs: Array<{ id: DetailTab; label: string }> = [
    { id: "overview", label: "개요" },
    { id: "properties", label: "속성" },
  ];

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-[#d5dce1] px-3">
        <span
          className={cn(
            "flex size-7 items-center justify-center rounded text-[13px] font-bold text-white",
            ICON_COLOR,
          )}
        >
          {object.objectType.slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] leading-tight font-bold text-[#1c2127]">
            {title}
          </div>
          <div className="truncate font-mono text-[10px] leading-tight text-muted-foreground">
            {object.objectType} › {object.objectId} · v{object.objectVersion}
          </div>
        </div>
        <button
          type="button"
          aria-label="상세 닫기"
          className="rounded p-1 text-muted-foreground hover:bg-muted/60 hover:text-foreground"
          onClick={onClose}
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="flex h-9 shrink-0 items-stretch gap-4 border-b border-[#e4e9ed] px-4">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "relative flex items-center text-[13px]",
              activeTab === tab.id
                ? "font-semibold text-[#215db0]"
                : "font-medium text-[#1c2127] hover:text-[#215db0]",
            )}
          >
            {tab.label}
            {activeTab === tab.id ? (
              <span className="absolute inset-x-0 -bottom-[9px] h-[3px] rounded-t bg-[#2d72d2]" />
            ) : null}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {activeAction ? (
          <div className="space-y-3">
            <div className="section-label">
              액션 · {activeAction.displayName}
            </div>
            <RuntimeActionForm
              actionView={activeAction}
              targetObject={object}
              onApplied={onApplied}
              onCancel={() => setActiveActionApiName(null)}
            />
          </div>
        ) : (
          <div className="space-y-4">
            {actions.length > 0 ? (
              <div className="flex flex-wrap items-center gap-2">
                {actions.map((action) => (
                  <button
                    key={action.apiName}
                    type="button"
                    disabled={!action.isEnabled}
                    className="h-8 rounded bg-[#2b9f6c] px-3.5 text-[13px] font-semibold text-white shadow-sm hover:bg-[#24895c] disabled:cursor-not-allowed disabled:opacity-50"
                    onClick={() => setActiveActionApiName(action.apiName)}
                  >
                    {action.displayName}
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground">
                이 객체 타입에 연결된 액션이 없습니다.
              </p>
            )}

            <div className="rounded border border-[#dde3e9]">
              <div className="section-label border-b border-[#e4e9ed] px-3 py-2">
                속성
              </div>
              <div className="divide-y divide-[#eef1f4]">
                {(activeTab === "overview"
                  ? propertyNames.slice(0, 8)
                  : propertyNames
                ).map((name) => {
                  const value = object.properties[name];
                  return (
                    <div
                      key={name}
                      className="flex items-center gap-3 px-3 py-1.5 text-[12px]"
                    >
                      <span className="w-32 shrink-0 truncate text-[#5f6b7c]">
                        {name}
                      </span>
                      {name === "status" && typeof value === "string" ? (
                        <StatusPill intent={statusIntentOf(value)}>
                          {value}
                        </StatusPill>
                      ) : (
                        <span
                          className={cn(
                            "truncate text-[#1c2127]",
                            typeof value === "number" && "tabular-nums",
                          )}
                        >
                          {formatCellValue(value)}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
              {activeTab === "overview" && propertyNames.length > 8 ? (
                <button
                  type="button"
                  className="w-full border-t border-[#e4e9ed] py-2 text-center text-[12px] font-medium text-[#215db0] hover:bg-[#f6f8fa]"
                  onClick={() => setActiveTab("properties")}
                >
                  모두 보기...
                </button>
              ) : null}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
