import type { Dataset, MediaSet } from "@foundry-lite/sdk";
import { FileStack, Plus, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import type { CatalogSelection } from "./catalog-selection";

interface DatasetCatalogProps {
  datasets: readonly Dataset[];
  isLoading: boolean;
  mediaSets: readonly MediaSet[];
  selection: CatalogSelection | null;
  onSelectDataset: (dataset: Dataset) => void;
  onSelectMediaSet: (mediaSetId: string) => void;
  onOpenMediaSetForm: () => void;
  className?: string;
}

/** 좌측 카탈로그: 검색 인풋 + 데이터셋/미디어 세트 섹션 (Palantir 컬럼 패널 스타일). */
export function DatasetCatalog({
  datasets,
  isLoading,
  mediaSets,
  selection,
  onSelectDataset,
  onSelectMediaSet,
  onOpenMediaSetForm,
  className,
}: DatasetCatalogProps) {
  const [filterText, setFilterText] = useState("");

  const filteredDatasets = useMemo(() => {
    const query = filterText.trim().toLowerCase();
    if (!query) return datasets;
    return datasets.filter((dataset) =>
      `${dataset.namespace}.${dataset.name}`.toLowerCase().includes(query),
    );
  }, [datasets, filterText]);

  const namespaces = useMemo(
    () => [...new Set(filteredDatasets.map((dataset) => dataset.namespace))],
    [filteredDatasets],
  );

  return (
    <aside className={cn("flex min-h-0 flex-col bg-card", className)}>
      <div className="border-b p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={filterText}
            onChange={(event) => setFilterText(event.target.value)}
            placeholder="이름으로 필터"
            className="h-7 pl-7 text-xs"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="flex items-center justify-between px-3 pt-3 pb-1">
          <span className="section-label">데이터셋</span>
          <span className="font-mono text-[11px] text-muted-foreground">
            {filteredDatasets.length}
          </span>
        </div>
        {isLoading && datasets.length === 0 ? (
          <div className="space-y-1.5 px-3 py-1">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
          </div>
        ) : filteredDatasets.length === 0 ? (
          <p className="px-3 py-2 text-xs text-muted-foreground">
            조건에 맞는 데이터셋이 없습니다.
          </p>
        ) : (
          namespaces.map((namespace) => (
            <NamespaceGroup
              key={namespace}
              namespace={namespace}
              datasets={filteredDatasets.filter(
                (dataset) => dataset.namespace === namespace,
              )}
              selection={selection}
              onSelectDataset={onSelectDataset}
            />
          ))
        )}

        <div className="mt-2 flex items-center justify-between border-t px-3 pt-3 pb-1">
          <span className="section-label">미디어 세트</span>
          <button
            type="button"
            onClick={onOpenMediaSetForm}
            className={cn(
              "flex items-center gap-0.5 rounded px-1 py-0.5 text-[11px] font-medium text-primary hover:bg-accent",
              selection?.kind === "media-new" && "bg-accent",
            )}
          >
            <Plus className="size-3" />
            추가
          </button>
        </div>
        {mediaSets.length === 0 ? (
          <p className="px-3 py-2 text-xs text-muted-foreground">
            이 세션에서 생성하거나 ID로 불러온 세트만 표시됩니다.
          </p>
        ) : (
          mediaSets.map((mediaSet) => (
            <CatalogRow
              key={mediaSet.media_set_id}
              icon={<FileStack className="size-3.5 shrink-0" />}
              label={mediaSet.name}
              sublabel={mediaSet.namespace}
              isSelected={
                selection?.kind === "media" &&
                selection.mediaSetId === mediaSet.media_set_id
              }
              onClick={() => onSelectMediaSet(mediaSet.media_set_id)}
            />
          ))
        )}
      </div>
    </aside>
  );
}

interface NamespaceGroupProps {
  namespace: string;
  datasets: readonly Dataset[];
  selection: CatalogSelection | null;
  onSelectDataset: (dataset: Dataset) => void;
}

function NamespaceGroup({
  namespace,
  datasets,
  selection,
  onSelectDataset,
}: NamespaceGroupProps) {
  return (
    <div className="pb-1">
      <div className="section-label px-3 py-1 text-muted-foreground/80">
        {namespace}
      </div>
      {datasets.map((dataset) => (
        <CatalogRow
          key={dataset.id}
          icon={<Table2 className="size-3.5 shrink-0 text-primary" />}
          label={dataset.name}
          sublabel={dataset.status}
          isSelected={
            selection?.kind === "dataset" &&
            selection.namespace === dataset.namespace &&
            selection.name === dataset.name
          }
          onClick={() => onSelectDataset(dataset)}
        />
      ))}
    </div>
  );
}

interface CatalogRowProps {
  icon: React.ReactNode;
  label: string;
  sublabel?: string;
  isSelected: boolean;
  onClick: () => void;
}

function CatalogRow({
  icon,
  label,
  sublabel,
  isSelected,
  onClick,
}: CatalogRowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-7 w-full items-center gap-2 px-3 text-left text-[12px] hover:bg-muted/60",
        isSelected && "bg-accent hover:bg-accent",
      )}
    >
      {icon}
      <span
        className={cn(
          "min-w-0 flex-1 truncate",
          isSelected ? "font-medium text-accent-foreground" : "font-normal",
        )}
      >
        {label}
      </span>
      {sublabel ? (
        <span className="shrink-0 text-[10px] text-muted-foreground/80">
          {sublabel}
        </span>
      ) : null}
    </button>
  );
}
