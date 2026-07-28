import type { PipelineNodeDescriptorPayload } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import {
  BookOpenCheck,
  Boxes,
  CheckCircle2,
  Database,
  FileStack,
  GitMerge,
  Search,
  Sparkles,
  TableProperties,
  TriangleAlert,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

import {
  type CatalogCategory,
  descriptorCategory,
  descriptorKey,
  descriptorLabel,
  descriptorState,
  filterDescriptors,
} from "../pipeline-catalog-model";
import { useSafeQuery } from "../use-safe-query";
import { PipelineCapabilityDetails } from "./PipelineCapabilityDetails";

interface PipelineCapabilityCatalogProps {
  hasOutputNode: boolean;
  contextLabel: string;
  importedTrainedModelRefs: readonly string[];
  trainedModelUsageByRef: Readonly<Record<string, readonly string[]>>;
  onImportTrainedModel: (modelRef: string) => void;
  onRemoveTrainedModel: (modelRef: string) => void;
  onAddDescriptor: (descriptor: PipelineNodeDescriptorPayload) => void;
  onClose: () => void;
}

const CATEGORY_META: Record<
  CatalogCategory,
  { label: string; icon: LucideIcon }
> = {
  all: { label: "전체", icon: Boxes },
  source: { label: "Sources", icon: Database },
  table: { label: "Table", icon: TableProperties },
  media: { label: "Media", icon: FileStack },
  content: { label: "Content / AI", icon: Sparkles },
  bridge: { label: "Bridges", icon: GitMerge },
  output: { label: "Outputs", icon: BookOpenCheck },
};

/** 서버 descriptor/processor 계약을 직접 읽는 3열 capability catalog. */
export function PipelineCapabilityCatalog({
  hasOutputNode,
  contextLabel,
  importedTrainedModelRefs,
  trainedModelUsageByRef,
  onImportTrainedModel,
  onRemoveTrainedModel,
  onAddDescriptor,
  onClose,
}: PipelineCapabilityCatalogProps) {
  const client = useFoundryLiteClient();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<CatalogCategory>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const loadNodeTypes = useCallback(
    () => client.pipelines.nodeTypes(),
    [client],
  );
  const nodeTypes = useSafeQuery(
    ["pipelines", "node-type-catalog"],
    loadNodeTypes,
  );
  const loadProcessors = useCallback(
    () => client.media.processors.list(),
    [client],
  );
  const processors = useSafeQuery(
    ["pipelines", "media-processor-catalog"],
    loadProcessors,
  );
  const loadTrainedModels = useCallback(
    () => client.pipelines.trainedModels(),
    [client],
  );
  const trainedModels = useSafeQuery(
    ["pipelines", "trained-model-catalog"],
    loadTrainedModels,
  );
  const hasImportedTrainedModel = importedTrainedModelRefs.length > 0;

  const descriptors = useMemo(
    () => nodeTypes.data?.items ?? [],
    [nodeTypes.data],
  );
  const visible = useMemo(
    () => filterDescriptors(descriptors, category, query),
    [category, descriptors, query],
  );
  const selected =
    visible.find((descriptor) => descriptor.descriptorId === selectedId) ??
    visible[0] ??
    null;

  const addSelected = () => {
    if (!selected) return;
    onAddDescriptor(selected);
    onClose();
  };

  return (
    <section
      aria-label="Pipeline node catalog"
      className="flex min-h-0 flex-1 flex-col bg-[#F4F6F8]"
    >
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-[#C5CBD3] px-4">
        <Database className="size-4 text-[#738091]" aria-hidden="true" />
        <span className="truncate text-[13px] font-semibold">
          {contextLabel}
        </span>
        <span className="text-[12px] text-muted-foreground">
          다음 노드 선택
        </span>
        <StatusPill intent="info" className="ml-auto">
          server-owned contract
        </StatusPill>
        <button
          type="button"
          aria-label="노드 카탈로그 닫기"
          className="flex size-7 items-center justify-center border border-transparent text-muted-foreground hover:border-[#C5CBD3] hover:bg-white hover:text-foreground"
          onClick={onClose}
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col p-3">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden border border-[#C5CBD3] bg-white shadow-sm">
          <div className="flex h-12 shrink-0 items-center gap-3 border-b border-[#C5CBD3] px-3">
            <div className="flex min-w-0 items-center gap-2">
              <BookOpenCheck
                className="size-4 text-[#147D75]"
                aria-hidden="true"
              />
              <div>
                <h2 className="text-[14px] leading-4 font-semibold">
                  Pipeline node catalog
                </h2>
                <p className="text-[10px] leading-4 text-muted-foreground">
                  정형·미디어·콘텐츠·AI 노드를 같은 typed graph에 추가합니다.
                </p>
              </div>
            </div>
            <CatalogSearch query={query} onChange={setQuery} />
            {query ? (
              <button
                type="button"
                className="h-7 px-2 text-[12px] text-primary hover:bg-[#EAF2FC]"
                onClick={() => setQuery("")}
              >
                지우기
              </button>
            ) : null}
          </div>

          {nodeTypes.isLoading ? (
            <LoadingState rowCount={8} className="p-4" />
          ) : nodeTypes.error ? (
            <ErrorState
              error={nodeTypes.error}
              onRetry={() => void nodeTypes.reload()}
              className="m-4"
            />
          ) : (
            <div className="grid min-h-0 flex-1 grid-cols-[168px_320px_minmax(0,1fr)]">
              <CategoryColumn
                descriptors={descriptors}
                selected={category}
                onSelect={setCategory}
              />
              <DescriptorColumn
                descriptors={visible}
                selectedId={selected?.descriptorId ?? null}
                hasOutputNode={hasOutputNode}
                hasImportedTrainedModel={hasImportedTrainedModel}
                onSelect={setSelectedId}
              />
              <PipelineCapabilityDetails
                descriptor={selected}
                processorData={processors.data}
                processorError={processors.error}
                isProcessorLoading={processors.isLoading}
                hasOutputNode={hasOutputNode}
                trainedModelData={trainedModels.data}
                importedTrainedModelRefs={importedTrainedModelRefs}
                trainedModelUsageByRef={trainedModelUsageByRef}
                trainedModelError={trainedModels.error}
                isTrainedModelLoading={trainedModels.isLoading}
                onRetryProcessors={() => void processors.reload()}
                onRetryTrainedModels={() => void trainedModels.reload()}
                onImportTrainedModel={onImportTrainedModel}
                onRemoveTrainedModel={onRemoveTrainedModel}
                onAdd={addSelected}
              />
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function CatalogSearch({
  query,
  onChange,
}: {
  query: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="relative ml-auto w-[min(42vw,520px)]">
      <Search
        className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <Input
        autoFocus
        aria-label="파이프라인 노드 검색"
        placeholder="변환, 미디어 처리, 프롬프트, 출력 검색..."
        className="h-8 rounded-[2px] border-[#AEB6C1] bg-[#F8F9FA] pl-8 text-[13px] focus-visible:bg-white"
        value={query}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function CategoryColumn({
  descriptors,
  selected,
  onSelect,
}: {
  descriptors: readonly PipelineNodeDescriptorPayload[];
  selected: CatalogCategory;
  onSelect: (category: CatalogCategory) => void;
}) {
  return (
    <nav className="border-r bg-[#F7F8FA] p-2" aria-label="노드 카테고리">
      {(Object.keys(CATEGORY_META) as CatalogCategory[]).map((category) => {
        const meta = CATEGORY_META[category];
        const count = categoryCount(descriptors, category);
        return (
          <button
            key={category}
            type="button"
            className={cn(
              "mb-0.5 flex w-full items-center gap-2 rounded-[2px] px-2 py-1.5 text-left text-[11px]",
              selected === category
                ? "bg-[#DCEAF7] font-semibold text-[#145A8D]"
                : "hover:bg-muted",
            )}
            onClick={() => onSelect(category)}
          >
            <meta.icon className="size-3.5" />
            <span>{meta.label}</span>
            <span className="ml-auto font-mono text-[10px]">{count}</span>
          </button>
        );
      })}
    </nav>
  );
}

function DescriptorColumn({
  descriptors,
  selectedId,
  hasOutputNode,
  hasImportedTrainedModel,
  onSelect,
}: {
  descriptors: readonly PipelineNodeDescriptorPayload[];
  selectedId: string | null;
  hasOutputNode: boolean;
  hasImportedTrainedModel: boolean;
  onSelect: (descriptorId: string) => void;
}) {
  return (
    <ScrollArea className="min-h-0 border-r">
      <div className="p-2">
        {descriptors.length === 0 ? (
          <p className="p-4 text-center text-[11px] text-muted-foreground">
            검색 조건과 일치하는 노드가 없습니다.
          </p>
        ) : (
          descriptors.map((descriptor) => (
            <DescriptorButton
              key={descriptorKey(descriptor)}
              descriptor={descriptor}
              isSelected={selectedId === descriptor.descriptorId}
              hasOutputNode={hasOutputNode}
              hasImportedTrainedModel={hasImportedTrainedModel}
              onSelect={onSelect}
            />
          ))
        )}
      </div>
    </ScrollArea>
  );
}

function DescriptorButton({
  descriptor,
  isSelected,
  hasOutputNode,
  hasImportedTrainedModel,
  onSelect,
}: {
  descriptor: PipelineNodeDescriptorPayload;
  isSelected: boolean;
  hasOutputNode: boolean;
  hasImportedTrainedModel: boolean;
  onSelect: (descriptorId: string) => void;
}) {
  const state = descriptorState(
    descriptor,
    hasOutputNode,
    hasImportedTrainedModel,
  );
  return (
    <button
      type="button"
      className={cn(
        "mb-1 w-full rounded-[2px] border p-2 text-left",
        isSelected
          ? "border-[#5C9BD1] bg-[#EEF6FC]"
          : "border-transparent hover:border-border hover:bg-muted/40",
      )}
      onClick={() => onSelect(descriptor.descriptorId)}
    >
      <div className="flex items-center gap-2">
        <span className="truncate text-[12px] font-semibold">
          {descriptorLabel(descriptor.descriptorId)}
        </span>
        {state.isAddable ? (
          <CheckCircle2 className="ml-auto size-3.5 text-success" />
        ) : (
          <TriangleAlert className="ml-auto size-3.5 text-warning" />
        )}
      </div>
      <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
        {descriptor.descriptorId}@{descriptor.specVersion}
      </div>
      <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-muted-foreground">
        {state.reason}
      </p>
    </button>
  );
}

function categoryCount(
  descriptors: readonly PipelineNodeDescriptorPayload[],
  category: CatalogCategory,
): number {
  if (category === "all") return descriptors.length;
  return descriptors.filter(
    (descriptor) => descriptorCategory(descriptor.descriptorId) === category,
  ).length;
}
