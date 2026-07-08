import type { OntologyCatalog } from "@foundry-lite/sdk";
import { ontologyDraftFromCatalog } from "@foundry-lite/sdk/ontology-draft";
import {
  useFoundryLiteClient,
  useFoundryLiteProvidedDatasetColumnMapping,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { ChevronDown, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { DataTableColumn } from "@/components/shared/DataTable";
import { DataTable } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

type ColumnMatchRow = {
  columnName: string;
  dataType: string | null;
  status: "mapped" | "suggested" | "unmapped";
  propertyApiName: string | null;
  suggestedPropertyApiName: string;
  suggestedDataType: string;
};

const MATCH_STATUS_LABELS: Record<ColumnMatchRow["status"], string> = {
  mapped: "매핑됨",
  suggested: "후보",
  unmapped: "미매핑",
};

interface ColumnMappingPanelProps {
  catalog: OntologyCatalog | null;
}

/**
 * 데이터셋 컬럼 → 속성 후보 패널 (acceptance: dataset column을 property
 * 후보로 표시). datasets.inspect 스키마와 드래프트 객체 타입을 매칭한다.
 */
export function ColumnMappingPanel({ catalog }: ColumnMappingPanelProps) {
  const client = useFoundryLiteClient();
  const [isOpen, setIsOpen] = useState(false);
  const [datasetKey, setDatasetKey] = useState<string | null>(null);
  const [objectApiName, setObjectApiName] = useState<string | null>(null);

  const datasetsQuery = useFoundryLiteQuery(
    ["ontology", "datasets", "list"],
    () => client.datasets.list(),
    { enabled: isOpen },
  );
  const datasets = datasetsQuery.data ?? [];

  const selectedDataset = useMemo(() => {
    if (!datasetKey) return null;
    const [namespace, name] = datasetKey.split("/");
    return namespace && name ? { namespace, name } : null;
  }, [datasetKey]);

  const mapping = useFoundryLiteProvidedDatasetColumnMapping(selectedDataset, {
    enabled: isOpen && selectedDataset !== null,
  });

  const draftObject = useMemo(() => {
    if (!catalog) return null;
    const draft = ontologyDraftFromCatalog(catalog);
    const targetApiName = objectApiName ?? draft.objectTypes[0]?.apiName;
    return (
      draft.objectTypes.find((item) => item.apiName === targetApiName) ?? null
    );
  }, [catalog, objectApiName]);

  const rows = useMemo<ColumnMatchRow[]>(
    () =>
      mapping.matchColumns(draftObject).map((match) => ({
        columnName: match.columnName,
        dataType: match.column.dataType,
        status: match.status,
        propertyApiName: match.propertyApiName,
        suggestedPropertyApiName: match.suggestedPropertyApiName,
        suggestedDataType: match.suggestedDataType,
      })),
    [mapping, draftObject],
  );

  const columns: DataTableColumn<ColumnMatchRow>[] = [
    {
      key: "column",
      header: "데이터셋 컬럼",
      isMono: true,
      render: (row) => row.columnName,
    },
    {
      key: "type",
      header: "컬럼 타입",
      isMono: true,
      className: "w-24",
      render: (row) => row.dataType ?? "—",
    },
    {
      key: "status",
      header: "매핑 상태",
      className: "w-24",
      render: (row) => (
        <StatusPill
          intent={
            row.status === "mapped"
              ? "success"
              : row.status === "suggested"
                ? "info"
                : "neutral"
          }
        >
          {MATCH_STATUS_LABELS[row.status]}
        </StatusPill>
      ),
    },
    {
      key: "property",
      header: "속성 후보",
      isMono: true,
      render: (row) =>
        `${row.propertyApiName ?? row.suggestedPropertyApiName}: ${row.suggestedDataType}`,
    },
  ];

  return (
    <Collapsible
      open={isOpen}
      onOpenChange={setIsOpen}
      className="rounded border bg-card"
    >
      <CollapsibleTrigger className="flex h-9 w-full items-center gap-2 px-3 text-left">
        <ChevronDown
          className={cn(
            "size-3.5 text-muted-foreground transition-transform",
            !isOpen && "-rotate-90",
          )}
        />
        <Table2 className="size-3.5 text-primary" />
        <span className="text-xs font-semibold">데이터셋 컬럼 → 속성 후보</span>
        {mapping.hasColumns ? (
          <span className="font-mono text-[11px] text-muted-foreground">
            컬럼 {mapping.columns.length}개
          </span>
        ) : null}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <Separator />
        <div className="space-y-3 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={datasetKey ?? undefined}
              onValueChange={setDatasetKey}
            >
              <SelectTrigger size="sm" className="w-56 text-xs">
                <SelectValue placeholder="데이터셋 선택" />
              </SelectTrigger>
              <SelectContent>
                {datasets.map((dataset) => (
                  <SelectItem
                    key={dataset.id}
                    value={`${dataset.namespace}/${dataset.name}`}
                    className="font-mono text-xs"
                  >
                    {dataset.namespace}.{dataset.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={objectApiName ?? draftObject?.apiName ?? undefined}
              onValueChange={setObjectApiName}
            >
              <SelectTrigger size="sm" className="w-48 text-xs">
                <SelectValue placeholder="대상 객체 타입" />
              </SelectTrigger>
              <SelectContent>
                {(catalog?.objectTypes ?? []).map((objectType) => (
                  <SelectItem
                    key={objectType.apiName}
                    value={objectType.apiName}
                    className="text-xs"
                  >
                    {objectType.displayName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {datasetsQuery.error ? (
            <ErrorState
              error={datasetsQuery.error}
              onRetry={datasetsQuery.reload}
            />
          ) : null}
          {!selectedDataset ? (
            <EmptyState
              title="데이터셋을 선택하세요"
              description="선택한 데이터셋의 스키마 컬럼을 객체 타입 속성 후보로 매칭해 보여줍니다."
            />
          ) : mapping.isLoading || datasetsQuery.isLoading ? (
            <LoadingState rowCount={4} />
          ) : mapping.error ? (
            <ErrorState error={mapping.error} onRetry={mapping.reload} />
          ) : (
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.columnName}
              emptyMessage="스키마에서 컬럼을 찾지 못했습니다."
            />
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
