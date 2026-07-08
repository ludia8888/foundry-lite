import type {
  OntologyDraftObjectType,
  OntologyDraftProperty,
} from "@foundry-lite/sdk/ontology-draft";
import { ontologyDraftPropertyForColumn } from "@foundry-lite/sdk/ontology-draft";
import { useFoundryLiteProvidedDatasetColumnMapping } from "@foundry-lite/sdk/react";
import { ChevronDown, Database, Trash2 } from "lucide-react";
import { useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

import type { ObjectTypeDatasource } from "../lib/object-type-draft";
import {
  ALLOWED_PROPERTY_TYPES,
  coercePropertyType,
} from "../lib/object-type-draft";

const STATUS_LABELS = {
  mapped: "매핑됨",
  suggested: "후보",
  unmapped: "미매핑",
} as const;

const STATUS_CLASSES = {
  mapped: "bg-[#e6f4ec] text-[#2f7d53]",
  suggested: "bg-[#ecf0fa] text-[#325caa]",
  unmapped: "bg-[#eff0f2] text-[#8a9099]",
} as const;

interface DatasourceMappingTableProps {
  datasource: ObjectTypeDatasource;
  draftObject: OntologyDraftObjectType;
  isEditable: boolean;
  onUpsertProperty: (property: OntologyDraftProperty) => void;
  onUpdateProperty: (
    apiName: string,
    patch: Partial<Omit<OntologyDraftProperty, "apiName">>,
  ) => void;
  onRenameProperty: (
    oldApiName: string,
    property: OntologyDraftProperty,
  ) => void;
  onRemoveProperty: (apiName: string) => void;
  onSetPrimaryKey: (propertyApiName: string) => void;
  onRemoveDatasource: (() => void) | null;
}

/**
 * 하나의 데이터소스 카드: 컬럼 → 속성 편집형 매핑 표. 각 행은
 * matchColumnsToProperties() status로 초기화하고, include 체크 시
 * ontologyDraftPropertyForColumn으로 속성을 생성한다.
 */
export function DatasourceMappingTable({
  datasource,
  draftObject,
  isEditable,
  onUpsertProperty,
  onUpdateProperty,
  onRenameProperty,
  onRemoveProperty,
  onSetPrimaryKey,
  onRemoveDatasource,
}: DatasourceMappingTableProps) {
  const [isOpen, setIsOpen] = useState(true);
  const datasetRef = parseDatasetRef(datasource.dataset);
  const mapping = useFoundryLiteProvidedDatasetColumnMapping(datasetRef, {
    enabled: datasetRef !== null,
  });
  const matches = mapping.matchColumns(draftObject);

  const handleToggleInclude = (columnName: string, isChecked: boolean) => {
    const match = matches.find((item) => item.columnName === columnName);
    if (!match) return;
    if (isChecked) {
      const property = ontologyDraftPropertyForColumn(match.column);
      onUpsertProperty({
        ...property,
        type: coercePropertyType(property.type),
      });
    } else if (match.property) {
      onRemoveProperty(match.property.apiName);
    }
  };

  return (
    <Collapsible
      open={isOpen}
      onOpenChange={setIsOpen}
      className="rounded border bg-card"
    >
      <div className="flex h-11 items-center gap-2 px-3">
        <CollapsibleTrigger className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <ChevronDown
            className={cn(
              "size-3.5 text-muted-foreground transition-transform",
              !isOpen && "-rotate-90",
            )}
          />
          <Database className="size-3.5 text-primary" />
          <span className="text-[13px] font-semibold">{datasource.name}</span>
          <span className="truncate font-mono text-[11px] text-muted-foreground">
            {datasource.dataset ?? "데이터셋 미지정"}
          </span>
          {datasource.primaryKeyColumns.map((column) => (
            <span
              key={column}
              className="rounded bg-[#e6e1f5] px-1.5 py-0.5 font-mono text-[10px] text-[#5b4a9e]"
            >
              {column}
            </span>
          ))}
          {datasource.requiredRole ? (
            <span className="rounded bg-[#f4e7d6] px-1.5 py-0.5 text-[10px] text-[#8b5923]">
              {datasource.requiredRole}
            </span>
          ) : null}
        </CollapsibleTrigger>
        {onRemoveDatasource && isEditable ? (
          <button
            type="button"
            onClick={onRemoveDatasource}
            className="text-muted-foreground hover:text-destructive"
            aria-label={`${datasource.name} 데이터소스 삭제`}
          >
            <Trash2 className="size-3.5" />
          </button>
        ) : null}
      </div>
      <CollapsibleContent>
        <Separator />
        {mapping.isLoading ? (
          <p className="p-3 text-xs text-muted-foreground">컬럼 로딩 중…</p>
        ) : matches.length === 0 ? (
          <p className="p-3 text-xs text-muted-foreground">
            스키마에서 컬럼을 찾지 못했습니다.
          </p>
        ) : (
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b bg-[#f6f7f9] text-[10px] tracking-wide text-muted-foreground uppercase">
                <th className="w-8 px-2 py-1.5" />
                <th className="px-2 py-1.5 text-left font-semibold">
                  데이터셋 컬럼
                </th>
                <th className="px-2 py-1.5 text-left font-semibold">
                  컬럼 타입
                </th>
                <th className="px-2 py-1.5 text-left font-semibold">
                  속성 apiName
                </th>
                <th className="px-2 py-1.5 text-left font-semibold">
                  속성 타입
                </th>
                <th className="w-10 px-2 py-1.5 text-center font-semibold">
                  PK
                </th>
                <th className="w-14 px-2 py-1.5 text-center font-semibold">
                  Indexed
                </th>
                <th className="w-16 px-2 py-1.5 text-left font-semibold">
                  상태
                </th>
              </tr>
            </thead>
            <tbody>
              {matches.map((match) => {
                const property = match.property;
                const isIncluded = property !== null;
                const apiName =
                  property?.apiName ?? match.suggestedPropertyApiName;
                const dataType = property?.type ?? match.suggestedDataType;
                const isPrimaryKey = apiName === draftObject.primaryKey;
                return (
                  <tr
                    key={match.columnName}
                    className={cn(
                      "border-b last:border-b-0",
                      !isIncluded && "bg-muted/20",
                    )}
                  >
                    <td className="px-2 py-1.5 text-center">
                      <Checkbox
                        checked={isIncluded}
                        disabled={!isEditable}
                        onCheckedChange={(value) =>
                          handleToggleInclude(match.columnName, value === true)
                        }
                        aria-label={`${match.columnName} 포함`}
                      />
                    </td>
                    <td className="px-2 py-1.5 font-mono text-[11px]">
                      {match.columnName}
                    </td>
                    <td className="px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
                      {match.column.dataType ?? "—"}
                    </td>
                    <td className="px-2 py-1.5">
                      <Input
                        value={apiName}
                        disabled={!isEditable || !isIncluded}
                        onChange={(event) => {
                          if (!property) return;
                          onRenameProperty(property.apiName, {
                            ...property,
                            apiName: event.target.value,
                          });
                        }}
                        className="h-7 font-mono text-[11px]"
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <Select
                        value={coercePropertyType(dataType)}
                        disabled={!isEditable || !isIncluded}
                        onValueChange={(value) => {
                          if (!property) return;
                          onUpdateProperty(property.apiName, { type: value });
                        }}
                      >
                        <SelectTrigger
                          size="sm"
                          className="h-7 w-28 text-[11px]"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {ALLOWED_PROPERTY_TYPES.map((type) => (
                            <SelectItem
                              key={type}
                              value={type}
                              className="text-xs"
                            >
                              {type}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <input
                        type="radio"
                        name={`pk-${datasource.name}`}
                        checked={isPrimaryKey}
                        disabled={!isEditable || !isIncluded}
                        onChange={() => onSetPrimaryKey(apiName)}
                        aria-label={`${apiName} 기본 키`}
                        className="accent-primary"
                      />
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <Checkbox
                        checked={property?.indexed === true}
                        disabled={!isEditable || !isIncluded}
                        onCheckedChange={(value) => {
                          if (!property) return;
                          onUpdateProperty(property.apiName, {
                            indexed: value === true,
                          });
                        }}
                        aria-label={`${apiName} 색인`}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <span
                        className={cn(
                          "inline-flex items-center rounded px-1.5 py-0.5 text-[10px]",
                          STATUS_CLASSES[match.status],
                        )}
                      >
                        {STATUS_LABELS[match.status]}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}

/** 'ns.name' 데이터셋 참조를 { namespace, name }로 파싱한다. */
export function parseDatasetRef(
  dataset: string | null,
): { namespace: string; name: string } | null {
  if (!dataset) return null;
  const separatorIndex = dataset.indexOf(".");
  if (separatorIndex === -1) return null;
  const namespace = dataset.slice(0, separatorIndex);
  const name = dataset.slice(separatorIndex + 1);
  return namespace && name ? { namespace, name } : null;
}
