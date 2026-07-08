import type { FoundryLiteApiError, GenericObject } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import {
  ArrowDownWideNarrow,
  ArrowRight,
  ArrowUpNarrowWide,
} from "lucide-react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import { linkTypesOf } from "../hooks/use-object-links";
import {
  formatPropertyValue,
  numericProperties,
  objectTitle,
  objectTypeIconClass,
  type ObjectRef,
} from "../lib/explorer-model";

export type SortSpec = {
  property: string;
  direction: "asc" | "desc";
};

interface ResultsRailProps {
  objectView: FoundryLiteOntologyObjectView;
  objects: GenericObject[];
  isLoading: boolean;
  error: FoundryLiteApiError | null;
  onRetry: () => void;
  selectedObjectId: string | null;
  sort: SortSpec | null;
  onSortChange: (sort: SortSpec | null) => void;
  onOpenObject: (ref: ObjectRef) => void;
  onShowAllResults: () => void;
  onOpenLinkedType: (typeName: string, linkApiName: string) => void;
}

const NO_SORT = "__none__";

function ObjectCard({
  object,
  objectView,
  subProperty,
  isSelected,
  onOpen,
}: {
  object: GenericObject;
  objectView: FoundryLiteOntologyObjectView;
  subProperty: string | null;
  isSelected: boolean;
  onOpen: () => void;
}) {
  const subValue = subProperty
    ? formatPropertyValue(object.properties[subProperty])
    : object.objectId;
  const title = objectTitle(object, objectView);
  return (
    <button
      type="button"
      aria-label={`객체 열기: ${object.objectType} ${title} ${object.objectId}`}
      onClick={onOpen}
      className={cn(
        "flex w-full items-center gap-3 border-b border-[#eef1f4] px-4 py-3 text-left hover:bg-[#f6f8fa]",
        isSelected && "bg-accent hover:bg-accent",
      )}
    >
      <span
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded text-[13px] font-bold text-white",
          objectTypeIconClass(object.objectType),
        )}
      >
        {object.objectType.slice(0, 1).toUpperCase()}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[14px] font-semibold text-[#1c2127]">
          {title}
        </span>
        <span className="block truncate text-[13px] text-[#5c7080]">
          {subValue}
        </span>
      </span>
    </button>
  );
}

