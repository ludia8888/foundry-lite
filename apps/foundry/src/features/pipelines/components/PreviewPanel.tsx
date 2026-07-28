import type {
  PipelineGraphV2,
  PipelinePreviewRun,
} from "@foundry-lite/sdk";
import {
  Ban,
  Clock3,
  FileSearch,
  Layers3,
  LoaderCircle,
  MousePointerClick,
  Play,
  RotateCcw,
} from "lucide-react";
import {
  type KeyboardEvent,
  useId,
  useMemo,
  useState,
} from "react";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  compactJson,
  displayCell,
  numberValue,
  previewItems,
  previewOutput,
  previewStatusLabel,
  recordList,
  recordValue,
  textValue,
  type PreviewRecord,
} from "../pipeline-preview-model";
import { nodeLabel, type PipelineCanvasNode } from "../pipeline-model";
import {
  isTerminalPreviewStatus,
  usePipelinePreviewRun,
} from "../use-pipeline-preview-run";
import { ArtifactPassport } from "./ArtifactPassport";

interface PreviewPanelProps {
  branchId: string | null;
  graph: PipelineGraphV2 | null;
  node: PipelineCanvasNode | null;
  isGraphDirty: boolean;
}

/** 저장되지 않은 현재 draft graph를 실제 no-commit preview run으로 실행하는 하단 패널. */
export function PreviewPanel({
  branchId,
  graph,
  node,
  isGraphDirty,
}: PreviewPanelProps) {
  const preview = usePipelinePreviewRun({
    branchId,
    graph,
    targetNodeId: node?.id ?? null,
  });
  const output = previewOutput(preview.run, node?.id ?? null);

  if (!node) {
    return (
      <div className="h-full">
        <PreviewOnlyNotice />
        <EmptyState
          icon={MousePointerClick}
          title="미리볼 노드를 선택하세요"
          description="선택한 노드까지의 현재 draft graph를 실행해 실제 행·미디어 증거를 확인합니다."
          className="m-3 border-0"
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PreviewOnlyNotice />
      <PreviewRunToolbar
        node={node}
        run={preview.run}
        isGraphDirty={isGraphDirty}
        isStarting={preview.isStarting}
        isCancelling={preview.isCancelling}
        isRunning={preview.isRunning}
        canStart={preview.canStart}
        onStart={() => void preview.start()}
        onCancel={() => void preview.cancel()}
      />
      <div className="min-h-0 flex-1" aria-live="polite">
        {preview.error ? (
          <ErrorState
            error={preview.error}
            onRetry={() => void preview.start()}
            className="m-3"
          />
        ) : preview.run ? (
          <PreviewRunResult run={preview.run} output={output} />
        ) : (
          <PreviewReadyState node={node} isGraphDirty={isGraphDirty} />
        )}
      </div>
    </div>
  );
}

function PreviewOnlyNotice() {
  return (
    <div className="flex h-7 shrink-0 items-center gap-2 border-b border-[#E2C98B] bg-[#FFF8E7] px-3 text-[11px] text-[#725B20]">
      <FileSearch className="size-3.5" />
      <strong>미리보기 전용 · 출력 버전이 생성되지 않음</strong>
      <span className="ml-auto font-mono text-[10px]">
        commitForbidden=true · serving=false
      </span>
    </div>
  );
}

function PreviewRunToolbar({
  node,
  run,
  isGraphDirty,
  isStarting,
  isCancelling,
  isRunning,
  canStart,
  onStart,
  onCancel,
}: {
  node: PipelineCanvasNode;
  run: PipelinePreviewRun | null;
  isGraphDirty: boolean;
  isStarting: boolean;
  isCancelling: boolean;
  isRunning: boolean;
  canStart: boolean;
  onStart: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="flex h-10 shrink-0 items-center gap-2 border-b bg-card px-3">
      <span className="section-label">{nodeLabel(node)}</span>
      <span className="font-mono text-[10px] text-muted-foreground">
        target={node.id}
      </span>
      {isGraphDirty ? (
        <StatusPill intent="warning">unsaved draft 포함</StatusPill>
      ) : (
        <StatusPill intent="neutral">saved graph와 동일</StatusPill>
      )}
      {run ? (
        <StatusPill intent={previewStatusIntent(run.status)}>
          {previewStatusLabel(run.status)}
        </StatusPill>
      ) : null}
      <div className="ml-auto flex items-center gap-1.5">
        {isRunning ? (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-[11px]"
            disabled={isCancelling}
            onClick={onCancel}
          >
            <Ban className="size-3.5" />
            {isCancelling ? "취소 요청 중..." : "미리보기 취소"}
          </Button>
        ) : null}
        <Button
          size="sm"
          className="h-7 text-[11px]"
          disabled={!canStart || isStarting || isRunning}
          onClick={onStart}
        >
          {isStarting || isRunning ? (
            <LoaderCircle className="size-3.5 animate-spin motion-reduce:animate-none" />
          ) : run ? (
            <RotateCcw className="size-3.5" />
          ) : (
            <Play className="size-3.5" />
          )}
          {isStarting
            ? "요청 중..."
            : run
              ? "현재 draft 다시 실행"
              : "현재 draft 미리보기 실행"}
        </Button>
      </div>
    </div>
  );
}

function PreviewReadyState({
  node,
  isGraphDirty,
}: {
  node: PipelineCanvasNode;
  isGraphDirty: boolean;
}) {
  return (
    <div className="grid h-full place-items-center p-4">
      <div className="max-w-xl rounded border border-dashed bg-muted/20 px-5 py-4 text-center">
        <Play className="mx-auto size-5 text-primary" />
        <p className="mt-2 text-[12px] font-semibold">
          {nodeLabel(node)}까지 실제 데이터를 읽어 실행합니다
        </p>
        <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
          {isGraphDirty
            ? "아직 저장하지 않은 노드 설정과 연결도 요청 body의 draft graph에 포함됩니다."
            : "현재 브랜치 graph를 preview 격리 경로에서 실행합니다."}
          {" "}중간 artifact와 output은 serving Dataset·Media version으로 commit되지
          않습니다.
        </p>
        <p className="mt-1 text-[10px] text-muted-foreground">
          {node.type === "use_llm"
            ? "Use LLM 결과 미리보기는 일반 테이블 한도와 별개로 최대 50행입니다."
            : "일반 테이블 미리보기는 기본값과 최대값이 모두 500행입니다."}
        </p>
      </div>
    </div>
  );
}

function PreviewRunResult({
  run,
  output,
}: {
  run: PipelinePreviewRun;
  output: PreviewRecord | null;
}) {
  const [activeTab, setActiveTab] = useState<PreviewResultTab>("data");
  const tabSetId = useId();
  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    tab: PreviewResultTab,
  ) => {
    const nextTab = nextPreviewResultTab(tab, event.key);
    if (!nextTab) return;
    event.preventDefault();
    setActiveTab(nextTab);
    document.getElementById(`${tabSetId}-tab-${nextTab}`)?.focus();
  };
  if (!isSuccessfulPreview(run.status)) {
    return <PreviewRunProgressOrFailure run={run} />;
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        role="tablist"
        aria-label="미리보기 결과 보기"
        className="flex h-8 shrink-0 items-end border-b border-[#C5CBD3] bg-[#F7F8FA] px-2"
      >
        {(Object.keys(PREVIEW_RESULT_LABELS) as PreviewResultTab[]).map(
          (tab) => (
            <button
              key={tab}
              id={`${tabSetId}-tab-${tab}`}
              type="button"
              role="tab"
              aria-controls={`${tabSetId}-panel-${tab}`}
              aria-selected={activeTab === tab}
              tabIndex={activeTab === tab ? 0 : -1}
              className={cn(
                "h-8 border-b-2 px-3 text-[11px]",
                activeTab === tab
                  ? "border-primary bg-white font-semibold text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
              onClick={() => setActiveTab(tab)}
              onKeyDown={(event) => handleTabKeyDown(event, tab)}
            >
              {PREVIEW_RESULT_LABELS[tab]}
            </button>
          ),
        )}
      </div>
      <div
        id={`${tabSetId}-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`${tabSetId}-tab-${activeTab}`}
        className="min-h-0 flex-1 overflow-auto"
      >
        {activeTab === "data" ? (
          <div className="p-3">
            <PreviewEvidenceBar run={run} output={output} />
            <PreviewArtifactBody
              output={output}
              previewLimits={recordValue(run.limits)}
            />
          </div>
        ) : null}
        {activeTab === "structure" ? (
          <PreviewStructure output={output} />
        ) : null}
        {activeTab === "lineage" ? (
          <PreviewLineage run={run} output={output} />
        ) : null}
        {activeTab === "evidence" ? (
          <ArtifactPassport
            run={run}
            output={output}
            className="w-full border-l-0"
          />
        ) : null}
      </div>
    </div>
  );
}

type PreviewResultTab = "data" | "structure" | "lineage" | "evidence";

const PREVIEW_RESULT_LABELS: Record<PreviewResultTab, string> = {
  data: "미리보기",
  structure: "구조",
  lineage: "계보",
  evidence: "Artifact Passport",
};

function nextPreviewResultTab(
  current: PreviewResultTab,
  key: string,
): PreviewResultTab | null {
  const tabs = Object.keys(PREVIEW_RESULT_LABELS) as PreviewResultTab[];
  if (key === "Home") return tabs[0] ?? null;
  if (key === "End") return tabs.at(-1) ?? null;
  if (!["ArrowLeft", "ArrowRight"].includes(key)) return null;
  const direction = key === "ArrowRight" ? 1 : -1;
  const currentIndex = tabs.indexOf(current);
  return tabs[(currentIndex + direction + tabs.length) % tabs.length] ?? null;
}

function PreviewStructure({ output }: { output: PreviewRecord | null }) {
  const items = previewItems(output);
  const keys = prioritizedKeys(items);
  return (
    <div className="grid gap-3 p-3 md:grid-cols-[220px_minmax(0,1fr)]">
      <div className="border border-[#C5CBD3] bg-white">
        <div className="border-b border-[#C5CBD3] bg-[#F7F8FA] px-2.5 py-1.5 text-[11px] font-semibold">
          Artifact structure
        </div>
        <dl className="space-y-1.5 p-2.5 text-[10px]">
          <EvidenceField
            label="artifact"
            value={textValue(output?.artifactKind) ?? "-"}
          />
          <EvidenceField label="items" value={String(items.length)} />
          <EvidenceField label="fields" value={String(keys.length)} />
        </dl>
      </div>
      <div className="border border-[#C5CBD3] bg-white">
        <div className="border-b border-[#C5CBD3] bg-[#F7F8FA] px-2.5 py-1.5 text-[11px] font-semibold">
          반환 필드
        </div>
        <div className="flex flex-wrap gap-1.5 p-2.5">
          {keys.length > 0 ? (
            keys.map((key) => (
              <span
                key={key}
                className="border border-[#C5CBD3] bg-[#F7F8FA] px-1.5 py-0.5 font-mono text-[10px]"
              >
                {key}
              </span>
            ))
          ) : (
            <span className="text-[10px] text-muted-foreground">
              반환된 필드가 없습니다.
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function PreviewLineage({
  run,
  output,
}: {
  run: PipelinePreviewRun;
  output: PreviewRecord | null;
}) {
  return (
    <div className="p-3">
      <div className="border border-[#C5CBD3] bg-white">
        <div className="border-b border-[#C5CBD3] bg-[#F7F8FA] px-2.5 py-1.5 text-[11px] font-semibold">
          미리보기 실행 계보
        </div>
        <div className="grid gap-2 p-3 font-mono text-[10px] md:grid-cols-2">
          <EvidenceField label="preview run" value={run.id} />
          <EvidenceField
            label="target node"
            value={textValue(run.targetNodeId) ?? "-"}
          />
          <EvidenceField
            label="graph"
            value={textValue(run.graphFingerprint) ?? "-"}
          />
          <EvidenceField
            label="artifact"
            value={textValue(output?.artifactKind) ?? "-"}
          />
          <EvidenceField label="serving" value="false" />
          <EvidenceField label="commit" value="forbidden" />
        </div>
      </div>
    </div>
  );
}

function PreviewRunProgressOrFailure({ run }: { run: PipelinePreviewRun }) {
  const status = run.status.toUpperCase();
  if (!isTerminalPreviewStatus(status)) {
    return (
      <div className="grid h-full place-items-center">
        <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" />
          preview run {run.id} · {previewStatusLabel(status)}
        </div>
      </div>
    );
  }
  const error = recordValue(run.error);
  return (
    <div className="m-3 rounded border border-destructive/30 bg-destructive/5 p-3">
      <div className="flex items-center gap-2">
        <StatusPill intent="danger">{previewStatusLabel(status)}</StatusPill>
        <span className="font-mono text-[10px] text-muted-foreground">
          run={run.id}
        </span>
      </div>
      <p className="mt-2 text-[12px] font-semibold text-destructive">
        {textValue(error?.message) ??
          textValue(error?.detail) ??
          "미리보기 실행이 완료되지 않았습니다."}
      </p>
      {error ? (
        <pre className="mt-2 overflow-auto rounded bg-background p-2 font-mono text-[10px]">
          {compactJson(error)}
        </pre>
      ) : null}
    </div>
  );
}

function PreviewEvidenceBar({
  run,
  output,
}: {
  run: PipelinePreviewRun;
  output: PreviewRecord | null;
}) {
  const items = previewItems(output);
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2 text-[10px]">
      <StatusPill intent={previewStatusIntent(run.status)}>
        {previewStatusLabel(run.status)}
      </StatusPill>
      <span className="font-mono text-muted-foreground">run={run.id}</span>
      <span className="font-mono text-muted-foreground">
        artifact={textValue(output?.artifactKind) ?? "-"}
      </span>
      <span className="font-mono text-muted-foreground">
        actual items={items.length}
      </span>
      <span className="ml-auto font-mono text-muted-foreground">
        graph={shortValue(textValue(run.graphFingerprint))}
      </span>
    </div>
  );
}

function PreviewArtifactBody({
  output,
  previewLimits,
}: {
  output: PreviewRecord | null;
  previewLimits: PreviewRecord | null;
}) {
  const items = previewItems(output);
  const artifactKind = textValue(output?.artifactKind) ?? "";
  if (!output || items.length === 0) {
    return (
      <EmptyState
        title="반환된 preview item이 없습니다"
        description="노드 실행은 완료됐지만 현재 한도와 입력에서 표시할 행·미디어가 없습니다."
        className="border-0"
      />
    );
  }
  if (items.some((item) => derivativeUnits(item).length > 0)) {
    return (
      <MediaDerivativeEvidence
        items={items}
        previewLimits={previewLimits}
      />
    );
  }
  if (
    artifactKind.includes("media") ||
    items.some((item) => textValue(item.mediaItemVersionId))
  ) {
    return <MediaEvidence items={items} />;
  }
  if (items.some((item) => textValue(item.text))) {
    return <ContentUnitEvidence items={items} />;
  }
  return <PreviewRowsTable items={items} />;
}

function PreviewRowsTable({ items }: { items: readonly PreviewRecord[] }) {
  const rows = useMemo(
    () => items.map((value, index) => ({ key: previewRowKey(value, index), value })),
    [items],
  );
  const columns = useMemo<
    DataTableColumn<{ key: string; value: PreviewRecord }>[]
  >(() => {
    const keys = prioritizedKeys(items);
    return keys.map((key) => ({
      key,
      header: key,
      isMono: true,
      render: (row) => displayCell(row.value[key]),
    }));
  }, [items]);
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(row) => row.key}
      emptyMessage="미리볼 실제 행이 없습니다."
      className="max-h-[240px]"
    />
  );
}

function MediaEvidence({ items }: { items: readonly PreviewRecord[] }) {
  return (
    <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
      {items.map((item, index) => (
        <article
          key={previewRowKey(item, index)}
          className="rounded border bg-card p-2.5"
        >
          <div className="flex items-center gap-2">
            <FileSearch className="size-4 text-primary" />
            <span className="truncate text-[12px] font-semibold">
              {textValue(item.logicalPath) ??
                textValue(item.name) ??
                `media item ${index + 1}`}
            </span>
          </div>
          <EvidenceField
            label="media version"
            value={textValue(item.mediaItemVersionId) ?? "-"}
          />
          <EvidenceField
            label="mime / format"
            value={
              textValue(item.mimeType) ??
              textValue(item.sniffedMimeType) ??
              textValue(item.format) ??
              "-"
            }
          />
          <EvidenceField
            label="processor"
            value={textValue(item.processorId) ?? "source selection"}
          />
          <EvidenceField
            label="bytes"
            value={String(item.byteSize ?? item.size ?? "-")}
          />
        </article>
      ))}
    </div>
  );
}

function MediaDerivativeEvidence({
  items,
  previewLimits,
}: {
  items: readonly PreviewRecord[];
  previewLimits: PreviewRecord | null;
}) {
  return (
    <div className="space-y-3">
      {items.map((item, index) => (
        <MediaDerivativeCard
          key={previewRowKey(item, index)}
          item={item}
          index={index}
          previewLimits={previewLimits}
        />
      ))}
    </div>
  );
}

function MediaDerivativeCard({
  item,
  index,
  previewLimits,
}: {
  item: PreviewRecord;
  index: number;
  previewLimits: PreviewRecord | null;
}) {
  const units = derivativeUnits(item);
  const evidence = recordValue(item.processingEvidence);
  const requested = recordValue(evidence?.requested);
  const applied = recordValue(evidence?.applied);
  const observed = recordValue(evidence?.observed);
  const requestedValue =
    formatEvidenceMap(requested) ??
    formatPreviewRequestCap(previewLimits, item, units);
  const appliedValue = formatEvidenceMap(applied);
  const observedValue =
    formatEvidenceMap(observed) ?? deriveObservedBounds(units);
  const sceneCount = derivativeSceneCount(item, observed, units);
  return (
    <article className="border border-[#C5CBD3] bg-white">
      <header className="flex flex-wrap items-center gap-2 border-b border-[#C5CBD3] bg-[#F7F8FA] px-3 py-2">
        <Layers3 className="size-4 text-primary" />
        <h3 className="text-[12px] font-semibold">
          {textValue(item.derivativeKind) ?? `media derivative ${index + 1}`}
        </h3>
        <StatusPill intent="info">{units.length} units</StatusPill>
        {sceneCount !== null ? (
          <StatusPill intent="neutral">{sceneCount} scenes</StatusPill>
        ) : null}
        <span
          className="ml-auto max-w-[360px] truncate font-mono text-[10px] text-muted-foreground"
          title={sourceMediaVersion(item, units)}
        >
          source={sourceMediaVersion(item, units)}
        </span>
      </header>
      <div className="grid gap-x-5 gap-y-1 border-b border-[#C5CBD3] px-3 py-2 md:grid-cols-2 xl:grid-cols-3">
        <EvidenceField
          label="processor"
          value={processingProcessorId(item)}
        />
        <EvidenceField
          label="spec hash"
          value={shortValue(processingSpecHash(item))}
        />
        <EvidenceField label="model" value={processingModelPin(item)} />
        {requested ? (
          <EvidenceField
            label="requested bounds"
            value={requestedValue ?? "-"}
            shouldWrap
          />
        ) : (
          <EvidenceField
            label="preview request / cap"
            value={requestedValue ?? "-"}
            shouldWrap
          />
        )}
        <EvidenceField
          label="applied bounds"
          value={appliedValue ?? "not reported"}
          shouldWrap
        />
        <EvidenceField
          label={observed ? "observed" : "observed from units"}
          value={observedValue ?? "-"}
          shouldWrap
        />
      </div>
      <ol aria-label="파생 미디어 content units" className="divide-y divide-[#D8DDE3]">
        {units.map((unit, unitIndex) => (
          <MediaDerivativeUnit
            key={previewRowKey(unit, unitIndex)}
            unit={unit}
            index={unitIndex}
          />
        ))}
      </ol>
    </article>
  );
}

function MediaDerivativeUnit({
  unit,
  index,
}: {
  unit: PreviewRecord;
  index: number;
}) {
  const speaker = textValue(unit.speaker);
  const language = textValue(unit.language);
  const processingEvidence = recordValue(unit.processingEvidence);
  return (
    <li className="grid min-w-0 md:grid-cols-[168px_minmax(0,1fr)]">
      <div className="border-l-2 border-l-primary bg-[#FAFBFC] px-3 py-2.5 font-mono text-[10px]">
        <div className="flex items-center gap-1.5 font-semibold text-foreground">
          <Clock3 className="size-3.5 text-primary" />
          {formatUnitLocator(unit, index)}
        </div>
        <div className="mt-1 text-muted-foreground">
          kind={textValue(unit.unitKind) ?? "content_unit"}
        </div>
        <div className="text-muted-foreground">
          ordinal={String(unit.ordinal ?? index)}
        </div>
        {speaker ? (
          <div className="text-muted-foreground">speaker={speaker}</div>
        ) : null}
        {language ? (
          <div className="text-muted-foreground">language={language}</div>
        ) : null}
      </div>
      <div className="min-w-0 px-3 py-2.5">
        <p className="whitespace-pre-wrap text-[12px] leading-5">
          {textValue(unit.text) ?? "텍스트 표현이 없는 미디어 unit"}
        </p>
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[9px] text-muted-foreground">
          <span>source={sourceMediaVersion(unit, [unit])}</span>
          {processingEvidence ? (
            <span className="break-all">
              processing={formatProcessingEvidence(processingEvidence)}
            </span>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function ContentUnitEvidence({ items }: { items: readonly PreviewRecord[] }) {
  return (
    <div className="space-y-2">
      {items.map((item, index) => (
        <article
          key={previewRowKey(item, index)}
          className="grid gap-2 rounded border bg-card p-2.5 md:grid-cols-[160px_minmax(0,1fr)]"
        >
          <div className="space-y-1 break-all font-mono text-[10px] text-muted-foreground">
            <div>source={sourceMediaVersion(item, [item])}</div>
            <div>timecode={formatUnitTimeRange(item) ?? "-"}</div>
            <div>page={String(item.pageNumber ?? item.page ?? "-")}</div>
            <div>bbox={displayCell(item.bbox ?? item.boundingBox)}</div>
            <div>locator={displayCell(item.sourceLocator)}</div>
            <div>structure={displayCell(item.structure)}</div>
            <div>confidence={displayCell(item.confidence)}</div>
            <div>processor={processingProcessorId(item)}</div>
            <div>spec={shortValue(processingSpecHash(item))}</div>
            <div>speaker={textValue(item.speaker) ?? "-"}</div>
            <div>language={textValue(item.language) ?? "-"}</div>
            {recordValue(item.processingEvidence) ? (
              <div>
                processing=
                {formatProcessingEvidence(
                  recordValue(item.processingEvidence) as PreviewRecord,
                )}
              </div>
            ) : null}
          </div>
          <p className="whitespace-pre-wrap text-[12px] leading-5">
            {textValue(item.text) ?? "-"}
          </p>
        </article>
      ))}
    </div>
  );
}

function derivativeUnits(item: PreviewRecord): PreviewRecord[] {
  return recordList(item.units);
}

function sourceMediaVersion(
  item: PreviewRecord,
  units: readonly PreviewRecord[],
): string {
  return (
    textValue(item.sourceMediaItemVersionId) ??
    textValue(item.mediaItemVersionId) ??
    units.map((unit) => textValue(unit.sourceMediaItemVersionId)).find(Boolean) ??
    "-"
  );
}

function derivativeSceneCount(
  item: PreviewRecord,
  observed: PreviewRecord | null,
  units: readonly PreviewRecord[],
): number | null {
  const reported =
    numberValue(observed?.sceneCount) ??
    numberValue(observed?.scene_count) ??
    numberValue(item.sceneCount) ??
    numberValue(item.scene_count);
  if (reported !== null) return reported;
  const count = units.filter((unit) => isSceneUnit(unit)).length;
  return count > 0 ? count : null;
}

function isSceneUnit(unit: PreviewRecord): boolean {
  const kind = (textValue(unit.unitKind) ?? "").toLowerCase();
  return kind.includes("scene") || kind.includes("video_frame");
}

function formatUnitLocator(unit: PreviewRecord, index: number): string {
  const timeRange = formatUnitTimeRange(unit);
  if (timeRange) return timeRange;
  const page = numberValue(unit.pageNumber) ?? numberValue(unit.page);
  return page !== null ? `page ${page}` : `unit ${unit.ordinal ?? index}`;
}

function formatUnitTimeRange(unit: PreviewRecord): string | null {
  const timecode = recordValue(unit.timecode);
  const startMs =
    numberValue(unit.startMs) ?? numberValue(timecode?.startMs);
  const endMs = numberValue(unit.endMs) ?? numberValue(timecode?.endMs);
  return formatTimeRange(startMs, endMs);
}

function formatTimeRange(
  startMs: number | null,
  endMs: number | null,
): string | null {
  if (startMs === null && endMs === null) return null;
  if (startMs === null) {
    return endMs === null ? null : `≤ ${formatTimecode(endMs)}`;
  }
  if (endMs === null || endMs === startMs) return formatTimecode(startMs);
  return `${formatTimecode(startMs)} – ${formatTimecode(endMs)}`;
}

function formatTimecode(value: number): string {
  const totalMs = Math.max(0, Math.round(value));
  const milliseconds = totalMs % 1000;
  const totalSeconds = Math.floor(totalMs / 1000);
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);
  const base = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
  return hours > 0 ? `${String(hours).padStart(2, "0")}:${base}` : base;
}

function formatEvidenceMap(value: PreviewRecord | null): string | null {
  if (!value || Object.keys(value).length === 0) return null;
  return Object.entries(value)
    .map(([key, item]) => `${key}=${formatEvidenceValue(key, item)}`)
    .join(" · ");
}

function formatEvidenceValue(key: string, value: unknown): string {
  const nested = recordValue(value);
  if (nested) {
    const range = formatTimeRange(
      numberValue(nested.startMs),
      numberValue(nested.endMs),
    );
    return range ?? compactJson(nested);
  }
  const numeric = numberValue(value);
  if (numeric !== null && key.toLowerCase().endsWith("ms")) {
    return formatTimecode(numeric);
  }
  if (numeric !== null && key.toLowerCase().includes("seconds")) {
    return `${numeric}s`;
  }
  return displayCell(value);
}

function formatPreviewRequestCap(
  limits: PreviewRecord | null,
  item: PreviewRecord,
  units: readonly PreviewRecord[],
): string | null {
  if (!limits) return null;
  const values: string[] = [];
  const kind = (textValue(item.derivativeKind) ?? "").toLowerCase();
  const isTimed = units.some((unit) => formatUnitTimeRange(unit) !== null);
  const hasPages = units.some((unit) => numberValue(unit.pageNumber) !== null);
  const seconds = numberValue(limits.audioVideoSeconds);
  const scenes = numberValue(limits.sceneCount);
  const pages = numberValue(limits.pdfPages);
  if (isTimed && seconds !== null) values.push(`audio/video ≤ ${seconds}s`);
  if ((kind.includes("video") || units.some(isSceneUnit)) && scenes !== null) {
    values.push(`scenes ≤ ${scenes}`);
  }
  if (hasPages && pages !== null) values.push(`PDF pages ≤ ${pages}`);
  return values.length > 0 ? values.join(" · ") : null;
}

function deriveObservedBounds(units: readonly PreviewRecord[]): string | null {
  const starts = units.flatMap((unit) => {
    const timecode = recordValue(unit.timecode);
    const value = numberValue(unit.startMs) ?? numberValue(timecode?.startMs);
    return value === null ? [] : [value];
  });
  const ends = units.flatMap((unit) => {
    const timecode = recordValue(unit.timecode);
    const value =
      numberValue(unit.endMs) ??
      numberValue(timecode?.endMs) ??
      numberValue(unit.startMs);
    return value === null ? [] : [value];
  });
  if (starts.length > 0 || ends.length > 0) {
    return formatTimeRange(
      starts.length > 0 ? Math.min(...starts) : null,
      ends.length > 0 ? Math.max(...ends) : null,
    );
  }
  const pages = units.flatMap((unit) => {
    const value = numberValue(unit.pageNumber) ?? numberValue(unit.page);
    return value === null ? [] : [value];
  });
  return pages.length > 0
    ? `pages ${Math.min(...pages)} – ${Math.max(...pages)}`
    : `${units.length} units`;
}

function formatModelPin(value: unknown): string {
  const model = recordValue(value);
  if (!model) return "-";
  const name = textValue(model.name) ?? textValue(model.model) ?? "-";
  const version = textValue(model.version) ?? textValue(model.modelVersion);
  return version ? `${name}@${version}` : name;
}

function processingProcessorId(item: PreviewRecord): string {
  const evidence = recordValue(item.processingEvidence);
  return (
    textValue(item.processorId) ??
    textValue(evidence?.processorId) ??
    textValue(evidence?.processor) ??
    "-"
  );
}

function processingSpecHash(item: PreviewRecord): string | null {
  const evidence = recordValue(item.processingEvidence);
  return (
    textValue(item.processingSpecHash) ??
    textValue(evidence?.processingSpecHash) ??
    textValue(evidence?.specHash)
  );
}

function processingModelPin(item: PreviewRecord): string {
  const evidence = recordValue(item.processingEvidence);
  return formatModelPin(item.model ?? evidence?.model);
}

function formatProcessingEvidence(evidence: PreviewRecord): string {
  const requested = formatEvidenceMap(recordValue(evidence.requested));
  const applied = formatEvidenceMap(recordValue(evidence.applied));
  const observed = formatEvidenceMap(recordValue(evidence.observed));
  return [
    requested ? `requested(${requested})` : null,
    applied ? `applied(${applied})` : null,
    observed ? `observed(${observed})` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" · ");
}

function EvidenceField({
  label,
  value,
  shouldWrap = false,
}: {
  label: string;
  value: string;
  shouldWrap?: boolean;
}) {
  return (
    <div className="mt-1 grid grid-cols-[86px_minmax(0,1fr)] gap-2 font-mono text-[10px]">
      <span className="text-muted-foreground">{label}</span>
      <span
        className={cn(shouldWrap ? "break-words" : "truncate")}
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

function prioritizedKeys(items: readonly PreviewRecord[]): string[] {
  const ignored = new Set(["securityEnvelope", "embedding", "units"]);
  const priority = [
    "id",
    "order_id",
    "name",
    "text",
    "pageNumber",
    "processorId",
  ];
  const keys = [...new Set(items.flatMap((item) => Object.keys(item)))].filter(
    (key) => !ignored.has(key),
  );
  return keys
    .sort((left, right) => {
      const leftOrder = priority.indexOf(left);
      const rightOrder = priority.indexOf(right);
      const normalizedLeft = leftOrder < 0 ? priority.length : leftOrder;
      const normalizedRight = rightOrder < 0 ? priority.length : rightOrder;
      return normalizedLeft - normalizedRight || left.localeCompare(right);
    })
    .slice(0, 12);
}

function previewRowKey(item: PreviewRecord, index: number): string {
  return (
    textValue(item.id) ??
    textValue(item.contentUnitId) ??
    textValue(item.mediaItemVersionId) ??
    `${index}:${compactJson(item)}`
  );
}

function isSuccessfulPreview(status: string): boolean {
  return ["SUCCEEDED", "PARTIAL"].includes(status.toUpperCase());
}

function previewStatusIntent(status: string): StatusIntent {
  const normalized = status.toUpperCase();
  if (normalized === "SUCCEEDED") return "success";
  if (normalized === "PARTIAL" || normalized === "CANCEL_REQUESTED") {
    return "warning";
  }
  if (normalized === "FAILED" || normalized === "CANCELLED") return "danger";
  return "info";
}

function shortValue(value: string | null): string {
  if (!value) return "-";
  return value.length > 16 ? `${value.slice(0, 16)}…` : value;
}
