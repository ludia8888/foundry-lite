import type { GenericObject } from "@foundry-lite/sdk";
import { Filter } from "lucide-react";
import { useMemo } from "react";

import { cn } from "@/lib/utils";

/** 필터 대상 속성 1개의 값별 카운트 버킷. */
type FacetBucket = {
  value: string;
  count: number;
};

type Facet = {
  property: string;
  label: string;
  buckets: FacetBucket[];
};

/** 막대 컬러 (레퍼런스 실측: 블루 계열). */
const BAR_COLOR = "#4a7fd6";

interface RuntimeFilterRailProps {
  objects: readonly GenericObject[];
  facetProperties: readonly string[];
  selectedFacets: Record<string, Set<string>>;
  onToggleFacet: (property: string, value: string) => void;
}

function buildFacet(
  objects: readonly GenericObject[],
  property: string,
): Facet {
  const countByValue = new Map<string, number>();
  for (const object of objects) {
    const raw = object.properties[property];
    if (raw === undefined || raw === null) continue;
    const value = String(raw);
    countByValue.set(value, (countByValue.get(value) ?? 0) + 1);
  }
  const buckets = Array.from(countByValue.entries())
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => b.count - a.count);
  return { property, label: property.toUpperCase(), buckets };
}

/**
 * 런타임 좌측 필터 레일: 속성별 facet 체크박스 + 카운트 + 비율 파란 막대.
 * 실 객체 데이터에서 카운트를 집계한다.
 */
export function RuntimeFilterRail({
  objects,
  facetProperties,
  selectedFacets,
  onToggleFacet,
}: RuntimeFilterRailProps) {
  const facets = useMemo(
    () => facetProperties.map((property) => buildFacet(objects, property)),
    [objects, facetProperties],
  );
  const maxCount = useMemo(
    () =>
      Math.max(
        1,
        ...facets.flatMap((facet) =>
          facet.buckets.map((bucket) => bucket.count),
        ),
      ),
    [facets],
  );

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex h-12 shrink-0 items-center gap-1.5 border-b border-[#d5dce1] px-3">
        <Filter className="size-4 text-[#5f6b7c]" />
        <span className="text-[13px] font-semibold text-[#1c2127]">필터</span>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
        {facets.map((facet) => (
          <div key={facet.property} className="space-y-1.5">
            <div className="section-label">{facet.label}</div>
            <div className="space-y-0.5">
              {facet.buckets.map((bucket) => {
                const isSelected =
                  selectedFacets[facet.property]?.has(bucket.value) ?? false;
                return (
                  <button
                    key={bucket.value}
                    type="button"
                    onClick={() => onToggleFacet(facet.property, bucket.value)}
                    className="group flex w-full items-center gap-2 rounded px-1 py-1 text-left hover:bg-muted/50"
                  >
                    <span
                      className={cn(
                        "flex size-3.5 shrink-0 items-center justify-center rounded-[3px] border",
                        isSelected
                          ? "border-[#2d72d2] bg-[#2d72d2]"
                          : "border-[#c1c8cf] bg-white",
                      )}
                    >
                      {isSelected ? (
                        <svg
                          viewBox="0 0 10 10"
                          className="size-2.5 fill-none stroke-white stroke-[1.8]"
                        >
                          <path
                            d="M1.5 5l2.2 2.2L8.5 2.5"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      ) : null}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate text-[12px] text-[#1c2127]">
                          {bucket.value}
                        </span>
                        <span className="shrink-0 font-mono text-[11px] text-muted-foreground tabular-nums">
                          {bucket.count.toLocaleString("en-US")}
                        </span>
                      </span>
                      <span className="mt-1 block h-1 w-full overflow-hidden rounded-full bg-[#eef1f4]">
                        <span
                          className="block h-full rounded-full"
                          style={{
                            width: `${Math.round((bucket.count / maxCount) * 100)}%`,
                            backgroundColor: BAR_COLOR,
                          }}
                        />
                      </span>
                    </span>
                  </button>
                );
              })}
              {facet.buckets.length === 0 ? (
                <div className="px-1 py-1 text-[11px] text-muted-foreground">
                  값 없음
                </div>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