/** 우측 Results 레일: 결과 카드(정렬 + 객체 리스트 + 모든 결과 보기) + 연결된 객체 카드. */
export function ResultsRail({
  objectView,
  objects,
  isLoading,
  error,
  onRetry,
  selectedObjectId,
  sort,
  onSortChange,
  onOpenObject,
  onShowAllResults,
  onOpenLinkedType,
}: ResultsRailProps) {
  const linkTypes = linkTypesOf(objectView);
  const subProperty =
    sort?.property ?? numericProperties(objectView)[0]?.apiName ?? null;

  return (
    <div className="w-[280px] shrink-0 space-y-4">
      <div className="rounded border border-[#dde3e9] bg-white shadow-[0_1px_2px_rgba(17,20,24,0.04)]">
        <div className="flex h-12 items-center gap-2 border-b border-[#e4e9ed] px-4">
          <span className="text-[15px] font-bold text-[#1c2127]">결과</span>
          <span className="rounded bg-[#edf0f2] px-2 py-0.5 text-[12px] font-semibold text-[#404854]">
            {objects.length}
          </span>
        </div>
        <div className="flex items-center gap-1 border-b border-[#e4e9ed] px-4 py-2">
          <span className="shrink-0 text-[13px] text-[#1c2127]">정렬 기준</span>
          <Select
            value={sort?.property ?? NO_SORT}
            onValueChange={(next) =>
              onSortChange(
                next === NO_SORT
                  ? null
                  : { property: next, direction: sort?.direction ?? "desc" },
              )
            }
          >
            <SelectTrigger
              size="sm"
              className="h-6 min-w-0 flex-1 gap-1 rounded border-none bg-transparent px-1 text-[13px] font-medium text-[#1c2127] underline decoration-[#8b98a6] underline-offset-3 shadow-none focus-visible:ring-0"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_SORT}>기본 순서</SelectItem>
              {objectView.properties.map((property) => (
                <SelectItem key={property.apiName} value={property.apiName}>
                  {property.displayName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="flex shrink-0">
            <button
              type="button"
              aria-label="오름차순 정렬"
              disabled={!sort}
              className={cn(
                "flex size-7 items-center justify-center rounded-l border border-[#c5cdd4]",
                sort?.direction === "asc"
                  ? "bg-[#e8ebed] text-[#1c2127]"
                  : "bg-white text-[#5f6b7c] hover:bg-[#f6f8fa]",
              )}
              onClick={() =>
                sort &&
                onSortChange({ property: sort.property, direction: "asc" })
              }
            >
              <ArrowUpNarrowWide className="size-3.5" />
            </button>
            <button
              type="button"
              aria-label="내림차순 정렬"
              disabled={!sort}
              className={cn(
                "-ml-px flex size-7 items-center justify-center rounded-r border border-[#c5cdd4]",
                sort?.direction === "desc"
                  ? "bg-[#e8ebed] text-[#1c2127]"
                  : "bg-white text-[#5f6b7c] hover:bg-[#f6f8fa]",
              )}
              onClick={() =>
                sort &&
                onSortChange({ property: sort.property, direction: "desc" })
              }
            >
              <ArrowDownWideNarrow className="size-3.5" />
            </button>
          </span>
        </div>
        <div>
          {isLoading ? (
            <LoadingState rowCount={6} className="p-3" />
          ) : error ? (
            <ErrorState error={error} onRetry={onRetry} className="m-3" />
          ) : objects.length === 0 ? (
            <EmptyState
              title="조건에 맞는 객체가 없습니다"
              description="필터 칩을 제거하거나 검색어를 바꿔 다시 조회해 보세요."
              className="m-3"
            />
          ) : (
            objects.map((object) => (
              <ObjectCard
                key={object.objectId}
                object={object}
                objectView={objectView}
                subProperty={subProperty}
                isSelected={object.objectId === selectedObjectId}
                onOpen={() =>
                  onOpenObject({
                    objectType: object.objectType,
                    objectId: object.objectId,
                  })
                }
              />
            ))
          )}
        </div>
        <button
          type="button"
          className="flex w-full items-center justify-center gap-2 px-4 py-3 text-[14px] font-medium text-[#215db0] hover:bg-[#f6f8fa]"
          onClick={onShowAllResults}
        >
          모든 결과 보기
          <ArrowRight className="size-4" />
        </button>
      </div>
      <div className="rounded border border-[#dde3e9] bg-white shadow-[0_1px_2px_rgba(17,20,24,0.04)]">
        <div className="flex h-12 items-center border-b border-[#e4e9ed] px-4 text-[15px] font-bold text-[#1c2127]">
          연결된 객체
        </div>
        {linkTypes.length === 0 ? (
          <div className="px-4 py-3 text-[12px] text-muted-foreground">
            이 객체 타입에 정의된 링크가 없습니다.
          </div>
        ) : (
          linkTypes.map((link) => {
            const targetType =
              link.fromObjectType === objectView.apiName
                ? link.toObjectType
                : link.fromObjectType;
            return (
              <button
                key={link.apiName}
                type="button"
                className="flex w-full items-center gap-3 border-b border-[#eef1f4] px-4 py-3 text-left last:border-b-0 hover:bg-[#f6f8fa]"
                onClick={() => onOpenLinkedType(targetType, link.apiName)}
              >
                <span
                  className={cn(
                    "flex size-8 shrink-0 items-center justify-center rounded text-[13px] font-bold text-white",
                    objectTypeIconClass(targetType),
                  )}
                >
                  {targetType.slice(0, 1).toUpperCase()}
                </span>
                <span className="flex-1 truncate text-[14px] text-[#1c2127]">
                  {targetType}
                </span>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {link.cardinality}
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
