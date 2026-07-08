import { idempotencyKey } from "@foundry-lite/sdk";
import { useFoundryLiteProvidedSourceOnboarding } from "@foundry-lite/sdk/react";
import { ExternalLink, FileUp, Play, RotateCw, Table2, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import {
  isValidDatasetRef,
  isValidIdentifier,
  readNumberField,
  readTextField,
  sanitizeIdentifier,
  toDatasetHref,
  toOperationsHref,
} from "../source-model";
import type { WizardCompletion } from "./CsvUploadFlow";
import {
  EvidenceList,
  EvidenceRow,
  PhaseTimeline,
  resolvePhaseItems,
} from "./WizardEvidence";
import { WizardField, WizardStepFooter } from "./WizardFields";
import { WizardStepLayout } from "./WizardStepLayout";

const BATCH_STEPS = [
  { id: "configure", title: "소스 구성" },
  { id: "files", title: "파일 & 실행" },
  { id: "evidence", title: "증거 확인" },
] as const;

interface BatchFileEntry {
  file: File;
  datasetRefInput: string;
  isDatasetRefTouched: boolean;
}

interface BatchFileFlowProps {
  initialDisplayName: string;
  onExit: () => void;
  onCancel: () => void;
  onComplete: (completion: WizardCompletion) => void;
}

function entryDatasetRef(entry: BatchFileEntry): string {
  if (entry.isDatasetRefTouched) return entry.datasetRefInput;
  const base = sanitizeIdentifier(entry.file.name.replace(/\.[^.]+$/, ""));
  return base ? `demo.${base}` : "";
}

/**
 * 배치 파일 온보딩 flow: 소스 구성 → 파일별 대상 데이터셋 매핑 →
 * sources.batchFiles.upload(멱등 키 필수) → 파일별 커밋 증거 확인.
 */
export function BatchFileFlow({
  initialDisplayName,
  onExit,
  onCancel,
  onComplete,
}: BatchFileFlowProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [displayName, setDisplayName] = useState(initialDisplayName);
  const [sourceNameInput, setSourceNameInput] = useState("");
  const [isSourceNameTouched, setIsSourceNameTouched] = useState(false);
  const [entries, setEntries] = useState<BatchFileEntry[]>([]);
  const uploadKeyRef = useRef<string | null>(null);
  const onboarding = useFoundryLiteProvidedSourceOnboarding();

  const sourceName = isSourceNameTouched
    ? sourceNameInput
    : sanitizeIdentifier(displayName);
  const sourceNameError =
    sourceName && !isValidIdentifier(sourceName)
      ? "영문/숫자/밑줄만 사용할 수 있습니다 (숫자로 시작 불가)."
      : null;
  const canConfigure =
    displayName.trim().length > 0 && sourceName.length > 0 && !sourceNameError;

  const hasInvalidDatasetRef = entries.some(
    (entry) => !isValidDatasetRef(entryDatasetRef(entry)),
  );
  const canRun = entries.length > 0 && !hasInvalidDatasetRef && canConfigure;

  const invalidateUploadKey = () => {
    uploadKeyRef.current = null;
  };

  const handleAddFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    invalidateUploadKey();
    const added = Array.from(files).map((file) => ({
      file,
      datasetRefInput: "",
      isDatasetRefTouched: false,
    }));
    setEntries((current) => [...current, ...added]);
  };

  const handleRemoveEntry = (index: number) => {
    invalidateUploadKey();
    setEntries((current) => current.filter((_, i) => i !== index));
  };

  const handleEntryDatasetRefChange = (index: number, value: string) => {
    invalidateUploadKey();
    setEntries((current) =>
      current.map((entry, i) =>
        i === index
          ? { ...entry, isDatasetRefTouched: true, datasetRefInput: value }
          : entry,
      ),
    );
  };

  const handleRunUpload = async () => {
    if (!canRun) return;
    if (!uploadKeyRef.current) {
      uploadKeyRef.current = idempotencyKey("source_batch_upload", sourceName);
    }
    const finalState = await onboarding.run({
      kind: "batch_file",
      payload: {
        sourceName,
        displayName: displayName.trim(),
        files: entries.map((entry) => ({
          fileName: entry.file.name,
          datasetRef: entryDatasetRef(entry),
          file: entry.file,
        })),
      },
      idempotencyKey: uploadKeyRef.current,
    });
    if (!finalState.error) setStepIndex(2);
  };

  const phaseItems = useMemo(
    () =>
      resolvePhaseItems(
        [
          {
            id: "source",
            label: "소스 등록",
            isDone: onboarding.source !== null,
            detail: onboarding.source?.sourceName,
          },
          {
            id: "commit",
            label: `파일 ${entries.length}개 업로드 & 데이터셋 커밋`,
            isDone: onboarding.commitResults.length > 0,
          },
          {
            id: "evidence",
            label: "운영 증거 연결",
            isDone:
              onboarding.phase === "operations_evidence" ||
              onboarding.phase === "ready_for_ontology",
            detail: onboarding.operationsPath ?? undefined,
          },
          {
            id: "ready",
            label: "온톨로지 연결 준비 완료",
            isDone: onboarding.phase === "ready_for_ontology",
          },
        ],
        {
          hasError: onboarding.error !== null,
          isRunning: onboarding.isRunning,
        },
      ),
    [onboarding, entries.length],
  );

  const handleBack = () => {
    if (stepIndex === 0) onExit();
    else setStepIndex(stepIndex - 1);
  };

  return (
    <WizardStepLayout
      title={displayName.trim() || "이름 없는 소스"}
      subtitle={`배치 파일 업로드 · ${BATCH_STEPS[stepIndex].title}`}
      steps={BATCH_STEPS}
      activeIndex={stepIndex}
      onBack={handleBack}
      onCancel={onCancel}
    >
      {stepIndex === 0 ? (
        <div className="space-y-4">
          <div>
            <h2 className="text-base font-semibold">소스 구성</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              여러 파일을 한 번에 받아 파일별 데이터셋으로 커밋하는 배치
              소스입니다.
            </p>
          </div>
          <WizardField
            label="표시 이름"
            helper="목록에 보여질 소스 이름입니다."
          >
            <Input
              value={displayName}
              onChange={(event) => {
                setDisplayName(event.target.value);
                invalidateUploadKey();
              }}
              placeholder="예: 월말 정산 배치 파일"
              className="h-8 text-xs"
            />
          </WizardField>
          <WizardField
            label="소스 이름"
            helper="시스템 식별자입니다. 영문 소문자/숫자/밑줄만 허용됩니다."
            error={sourceNameError}
          >
            <Input
              value={sourceName}
              onChange={(event) => {
                setIsSourceNameTouched(true);
                setSourceNameInput(
                  sanitizeIdentifier(event.target.value) || event.target.value,
                );
                invalidateUploadKey();
              }}
              placeholder="monthly_batch_files"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardStepFooter
            right={
              <Button
                size="sm"
                disabled={!canConfigure}
                onClick={() => setStepIndex(1)}
              >
                다음
              </Button>
            }
          />
        </div>
      ) : null}
      {stepIndex === 1 ? (
        <div className="space-y-4">
          <div>
            <h2 className="text-base font-semibold">파일 & 대상 데이터셋</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              파일마다 커밋될 데이터셋 참조(namespace.name)를 지정합니다.
            </p>
          </div>
          <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded border border-dashed p-6 text-center transition-colors hover:border-primary/50 hover:bg-accent/30">
            <FileUp className="size-6 text-muted-foreground/70" />
            <span className="text-[13px] font-medium">파일 추가</span>
            <span className="text-xs text-muted-foreground">
              클릭해서 업로드할 .csv 파일들을 선택하세요 (다중 선택 가능).
            </span>
            <input
              type="file"
              accept=".csv,text/csv"
              multiple
              className="hidden"
              onChange={(event) => {
                handleAddFiles(event.target.files);
                event.target.value = "";
              }}
            />
          </label>
          {entries.length > 0 ? (
            <div className="rounded border bg-card">
              <div className="section-label border-b px-3 py-2">
                업로드 파일 {entries.length}개
              </div>
              <div className="divide-y divide-border/60">
                {entries.map((entry, index) => {
                  const datasetRef = entryDatasetRef(entry);
                  const hasError = !isValidDatasetRef(datasetRef);
                  return (
                    <div
                      key={`${entry.file.name}-${index}`}
                      className="flex items-center gap-3 px-3 py-2"
                    >
                      <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
                        {entry.file.name}
                        <span className="ml-2 text-muted-foreground">
                          {entry.file.size.toLocaleString()} bytes
                        </span>
                      </span>
                      <Input
                        value={datasetRef}
                        onChange={(event) =>
                          handleEntryDatasetRefChange(index, event.target.value)
                        }
                        placeholder="demo.orders_raw"
                        aria-label={`${entry.file.name} 대상 데이터셋`}
                        className={
                          hasError
                            ? "h-8 w-64 border-destructive font-mono text-xs"
                            : "h-8 w-64 font-mono text-xs"
                        }
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-6 shrink-0"
                        onClick={() => handleRemoveEntry(index)}
                        aria-label={`${entry.file.name} 제거`}
                      >
                        <X className="size-3.5" />
                      </Button>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}
          {onboarding.isRunning || onboarding.error || onboarding.source ? (
            <div className="rounded border bg-card p-3">
              <div className="section-label mb-2">실행 진행 증거</div>
              <PhaseTimeline items={phaseItems} />
            </div>
          ) : null}
          {onboarding.error ? <ErrorState error={onboarding.error} /> : null}
          <WizardStepFooter
            left={
              uploadKeyRef.current ? (
                <span className="font-mono text-[11px] text-muted-foreground">
                  idempotency-key={uploadKeyRef.current}
                </span>
              ) : null
            }
            right={
              <Button
                size="sm"
                disabled={!canRun || onboarding.isRunning}
                onClick={() => void handleRunUpload()}
              >
                {onboarding.error ? (
                  <>
                    <RotateCw className="size-3.5" /> 같은 키로 다시 시도
                  </>
                ) : (
                  <>
                    <Play className="size-3.5" /> 배치 업로드 & 커밋 실행
                  </>
                )}
              </Button>
            }
          />
        </div>
      ) : null}
      {stepIndex === 2 ? (
        <BatchEvidenceStep
          onboarding={onboarding}
          uploadKey={uploadKeyRef.current}
          phaseItems={phaseItems}
          onComplete={onComplete}
        />
      ) : null}
    </WizardStepLayout>
  );
}

function BatchEvidenceStep({
  onboarding,
  uploadKey,
  phaseItems,
  onComplete,
}: {
  onboarding: ReturnType<typeof useFoundryLiteProvidedSourceOnboarding>;
  uploadKey: string | null;
  phaseItems: ReturnType<typeof resolvePhaseItems>;
  onComplete: (completion: WizardCompletion) => void;
}) {
  const operationsHref = toOperationsHref(onboarding.operationsPath);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">배치 커밋 증거 확인</h2>
        <StatusPill intent="success">커밋 완료</StatusPill>
      </div>
      <div className="rounded border bg-card p-3">
        <div className="section-label mb-2">실행 타임라인</div>
        <PhaseTimeline items={phaseItems} />
      </div>
      <EvidenceList title="커밋 증거">
        <EvidenceRow label="request id" value={onboarding.requestId} />
        <EvidenceRow label="idempotency key" value={uploadKey} />
        <EvidenceRow label="소스" value={onboarding.source?.sourceName} />
        <EvidenceRow label="operations" value={onboarding.operationsPath} />
      </EvidenceList>
      <div className="rounded border bg-card">
        <div className="section-label border-b px-3 py-2">
          파일별 커밋 결과 {onboarding.commitResults.length}건
        </div>
        <div className="divide-y divide-border/60">
          {onboarding.commitResults.map((commit, index) => {
            const record = commit as Record<string, unknown>;
            const datasetRef = readTextField(record, "datasetRef");
            const datasetHref = toDatasetHref(datasetRef);
            return (
              <div
                key={readTextField(record, "versionId") ?? index}
                className="flex items-center gap-3 px-3 py-2"
              >
                {datasetHref ? (
                  <Link
                    to={datasetHref}
                    className="min-w-0 flex-1 truncate font-mono text-[11px] text-primary hover:underline"
                  >
                    {datasetRef}
                  </Link>
                ) : (
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
                    {datasetRef ?? "—"}
                  </span>
                )}
                <span className="font-mono text-[11px] text-muted-foreground">
                  {readNumberField(record, "rowCount") ?? "—"} rows · v
                  {readNumberField(record, "versionNumber") ?? "?"}
                </span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {operationsHref ? (
          <Button asChild variant="outline" size="sm">
            <Link to={operationsHref}>
              <ExternalLink className="size-3.5" /> 운영 증거 보기
            </Link>
          </Button>
        ) : null}
        <Button
          size="sm"
          onClick={() =>
            onComplete({
              sourceName: onboarding.source?.sourceName ?? null,
              syncName: null,
            })
          }
        >
          <Table2 className="size-3.5" /> 완료 보기
        </Button>
      </div>
    </div>
  );
}
