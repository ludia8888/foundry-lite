import type { GenericObject } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteGenericObjectQuery,
  type FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { LayoutGrid } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import {
  collectWidgets,
  formatCellValue,
  statusIntentOf,
  tableColumnNames,
  type AppPage,
} from "../lib/app-model";
import { RuntimeDetailPanel } from "./RuntimeDetailPanel";
import { RuntimeFilterRail } from "./RuntimeFilterRail";

interface RuntimeModeProps {
  page: AppPage;
  objectViewsByApiName: Record<string, FoundryLiteOntologyObjectView>;
}

/** facet 후보: status 우선 + 낮은 카디널리티 문자열 속성. */
function facetPropertiesFor(
  objectView: FoundryLiteOntologyObjectView | null,
  objects: readonly GenericObject[],
): string[] {
  const candidates = new Set<string>();
  if (objects.some((object) => object.properties.status !== undefined))
    candidates.add("status");
  const stringProps = (objectView?.properties ?? [])
    .filter(
      (property) => !property.isPrimaryKey && property.dataType === "string",
    )
    .map((property) => property.apiName);
  for (const name of stringProps) {
    const distinct = new Set(
      objects.map((object) => String(object.properties[name] ?? "")),
    );
    if (distinct.size > 1 && distinct.size <= 8) candidates.add(name);
  }
  return Array.from(candidates).slice(0, 3);
}

/**
 * 런타임 모드: 앱 정의의 객체 테이블 위젯이 지정한 객체 타입을 실제 쿼리하고
 * 좌 필터 · 중앙 테이블 · 우 상세(액션 폼)로 렌더링한다.
 */
