import type { MediaSet } from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  foundryLiteMediaProcessingFailure,
  useFoundryLiteProvidedMediaPipeline,
} from "@foundry-lite/sdk/react";
import { Loader2, Play, Upload } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { MediaDerivativeViewer } from "./MediaDerivativeViewer";
import { MediaPipelineStepper } from "./MediaPipelineStepper";
import {
  MEDIA_DEFAULT_FORMAT,
  MEDIA_DEFAULT_PROCESSOR,
  MEDIA_DEFAULT_PROCESSOR_VERSION,
  MEDIA_DEFAULT_SCHEMA_TYPE,
  MEDIA_SEARCH_TOP_K,
} from "./media-constants";

interface MediaPipelinePanelProps {
  mediaSet: MediaSet;
  onPipelineCompleted: () => void;
}

/**
 * 미디어 업로드 파이프라인 패널: 파일 선택 → upload → commit → process →
 * index·search를 idempotency key와 함께 실행하고 단계별 evidence를 노출한다.
 */
export function MediaPipelinePanel({
  mediaSet,
  onPipelineCompleted,
}: MediaPipelinePanelProps) {
  const [file, setFile] = useState<File | null>(null);
  const [logicalPath, setLogicalPath] = useState("");
  const [searchText, setSearchText] = useState("");
  const [usedIdempotencyKey, setUsedIdempotencyKey] = useState<string | null>(
    null,
  );

  const pipeline = useFoundryLiteProvidedMediaPipeline({
    onSuccess: onPipelineCompleted,
  });

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const nextFile = event.target.files?.[0] ?? null;
    setFile(nextFile);
    if (nextFile) setLogicalPath(`docs/${nextFile.name}`);
  };

  const canRun =
    file !== null && logicalPath.trim().length > 0 && !pipeline.isRunning;

  const handleRun = () => {
    if (!file || !canRun) return;
    const nextKey = idempotencyKey(
      "media-pipeline",
      `${mediaSet.media_set_id}:${logicalPath.trim()}`,
    );
    setUsedIdempotencyKey(nextKey);
    const trimmedSearch = searchText.trim();
    void pipeline.execute({
      mediaSetId: mediaSet.media_set_id,
      idempotencyKey: nextKey,
      logicalPath: logicalPath.trim(),
      file,
      fileName: file.name,
      suppliedMimeType: file.type || "application/pdf",
      schemaType: MEDIA_DEFAULT_SCHEMA_TYPE,
      format: MEDIA_DEFAULT_FORMAT,
      process: {
        processor: MEDIA_DEFAULT_PROCESSOR,
        processorVersion: MEDIA_DEFAULT_PROCESSOR_VERSION,
      },
      indexGeneration: `gen-${Date.now()}`,
      search:
        trimmedSearch.length > 0
          ? { text: trimmedSearch, topK: MEDIA_SEARCH_TOP_K }
          : null,
    });
  };

  return (
    <section className="space-y-3 rounded border bg-card p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Upload className="size-4 text-primary" />
          <span className="section-label">업로드 파이프라인</span>
        </div>
        <span className="font-mono text-[11px] text-muted-foreground">
          processor={MEDIA_DEFAULT_PROCESSOR} (PDF 텍스트 추출)
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="media-file" className="text-xs">
            PDF 파일
          </Label>
          <Input
            id="media-file"
            type="file"
            accept="application/pdf,.pdf"
            onChange={handleFileChange}
            className="h-7 text-xs"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="media-logical-path" className="text-xs">
            논리 경로
          </Label>
          <Input
            id="media-logical-path"
            value={logicalPath}
            onChange={(event) => setLogicalPath(event.target.value)}
            placeholder="docs/contract.pdf"
            className="h-7 font-mono text-[11px]"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="media-pipeline-search" className="text-xs">
            처리 후 검색어 (선택)
          </Label>
          <Input
            id="media-pipeline-search"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            placeholder="인덱스 직후 검색할 텍스트"
            className="h-7 text-xs"
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={handleRun} disabled={!canRun}>
          {pipeline.isRunning ? <Loader2 className="animate-spin" /> : <Play />}
          파이프라인 실행
        </Button>
        {usedIdempotencyKey ? (
          <span
            className="truncate font-mono text-[11px] text-muted-foreground"
            title={usedIdempotencyKey}
          >
            idempotency_key={usedIdempotencyKey}
          </span>
        ) : null}
      </div>

      <MediaPipelineStepper pipeline={pipeline} />

      {pipeline.error ? (
        <ErrorState error={pipeline.error} onRetry={handleRun} />
      ) : null}

      {pipeline.phase === "succeeded" ? (
        <PipelineResultEvidence pipeline={pipeline} />
      ) : null}

      {pipeline.mediaDerivativeId &&
      pipeline.processing &&
      foundryLiteMediaProcessingFailure(pipeline.processing) === null ? (
        <MediaDerivativeViewer mediaDerivativeId={pipeline.mediaDerivativeId} />
      ) : null}
    </section>
  );
}

function PipelineResultEvidence({
  pipeline,
}: {
  pipeline: ReturnType<typeof useFoundryLiteProvidedMediaPipeline>;
}) {
  return (
    <div className="space-y-1.5 rounded border border-success/40 bg-success/5 p-2.5">
      <div className="flex items-center gap-2">
        <StatusPill intent="success">파이프라인 완료</StatusPill>
        {pipeline.isIndexed ? (
          <StatusPill intent="info">인덱스 완료</StatusPill>
        ) : null}
        {pipeline.isSearchable ? (
          <StatusPill intent="info">
            검색 hit {pipeline.hits.length}건
          </StatusPill>
        ) : null}
      </div>
      <div className="grid grid-cols-1 gap-x-4 gap-y-0.5 font-mono text-[11px] text-muted-foreground lg:grid-cols-2">
        <span className="truncate">
          transaction={pipeline.uploadResult?.transaction.mediaTransactionId}
        </span>
        <span className="truncate">
          item_version={pipeline.servingTruthMediaItemVersionId}
        </span>
        <span className="truncate">
          committed_versions={pipeline.committedVersionIds.join(", ")}
        </span>
        <span className="truncate">
          derivative={pipeline.mediaDerivativeId} · content_units=
          {pipeline.processing?.content_unit_count}
        </span>
        {pipeline.indexing ? (
          <span className="truncate">
            index_generation={pipeline.indexing.generation} · indexed=
            {pipeline.indexing.indexed} · failed={pipeline.indexing.failed}
          </span>
        ) : null}
      </div>
    </div>
  );
}
