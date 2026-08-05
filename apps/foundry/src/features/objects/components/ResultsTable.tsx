import type { FoundryLiteApiError, GenericObject } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  formatPropertyValue,
  isNumericDataType,
  objectTitle,
  objectTypeIconClass,
  statusIntentFor,
  type ObjectRef,
} from "../lib/explorer-model";
import { InlineActionCell, inlineActionBinding } from "./InlineActionCell";

const MAX_PROPERTY_COLUMNS = 8;

interface ResultsTableProps {
  objectView: FoundryLiteOntologyObjectView;
  objects: GenericObject[];
  isLoading: boolean;
  error: FoundryLiteApiError | null;
  onRetry: () => void;
  selectedObjectId: string | null;
  nextCursor: string | null;
  hasPreviousPage: boolean;
  onNextPage: () => void;
  onFirstPage: () => void;
  onOpenObject: (ref: ObjectRef) => void;
  onObjectsChanged: () => void;
}

function buildColumns(
  objectView: FoundryLiteOntologyObjectView,
  onObjectsChanged: () => void,
): DataTableColumn<GenericObject>[] {
  const titleColumn: DataTableColumn<GenericObject> = {
    key: "__title",
    header: "제목",
    render: (object) => (
      <span className="flex items-center gap-1.5">
        <span
          className={cn(
            "flex size-4 items-center justify-center rounded-sm text-[8px] font-bold text-white",
            objectTypeIconClass(object.objectType),
          )}
        >
          {object.objectType.slice(0, 1).toUpperCase()}
        </span>
        <span className="font-medium text-primary">
          {objectTitle(object, objectView)}
        </span>
      </span>
    ),
  };
  const propertyColumns = objectView.properties
    .slice(0, MAX_PROPERTY_COLUMNS)
    .map<DataTableColumn<GenericObject>>((property) => ({
      key: property.apiName,
      header: property.displayName,
      isMono: property.isPrimaryKey || isNumericDataType(property.dataType),
      className: isNumericDataType(property.dataType)
        ? "text-right"
        : undefined,
      render: (object) => {
        const value = object.properties[property.apiName];
        const rendered = property.apiName === "status" && typeof value === "string" ? (
            <StatusPill intent={statusIntentFor(value)}>{value}</StatusPill>
          ) : (
            formatPropertyValue(value)
          );
        const inlineAction = objectView.actions
          .map((actionView) => ({ actionView, binding: inlineActionBinding(actionView) }))
          .filter(({ actionView, binding }) =>
            Boolean(actionView.isEnabled && binding?.propertyApiName === property.apiName),
          )
          .sort((left, right) => left.actionView.apiName.localeCompare(right.actionView.apiName))[0];
        if (property.isEditable && inlineAction?.binding) {
          return (
            <InlineActionCell
              actionView={inlineAction.actionView}
              binding={inlineAction.binding}
              object={object}
              property={property}
              onApplied={onObjectsChanged}
            >
              {rendered}
            </InlineActionCell>
          );
        }
        return rendered;
      },
    }));
  const versionColumn: DataTableColumn<GenericObject> = {
    key: "__version",
    header: "버전",
    isMono: true,
    className: "text-right",
    render: (object) => `v${object.objectVersion}`,
  };
  return [titleColumn, ...propertyColumns, versionColumn];
}

/** Results 탭: 전체 폭 객체 테이블 + 커서 페이지네이션 증거. */
export function ResultsTable({
  objectView,
  objects,
  isLoading,
  error,
  onRetry,
  selectedObjectId,
  nextCursor,
  hasPreviousPage,
  onNextPage,
  onFirstPage,
  onOpenObject,
  onObjectsChanged,
}: ResultsTableProps) {
  if (isLoading) return <LoadingState rowCount={8} />;
  if (error) return <ErrorState error={error} onRetry={onRetry} />;

  return (
    <div className="space-y-2">
      <DataTable
        columns={buildColumns(objectView, onObjectsChanged)}
        rows={objects}
        rowKey={(object) => object.objectId}
        selectedKey={selectedObjectId}
        onRowClick={(object) =>
          onOpenObject({
            objectType: object.objectType,
            objectId: object.objectId,
          })
        }
        emptyMessage="조건에 맞는 객체가 없습니다. 필터를 조정해 보세요."
      />
      <div className="flex flex-wrap items-center gap-2 rounded border bg-card px-3 py-1.5">
        <span className="font-mono text-[11px] text-muted-foreground">
          현재 페이지 {objects.length}행
        </span>
        {nextCursor ? (
          <span className="max-w-64 truncate font-mono text-[10px] text-muted-foreground">
            next_cursor={nextCursor}
          </span>
        ) : (
          <span className="font-mono text-[10px] text-muted-foreground">
            마지막 페이지
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {hasPreviousPage ? (
            <Button size="sm" variant="outline" onClick={onFirstPage}>
              첫 페이지로
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="outline"
            disabled={!nextCursor}
            onClick={onNextPage}
          >
            다음 페이지
          </Button>
        </div>
      </div>
    </div>
  );
}
