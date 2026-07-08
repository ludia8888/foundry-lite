import type { Dataset } from "@foundry-lite/sdk";
import {
  ChevronDown,
  ChevronRight,
  Database,
  FileCode2,
  RefreshCw,
} from "lucide-react";
import { useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import {
  datasetRef,
  groupDatasetsByNamespace,
  type RegisteredTransform,
} from "../code-model";

interface TransformTreeProps {
  transforms: readonly RegisteredTransform[];
  datasets: readonly Dataset[];
  selectedApiName: string | null;
  isLoading: boolean;
  onSelectTransform: (transform: RegisteredTransform) => void;
  onAddInput: (ref: string) => void;
  onRefresh: () => void;
}

/**
 * 좌측 파일 트리(code-view.png의 Files 패널).
 * - SQL Transforms: previewDue로 조회한 등록 transform (선택 시 에디터에 로드 컨텍스트 전달)
 * - Datasets: datasets.list 카탈로그 (클릭 시 SQL 입력으로 추가)
 */
export function TransformTree({
  transforms,
  datasets,
  selectedApiName,
  isLoading,
  onSelectTransform,
  onAddInput,
  onRefresh,
}: TransformTreeProps) {
  const [isTransformsOpen, setIsTransformsOpen] = useState(true);
  const [isDatasetsOpen, setIsDatasetsOpen] = useState(true);
  const namespaces = groupDatasetsByNamespace(datasets);

  return (
    <div className="flex w-60 shrink-0 flex-col border-r bg-card">
      <div className="flex h-8 shrink-0 items-center justify-between border-b px-2">
        <span className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
          탐색기
        </span>
        <button
          type="button"
          title="트리 새로고침"
          className="flex size-5 items-center justify-center rounded text-muted-foreground hover:bg-muted"
          onClick={onRefresh}
        >
          <RefreshCw
            className={cn("size-3", isLoading ? "animate-spin" : null)}
          />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {/* 등록된 SQL transforms */}
        <TreeSection
          label="SQL Transforms"
          count={transforms.length}
          isOpen={isTransformsOpen}
          onToggle={() => setIsTransformsOpen((prev) => !prev)}
        >
          {transforms.length === 0 ? (
            <div className="px-6 py-1.5 text-[11px] text-muted-foreground">
              {isLoading ? "불러오는 중..." : "등록된 transform이 없습니다."}
            </div>
          ) : (
            transforms.map((transform) => (
              <button
                key={transform.transformId}
                type="button"
                className={cn(
                  "flex w-full items-center gap-1.5 py-1 pr-2 pl-6 text-left text-[12px] transition-colors",
                  selectedApiName === transform.apiName
                    ? "bg-primary/10 text-primary"
                    : "text-foreground hover:bg-muted",
                )}
                onClick={() => onSelectTransform(transform)}
                title={`${transform.apiName} → ${transform.outputDatasetRef}`}
              >
                <FileCode2 className="size-3.5 shrink-0 text-[#00847A]" />
                <span className="truncate">{transform.apiName}</span>
                {transform.lastSuccessfulRunId ? (
                  <span className="ml-auto size-1.5 shrink-0 rounded-full bg-success" />
                ) : transform.due ? (
                  <span className="ml-auto size-1.5 shrink-0 rounded-full bg-[#EC9A3C]" />
                ) : null}
              </button>
            ))
          )}
        </TreeSection>

        {/* 입력 데이터셋 카탈로그 */}
        <TreeSection
          label="Datasets"
          count={datasets.length}
          isOpen={isDatasetsOpen}
          onToggle={() => setIsDatasetsOpen((prev) => !prev)}
        >
          {namespaces.map(({ namespace, datasets: nsDatasets }) => (
            <div key={namespace}>
              <div className="flex items-center gap-1 py-0.5 pr-2 pl-6 text-[11px] font-medium text-muted-foreground">
                {namespace}
              </div>
              {nsDatasets.map((dataset) => {
                const ref = datasetRef(dataset);
                return (
                  <button
                    key={dataset.id}
                    type="button"
                    className="flex w-full items-center gap-1.5 py-1 pr-2 pl-9 text-left text-[12px] text-foreground transition-colors hover:bg-muted"
                    onClick={() => onAddInput(ref)}
                    title={`${ref} 를 입력으로 추가`}
                  >
                    <Database className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">{dataset.name}</span>
                  </button>
                );
              })}
            </div>
          ))}
          {datasets.length === 0 ? (
            <div className="px-6 py-1.5 text-[11px] text-muted-foreground">
              {isLoading ? "불러오는 중..." : "데이터셋이 없습니다."}
            </div>
          ) : null}
        </TreeSection>
      </div>

      <div className="shrink-0 border-t px-2 py-1.5">
        <StatusPill intent="neutral" className="!text-[9px]">
          Git parity partial
        </StatusPill>
      </div>
    </div>
  );
}

interface TreeSectionProps {
  label: string;
  count: number;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

function TreeSection({
  label,
  count,
  isOpen,
  onToggle,
  children,
}: TreeSectionProps) {
  return (
    <div className="mb-1">
      <button
        type="button"
        className="flex w-full items-center gap-1 px-2 py-1 text-left text-[11px] font-semibold text-foreground hover:bg-muted"
        onClick={onToggle}
      >
        {isOpen ? (
          <ChevronDown className="size-3" />
        ) : (
          <ChevronRight className="size-3" />
        )}
        {label}
        <span className="ml-1 font-mono text-[10px] font-normal text-muted-foreground">
          {count}
        </span>
      </button>
      {isOpen ? children : null}
    </div>
  );
}
