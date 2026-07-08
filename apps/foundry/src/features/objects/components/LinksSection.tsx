import type { ObjectLinkPayload } from "@foundry-lite/sdk";
import { ChevronRight, Link2 } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import type { ObjectLinksState } from "../hooks/use-object-links";
import {
  formatPropertyValue,
  objectTypeIconClass,
  type ObjectRef,
} from "../lib/explorer-model";

interface LinksSectionProps {
  linksState: ObjectLinksState;
  onOpenLinked: (ref: ObjectRef) => void;
}

function LinkedObjectRow({
  link,
  onOpenLinked,
}: {
  link: ObjectLinkPayload;
  onOpenLinked: (ref: ObjectRef) => void;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const propertyNames = Object.keys(link.to.properties);
  return (
    <div>
      <div className="flex items-center gap-3 py-1.5">
        <button
          type="button"
          aria-label={isExpanded ? "미리보기 접기" : "미리보기 펼치기"}
          className="flex size-6 items-center justify-center rounded text-[#5f6b7c] hover:bg-[#f0f3f5]"
          onClick={() => setIsExpanded((previous) => !previous)}
        >
          <ChevronRight
            className={cn(
              "size-4 transition-transform",
              isExpanded && "rotate-90",
            )}
          />
        </button>
        <span
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded text-[14px] font-bold text-white",
            objectTypeIconClass(link.to.objectType),
          )}
        >
          {link.to.objectType.slice(0, 1).toUpperCase()}
        </span>
        <button
          type="button"
          className="truncate text-[14px] font-medium text-[#215db0] hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground"
          disabled={link.to.targetMissing}
          onClick={() =>
            onOpenLinked({
              objectType: link.to.objectType,
              objectId: link.to.objectId,
            })
          }
        >
          {formatPropertyValue(link.to.properties.name ?? link.to.objectId)}
        </button>
        {link.to.targetMissing ? (
          <StatusPill intent="warning">대상 없음</StatusPill>
        ) : null}
        {link.warning ? (
          <StatusPill intent="warning">{link.warning.type}</StatusPill>
        ) : null}
      </div>
      {isExpanded ? (
        <div className="mb-2 ml-[72px] grid max-w-xl grid-cols-1 gap-x-8 rounded border border-[#eef1f4] bg-[#f6f8fa] px-3 py-2 md:grid-cols-2">
          {propertyNames.length === 0 ? (
            <span className="py-1 text-[12px] text-muted-foreground">
              미리볼 속성이 없습니다.
            </span>
          ) : (
            propertyNames.slice(0, 6).map((name) => (
              <div
                key={name}
                className="flex items-center justify-between gap-3 py-1 text-[12px]"
              >
                <span className="text-[#5f6b7c]">{name}</span>
                <span className="truncate text-[#1c2127]">
                  {formatPropertyValue(link.to.properties[name])}
                </span>
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

/** Links 섹션: 링크 그룹 헤더 + chevron 확장 미리보기 + 파란 링크 텍스트 행. */
export function LinksSection({ linksState, onOpenLinked }: LinksSectionProps) {
  return (
    <div className="rounded border border-[#dde3e9] bg-white shadow-[0_1px_2px_rgba(17,20,24,0.04)]">
      <div className="flex h-11 items-center gap-2 border-b border-[#e4e9ed] px-4">
        <Link2 className="size-4 text-[#5f6b7c]" />
        <span className="text-[14px] font-bold text-[#1c2127]">링크</span>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          연결 객체 {linksState.totalLinkedObjects}
        </span>
      </div>
      <div className="px-4 py-3">
        {linksState.isLoading ? (
          <LoadingState rowCount={3} />
        ) : linksState.error ? (
          <ErrorState error={linksState.error} onRetry={linksState.reload} />
        ) : linksState.groups.length === 0 ? (
          <EmptyState
            title="정의된 링크 타입이 없습니다"
            description="온톨로지에서 이 객체 타입의 링크를 추가하면 여기에 표시됩니다."
            className="p-4"
          />
        ) : (
          <div className="space-y-4">
            {linksState.groups.map((group) => (
              <div key={group.link.apiName}>
                <div className="mb-1 text-[13px] text-[#383e47]">
                  {group.link.apiName}:
                  <span className="ml-2 font-mono text-[10px] text-muted-foreground">
                    {group.link.cardinality}
                  </span>
                </div>
                {group.links.length === 0 ? (
                  <div className="pl-9 text-[12px] text-muted-foreground">
                    연결된 객체가 없습니다.
                  </div>
                ) : (
                  group.links.map((link) => (
                    <LinkedObjectRow
                      key={`${link.linkType}:${link.to.objectType}:${link.to.objectId}`}
                      link={link}
                      onOpenLinked={onOpenLinked}
                    />
                  ))
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
