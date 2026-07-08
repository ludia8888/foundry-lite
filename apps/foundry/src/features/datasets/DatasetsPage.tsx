import type { Dataset, MediaSet } from "@foundry-lite/sdk";
import { Database, GitBranch } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { PageHeader } from "@/components/shared/PageHeader";
import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { Button } from "@/components/ui/button";

import { DatasetCatalog } from "./DatasetCatalog";
import { DatasetDetail } from "./DatasetDetail";
import { MediaSetForm } from "./MediaSetForm";
import { MediaWorkspace } from "./MediaWorkspace";
import type { CatalogSelection } from "./catalog-selection";
import type { DatasetExplorerState } from "./use-dataset-explorer";
import { useDatasetExplorer } from "./use-dataset-explorer";

const PREVIEW_LIMIT = 50;

/**
 * Dataset / Media Set 상세 화면 (route /datasets).
 * 좌측 카탈로그 + 메인 상세(스키마·프리뷰 동시 표시, 버전/품질/리니지 evidence,
 * 미디어 upload→commit→process→search 파이프라인).
 */
function initialSelectionFromRef(ref: string | null): CatalogSelection | null {
  if (!ref) return null;
  const separatorIndex = ref.indexOf(".");
  if (separatorIndex <= 0 || separatorIndex === ref.length - 1) return null;
  return {
    kind: "dataset",
    namespace: ref.slice(0, separatorIndex),
    name: ref.slice(separatorIndex + 1),
  };
}

export default function DatasetsPage() {
  const [searchParams] = useSearchParams();
  const [selection, setSelection] = useState<CatalogSelection | null>(() =>
    initialSelectionFromRef(searchParams.get("ref")),
  );
  const [mediaSets, setMediaSets] = useState<MediaSet[]>([]);

  const datasetSelection = selection?.kind === "dataset" ? selection : null;
  const lineageHref = datasetSelection
    ? `/lineage?ref=${encodeURIComponent(
        `${datasetSelection.namespace}.${datasetSelection.name}`,
      )}`
    : "/lineage";
  const explorer = useDatasetExplorer(
    datasetSelection
      ? {
          namespace: datasetSelection.namespace,
          name: datasetSelection.name,
          version: datasetSelection.version,
          previewLimit: PREVIEW_LIMIT,
        }
      : null,
  );

  useEffect(() => {
    if (selection !== null || explorer.datasets.length === 0) return;
    const firstDataset = explorer.datasets[0];
    setSelection({
      kind: "dataset",
      namespace: firstDataset.namespace,
      name: firstDataset.name,
    });
  }, [explorer.datasets, selection]);

  useEffect(() => {
    if (selection?.kind !== "dataset" || explorer.datasets.length === 0) {
      return;
    }
    const selectedExists = explorer.datasets.some(
      (dataset) =>
        dataset.namespace === selection.namespace &&
        dataset.name === selection.name,
    );
    if (selectedExists) return;
    const firstDataset = explorer.datasets[0];
    setSelection({
      kind: "dataset",
      namespace: firstDataset.namespace,
      name: firstDataset.name,
    });
  }, [explorer.datasets, selection]);

  const handleSelectDataset = useCallback((dataset: Dataset) => {
    setSelection({
      kind: "dataset",
      namespace: dataset.namespace,
      name: dataset.name,
    });
  }, []);

  const handleSelectVersion = useCallback((versionId: string) => {
    setSelection((previous) =>
      previous?.kind === "dataset"
        ? { ...previous, version: versionId }
        : previous,
    );
  }, []);

  const handleRegisterMediaSet = useCallback((mediaSet: MediaSet) => {
    setMediaSets((previous) =>
      previous.some((item) => item.media_set_id === mediaSet.media_set_id)
        ? previous
        : [...previous, mediaSet],
    );
    setSelection({ kind: "media", mediaSetId: mediaSet.media_set_id });
  }, []);

  const selectedMediaSet =
    selection?.kind === "media"
      ? (mediaSets.find(
          (mediaSet) => mediaSet.media_set_id === selection.mediaSetId,
        ) ?? null)
      : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3">
        <PageHeader
          title="Dataset / Media Set"
          description="스키마·프리뷰를 함께 확인하고 버전, 품질, 리니지, 미디어 처리 evidence를 검사합니다."
          actions={
            <>
              <RequestTelemetry />
              <Button size="sm" variant="outline" asChild>
                <Link to={lineageHref}>
                  <GitBranch />
                  Data Lineage
                </Link>
              </Button>
            </>
          }
        />
      </div>

      <div className="flex min-h-0 flex-1">
        <DatasetCatalog
          datasets={explorer.datasets}
          isLoading={explorer.isLoading}
          mediaSets={mediaSets}
          selection={selection}
          onSelectDataset={handleSelectDataset}
          onSelectMediaSet={(mediaSetId) =>
            setSelection({ kind: "media", mediaSetId })
          }
          onOpenMediaSetForm={() => setSelection({ kind: "media-new" })}
          className="w-64 shrink-0 border-r"
        />

        <main className="min-h-0 min-w-0 flex-1 overflow-auto bg-canvas">
          <MainContent
            selection={selection}
            explorer={explorer}
            selectedMediaSet={selectedMediaSet}
            onSelectVersion={handleSelectVersion}
            onRegisterMediaSet={handleRegisterMediaSet}
          />
        </main>
      </div>
    </div>
  );
}

interface MainContentProps {
  selection: CatalogSelection | null;
  explorer: DatasetExplorerState;
  selectedMediaSet: MediaSet | null;
  onSelectVersion: (versionId: string) => void;
  onRegisterMediaSet: (mediaSet: MediaSet) => void;
}

function MainContent({
  selection,
  explorer,
  selectedMediaSet,
  onSelectVersion,
  onRegisterMediaSet,
}: MainContentProps) {
  if (selection?.kind === "dataset") {
    return (
      <DatasetDetail
        key={`${selection.namespace}.${selection.name}`}
        explorer={explorer}
        namespace={selection.namespace}
        name={selection.name}
        onSelectVersion={onSelectVersion}
      />
    );
  }

  if (selection?.kind === "media-new") {
    return <MediaSetForm onRegister={onRegisterMediaSet} />;
  }

  if (selection?.kind === "media") {
    if (!selectedMediaSet) {
      return (
        <div className="p-4">
          <EmptyState
            title="미디어 세트를 찾을 수 없습니다"
            description="세션 레지스트리에서 제거되었습니다. ID로 다시 불러오세요."
          />
        </div>
      );
    }
    return <MediaWorkspace mediaSet={selectedMediaSet} />;
  }

  if (explorer.isLoading) {
    return <LoadingState rowCount={8} className="p-4" />;
  }

  if (explorer.error) {
    return (
      <div className="p-4">
        <ErrorState error={explorer.error} onRetry={explorer.reload} />
      </div>
    );
  }

  return (
    <div className="p-4">
      <EmptyState
        icon={Database}
        title="데이터셋이 없습니다"
        description="Data Connection에서 소스를 동기화하거나 Pipeline을 실행해 첫 데이터셋을 만드세요."
        action={
          <Button size="sm" asChild>
            <Link to="/data/connections">Data Connection 열기</Link>
          </Button>
        }
      />
    </div>
  );
}