export function RuntimeMode({ page, objectViewsByApiName }: RuntimeModeProps) {
  const widgets = collectWidgets(page);
  const tableWidget =
    widgets.find((widget) => widget.kind === "objectTable") ?? null;
  const objectApiName = tableWidget?.objectApiName ?? null;
  const objectView = objectApiName
    ? (objectViewsByApiName[objectApiName] ?? null)
    : null;

  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const [selectedFacets, setSelectedFacets] = useState<
    Record<string, Set<string>>
  >({});
  const [dataVersion, setDataVersion] = useState(0);

  const client = useFoundryLiteClient();
  const query = useFoundryLiteGenericObjectQuery(client, {
    objectApiName,
    pageSize: 100,
    key: ["workshop-runtime", objectApiName ?? "none", dataVersion],
  });

  const allObjects = query.objects;
  const facetProperties = useMemo(
    () => facetPropertiesFor(objectView, allObjects),
    [objectView, allObjects],
  );

  const filteredObjects = useMemo(() => {
    const activeFacets = Object.entries(selectedFacets).filter(
      ([, values]) => values.size > 0,
    );
    if (activeFacets.length === 0) return allObjects;
    return allObjects.filter((object) =>
      activeFacets.every(([property, values]) =>
        values.has(String(object.properties[property] ?? "")),
      ),
    );
  }, [allObjects, selectedFacets]);

  const columnNames = useMemo(
    () => tableColumnNames(objectView, filteredObjects).slice(0, 6),
    [objectView, filteredObjects],
  );

  const selectedObject =
    filteredObjects.find((object) => object.objectId === selectedObjectId) ??
    allObjects.find((object) => object.objectId === selectedObjectId) ??
    null;

  const handleToggleFacet = (property: string, value: string) => {
    setSelectedFacets((previous) => {
      const nextValues = new Set(previous[property] ?? []);
      if (nextValues.has(value)) nextValues.delete(value);
      else nextValues.add(value);
      return { ...previous, [property]: nextValues };
    });
  };

  const handleApplied = () => setDataVersion((version) => version + 1);

  if (!tableWidget || !objectApiName) {
    return (
      <div className="p-8">
        <EmptyState
          icon={LayoutGrid}
          title="객체 테이블 위젯이 없습니다"
          description="빌더 모드에서 객체 테이블 위젯을 배치하고 객체 타입을 지정하세요."
        />
      </div>
    );
  }

  if (query.isLoading && allObjects.length === 0) {
    return (
      <div className="p-4">
        <LoadingState rowCount={8} />
      </div>
    );
  }

  if (query.error && allObjects.length === 0) {
    return (
      <div className="p-4">
        <ErrorState error={query.error} onRetry={() => query.reload()} />
      </div>
    );
  }

  const hasDetailWidget = widgets.some(
    (widget) => widget.kind === "objectDetail",
  );

  return (
    <div className="grid h-full min-h-0 grid-cols-[248px_1fr_400px] divide-x divide-[#d5dce1]">
      <RuntimeFilterRail
        objects={allObjects}
        facetProperties={facetProperties}
        selectedFacets={selectedFacets}
        onToggleFacet={handleToggleFacet}
      />

      <div className="flex h-full min-h-0 flex-col bg-white">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-[#d5dce1] px-3">
          <span className="text-[13px] font-semibold text-[#1c2127]">
            {objectView?.displayName ?? objectApiName}
          </span>
          <StatusPill intent="neutral">
            {filteredObjects.length.toLocaleString("en-US")}행
          </StatusPill>
          <span className="ml-auto font-mono text-[11px] text-muted-foreground">
            objects.generic.query · {objectApiName}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full border-collapse text-[12px]">
            <thead className="sticky top-0 bg-white">
              <tr className="border-b border-[#e4e9ed]">
                <th className="section-label h-8 w-8 px-2 text-left" />
                {columnNames.map((name) => (
                  <th
                    key={name}
                    className="section-label h-8 px-3 text-left align-middle"
                  >
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredObjects.map((object) => {
                const isSelected = object.objectId === selectedObjectId;
                return (
                  <tr
                    key={object.objectId}
                    onClick={() => setSelectedObjectId(object.objectId)}
                    className={cn(
                      "cursor-pointer border-b border-[#eef1f4] last:border-b-0 hover:bg-[#f6f8fa]",
                      isSelected && "bg-[#e8f0fb] hover:bg-[#e8f0fb]",
                    )}
                  >
                    <td className="h-8 px-2 align-middle">
                      <span className="flex size-4 items-center justify-center rounded-[3px] bg-[#c87619] text-[10px] font-bold text-white">
                        {object.objectType.slice(0, 1).toUpperCase()}
                      </span>
                    </td>
                    {columnNames.map((name) => {
                      const value = object.properties[name];
                      return (
                        <td
                          key={name}
                          className={cn(
                            "h-8 px-3 align-middle whitespace-nowrap",
                            typeof value === "number" &&
                              "font-mono tabular-nums",
                          )}
                        >
                          {name === "status" && typeof value === "string" ? (
                            <StatusPill intent={statusIntentOf(value)}>
                              {value}
                            </StatusPill>
                          ) : (
                            <span className="truncate text-[#1c2127]">
                              {formatCellValue(value)}
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
              {filteredObjects.length === 0 ? (
                <tr>
                  <td
                    colSpan={columnNames.length + 1}
                    className="h-16 px-3 text-center text-xs text-muted-foreground"
                  >
                    필터 조건에 맞는 객체가 없습니다.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      <div className="h-full min-h-0">
        {!hasDetailWidget ? (
          <div className="flex h-full items-center justify-center p-8">
            <EmptyState
              title="객체 상세 위젯이 없습니다"
              description="빌더에서 객체 상세 위젯을 배치하면 선택 객체의 속성과 액션이 표시됩니다."
            />
          </div>
        ) : selectedObject ? (
          <RuntimeDetailPanel
            object={selectedObject}
            objectView={objectView}
            onClose={() => setSelectedObjectId(null)}
            onApplied={handleApplied}
          />
        ) : (
          <div className="flex h-full items-center justify-center bg-white p-8">
            <div className="text-center">
              <div className="mx-auto mb-2 flex size-10 items-center justify-center rounded-full bg-[#eef1f4] text-[#5f6b7c]">
                <LayoutGrid className="size-5" />
              </div>
              <p className="text-[13px] font-medium text-[#1c2127]">
                객체를 선택하세요
              </p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                왼쪽 테이블에서 행을 클릭하면 속성과 액션이 열립니다.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
