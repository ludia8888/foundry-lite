import type { ObjectAggregateRequest } from "@foundry-lite/sdk";
import {
  useFoundryLiteProvidedObjectAggregate,
  type FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { ChevronRight, CircleHelp } from "lucide-react";
import { useMemo } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { cn } from "@/lib/utils";

import {
  formatPropertyValue,
  groupableProperties,
  objectTypeIconClass,
} from "../lib/explorer-model";
import { DistributionRows } from "./DistributionRows";

interface LinkDistributionCardProps {
  targetView: FoundryLiteOntologyObjectView;
  linkApiName: string;
  dataVersion: number;
}

/** 링크 순회 분포 카드: [링크 타입 아이콘 + "Order › 속성"] 헤더 + 링크 대상 타입 분포. */
export function LinkDistributionCard({
  targetView,
  linkApiName,
  dataVersion,
}: LinkDistributionCardProps) {
  const groupBy = groupableProperties(targetView)[0] ?? null;
  const request = useMemo<ObjectAggregateRequest>(
    () => ({
      select: [{ function: "count" }],
      groupBy: groupBy ? [groupBy.apiName] : [],
      filter: null,
    }),
    [groupBy],
  );
  const aggregate = useFoundryLiteProvidedObjectAggregate(
    targetView.apiName,
    request,
    {
      key: [
        "objects",
        "aggregate",
        "link",
        targetView.apiName,
        groupBy?.apiName ?? null,
        dataVersion,
      ],
      enabled: groupBy !== null,
    },
  );

  const rows = useMemo(
    () =>
      [...aggregate.groups]
        .map((group) => ({
          label: formatPropertyValue(
            groupBy ? group.key[groupBy.apiName] : null,
          ),
          value: group.metrics.count ?? 0,
          display: (group.metrics.count ?? 0).toLocaleString("ko-KR"),
        }))
        .sort((left, right) => right.value - left.value),
    [aggregate.groups, groupBy],
  );

  if (!groupBy) return null;

  return (
    <div className="rounded border border-[#dde3e9] bg-white shadow-[0_1px_2px_rgba(17,20,24,0.04)]">
      <div className="flex h-12 items-center gap-2 border-b border-[#e4e9ed] px-4">
        <span
          className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded text-[12px] font-bold text-white",
            objectTypeIconClass(targetView.apiName),
          )}
        >
          {targetView.apiName.slice(0, 1).toUpperCase()}
        </span>
        <span className="text-[14px] font-bold text-[#1c2127]">
          {targetView.displayName}
        </span>
        <ChevronRight className="size-3.5 text-[#5f6b7c]" />
        <span className="truncate text-[14px] font-bold text-[#1c2127]">
          {groupBy.displayName}
        </span>
        <CircleHelp
          className="size-4 shrink-0 text-[#8b98a6]"
          aria-label={`${linkApiName} 링크로 연결된 ${targetView.displayName} 분포`}
        />
      </div>
      {aggregate.isLoading ? (
        <LoadingState rowCount={4} className="p-4" />
      ) : aggregate.error ? (
        <ErrorState
          error={aggregate.error}
          onRetry={aggregate.reload}
          className="m-4"
        />
      ) : rows.length === 0 ? (
        <EmptyState
          title="링크된 객체가 없습니다"
          description="링크 대상 타입에 집계할 객체가 없습니다."
          className="p-6"
        />
      ) : (
        <DistributionRows rows={rows} />
      )}
      <div className="border-t border-[#eef1f4] px-4 py-1.5 font-mono text-[10px] text-muted-foreground">
        {linkApiName} 링크 대상 {targetView.apiName} · objects.generic.aggregate
      </div>
    </div>
  );
}
