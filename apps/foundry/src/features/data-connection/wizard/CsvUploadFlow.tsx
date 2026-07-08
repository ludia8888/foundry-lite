import { idempotencyKey } from "@foundry-lite/sdk";
import { useFoundryLiteProvidedSourceOnboarding } from "@foundry-lite/sdk/react";
import { ExternalLink, FileUp, Play, RotateCw, Table2 } from "lucide-react";
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
import {
  EvidenceList,
  EvidenceRow,
  PhaseTimeline,
  resolvePhaseItems,
} from "./WizardEvidence";
import { WizardField, WizardStepFooter } from "./WizardFields";
import { WizardStepLayout } from "./WizardStepLayout";

const CSV_STEPS = [
  { id: "configure", title: "소스 구성" },
  { id: "upload", title: "업로드 & 실행" },
  { id: "evidence", title: "증거 확인" },
] as const;

export interface WizardCompletion {
  sourceName: string | null;
  syncName: string | null;
}

interface CsvUploadFlowProps {
  initialDisplayName: string;
  onExit: () => void;
  onCancel: () => void;
  onComplete: (completion: WizardCompletion) => void;
}

/**
 * CSV 업로드 온보딩 flow: 소스 구성 → 파일 업로드(uploadCsv, idempotencyKey 필수)
 * → 커밋 증거(row count/version/operations link) 확인.
 */
export function CsvUploadFlow({
  initialDisplayName,
  onExit,
  onCancel,
  onComplete,
}: CsvUploadFlowProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [displayName, setDisplayName] = useState(initialDisplayName);
  const [sourceNameInput, setSourceNameInput] = useState("");
  const [isSourceNameTouched, setIsSourceNameTouched] = useState(false);
  const [datasetRefInput, setDatasetRefInput] = useState("");
  const [isDatasetRefTouched, setIsDatasetRefTouched] = useState(false);
  const [primaryKeyText, setPrimaryKeyText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [previewLines, setPreviewLines] = useState<string[]>([]);
  const uploadKeyRef = useRef<string | null>(null);
  const onboarding = useFoundryLiteProvidedSourceOnboarding();

  const sourceName = isSourceNameTouched
    ? sourceNameInput
    : sanitizeIdentifier(displayName);
  const datasetRef = isDatasetRefTouched
    ? datasetRefInput
    : sourceName
      ? `demo.${sourceName}`
      : "";

  const sourceNameError =
    sourceName && !isValidIdentifier(sourceName)
      ? "영문/숫자/밑줄만 사용할 수 있습니다 (숫자로 시작 불가)."
      : null;
  const datasetRefError =
    datasetRef && !isValidDatasetRef(datasetRef)
      ? "namespace.name 형식이어야 합니다 (예: demo.orders_raw)."
      : null;
  const canConfigure =
    displayName.trim().length > 0 &&
    sourceName.length > 0 &&
    !sourceNameError &&
    !datasetRefError &&
    datasetRef.length > 0;

  const invalidateUploadKey = () => {
    uploadKeyRef.current = null;
  };

  const handleSelectFile = (nextFile: File | null) => {
    setFile(nextFile);
    setPreviewLines([]);
    invalidateUploadKey();
    if (nextFile) {
      void nextFile.text().then((text) => {
        setPreviewLines(text.split(/\r?\n/).filter(Boolean).slice(0, 5));
      });
    }
  };

  const handleRunUpload = async () => {
    if (!file || !canConfigure) return;
    if (!uploadKeyRef.current) {
      uploadKeyRef.current = idempotencyKey("source_csv_upload", sourceName);
    }
    const primaryKey = primaryKeyText
      .split(",")
      .map((column) => column.trim())
      .filter(Boolean);
    const finalState = await onboarding.run({
      kind: "csv_upload",
      payload: {
        sourceName,
        displayName: displayName.trim(),
        datasetRef,
        file,
        fileName: file.name,
        ...(primaryKey.length > 0 ? { primaryKey } : {}),
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
            label: "파일 업로드 & 데이터셋 커밋",
            isDone: onboarding.commitResult !== null,
            detail: onboarding.datasetRef ?? undefined,
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
    [onboarding],
  );

  const handleBack = () => {
    if (stepIndex === 0) onExit();
    else setStepIndex(stepIndex - 1);
  };

  return (
    <WizardStepLayout
      title={displayName.trim() || "이름 없는 소스"}
      subtitle={`CSV 업로드 · ${CSV_STEPS[stepIndex].title}`}
      steps={CSV_STEPS}
      activeIndex={stepIndex}
      onBack={handleBack}
      onCancel={onCancel}
    >
      {stepIndex === 0 ? (
        <ConfigureStep
          displayName={displayName}
          onDisplayNameChange={(value) => {
            setDisplayName(value);
            invalidateUploadKey();
          }}
          sourceName={sourceName}
          sourceNameError={sourceNameError}
          onSourceNameChange={(value) => {
            setIsSourceNameTouched(true);
            setSourceNameInput(sanitizeIdentifier(value) || value);
            invalidateUploadKey();
          }}
          datasetRef={datasetRef}
          datasetRefError={datasetRefError}
          onDatasetRefChange={(value) => {
            setIsDatasetRefTouched(true);
            setDatasetRefInput(value);
            invalidateUploadKey();
          }}
          primaryKeyText={primaryKeyText}
          onPrimaryKeyChange={setPrimaryKeyText}
          canContinue={canConfigure}
          onContinue={() => setStepIndex(1)}
        />
      ) : null}
      {stepIndex === 1 ? (
        <div className="space-y-4">
          <div>
            <h2 className="text-base font-semibold">CSV 파일 업로드</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              업로드한 파일은 <span className="font-mono">{datasetRef}</span>{" "}
              데이터셋으로 커밋되고 run 증거가 남습니다.
            </p>
          </div>
          <FilePickerBox file={file} onSelect={handleSelectFile} />
          {previewLines.length > 0 ? (
            <div className="rounded border bg-card">
              <div className="section-label flex items-center gap-1.5 border-b px-3 py-2">
                <Table2 className="size-3" /> 파일 미리보기 (상위 5행)
              </div>
              <pre className="overflow-x-auto p-3 font-mono text-[11px] leading-5">
                {previewLines.join("\n")}
              </pre>
            </div>
          ) : null}
          {onboarding.isRunning || onboarding.error || onboarding.source ? (
            <div className="rounded border bg-card p-3">
              <div className="section-label mb-2">실행 진행 증거</div>
              <PhaseTimeline items={phaseItems} />
            </div>
          ) : null}
          {onboarding.error ? (
            <div className="space-y-2">
              <ErrorState error={onboarding.error} />
              <OperationsLinkRow operationsPath={onboarding.operationsPath} />
            </div>
          ) : null}
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
                disabled={!file || onboarding.isRunning}
                onClick={() => void handleRunUpload()}
              >
                {onboarding.error ? (
                  <>
                    <RotateCw className="size-3.5" /> 같은 키로 다시 시도
                  </>
                ) : (
                  <>
                    <Play className="size-3.5" /> 업로드 & 커밋 실행
                  </>
                )}
              </Button>
            }
          />
        </div>
      ) : null}
      {stepIndex === 2 ? (
        <CsvEvidenceStep
          onboarding={onboarding}
          uploadKey={uploadKeyRef.current}
          phaseItems={phaseItems}
          onComplete={onComplete}
        />
      ) : null}
    </WizardStepLayout>
  );
}

function ConfigureStep({
  displayName,
  onDisplayNameChange,
  sourceName,
  sourceNameError,
  onSourceNameChange,
  datasetRef,
  datasetRefError,
  onDatasetRefChange,
  primaryKeyText,
  onPrimaryKeyChange,
  canContinue,
  onContinue,
}: {
  displayName: string;
  onDisplayNameChange: (value: string) => void;
  sourceName: string;
  sourceNameError: string | null;
  onSourceNameChange: (value: string) => void;
  datasetRef: string;
  datasetRefError: string | null;
  onDatasetRefChange: (value: string) => void;
  primaryKeyText: string;
  onPrimaryKeyChange: (value: string) => void;
  canContinue: boolean;
  onContinue: () => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">소스 구성</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          CSV 파일을 받을 소스와 대상 데이터셋을 정의합니다.
        </p>
      </div>
      <WizardField label="표시 이름" helper="목록에 보여질 소스 이름입니다.">
        <Input
          value={displayName}
          onChange={(event) => onDisplayNameChange(event.target.value)}
          placeholder="예: 주문 원장 CSV"
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
          onChange={(event) => onSourceNameChange(event.target.value)}
          placeholder="orders_raw_csv"
          className="h-8 font-mono text-xs"
        />
      </WizardField>
      <WizardField
        label="대상 데이터셋"
        helper="namespace.name 형식으로 커밋될 데이터셋 참조입니다."
        error={datasetRefError}
      >
        <Input
          value={datasetRef}
          onChange={(event) => onDatasetRefChange(event.target.value)}
          placeholder="demo.orders_raw"
          className="h-8 font-mono text-xs"
        />
      </WizardField>
      <WizardField
        label="기본 키 (선택)"
        helper="쉼표로 구분한 기본 키 컬럼 목록입니다."
      >
        <Input
          value={primaryKeyText}
          onChange={(event) => onPrimaryKeyChange(event.target.value)}
          placeholder="order_id"
          className="h-8 font-mono text-xs"
        />
      </WizardField>
      <WizardStepFooter
        right={
          <Button size="sm" disabled={!canContinue} onClick={onContinue}>
            다음
          </Button>
        }
      />
    </div>
  );
}

function FilePickerBox({
  file,
  onSelect,
}: {
  file: File | null;
  onSelect: (file: File | null) => void;
}) {
  return (
    <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded border border-dashed p-8 text-center transition-colors hover:border-primary/50 hover:bg-accent/30">
      <FileUp className="size-6 text-muted-foreground/70" />
      {file ? (
        <>
          <span className="text-[13px] font-medium">{file.name}</span>
          <span className="font-mono text-[11px] text-muted-foreground">
            {file.size.toLocaleString()} bytes
          </span>
        </>
      ) : (
        <>
          <span className="text-[13px] font-medium">CSV 파일 선택</span>
          <span className="text-xs text-muted-foreground">
            클릭해서 업로드할 .csv 파일을 선택하세요.
          </span>
        </>
      )}
      <input
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={(event) => onSelect(event.target.files?.[0] ?? null)}
      />
    </label>
  );
}

function OperationsLinkRow({
  operationsPath,
}: {
  operationsPath: string | null;
}) {
  const href = toOperationsHref(operationsPath);
  if (!href) return null;
  return (
    <Button asChild variant="outline" size="sm">
      <Link to={href}>
        <ExternalLink className="size-3.5" /> 운영 증거 보기
      </Link>
    </Button>
  );
}

function CsvEvidenceStep({
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
  const commit = onboarding.commitResult;
  const rowCount = readNumberField(commit, "rowCount");
  const versionNumber = readNumberField(commit, "versionNumber");
  const versionId = readTextField(commit, "versionId");
  const datasetHref = toDatasetHref(onboarding.datasetRef);
  const operationsHref = toOperationsHref(onboarding.operationsPath);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">업로드 증거 확인</h2>
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
        <EvidenceRow label="데이터셋" value={onboarding.datasetRef} />
        <EvidenceRow label="row count" value={rowCount} />
        <EvidenceRow
          label="버전"
          value={versionId ? `v${versionNumber ?? "?"} · ${versionId}` : null}
        />
        <EvidenceRow
          label="schema hash"
          value={readTextField(commit, "schemaHash")}
        />
        <EvidenceRow
          label="transaction"
          value={readTextField(commit, "transactionId")}
        />
        <EvidenceRow label="operations" value={onboarding.operationsPath} />
      </EvidenceList>
      <div className="flex flex-wrap items-center gap-2">
        {datasetHref ? (
          <Button asChild size="sm">
            <Link to={datasetHref}>
              <Table2 className="size-3.5" /> 데이터셋 열기
            </Link>
          </Button>
        ) : null}
        {operationsHref ? (
          <Button asChild variant="outline" size="sm">
            <Link to={operationsHref}>
              <ExternalLink className="size-3.5" /> 운영 증거 보기
            </Link>
          </Button>
        ) : null}
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            onComplete({
              sourceName: onboarding.source?.sourceName ?? null,
              syncName: null,
            })
          }
        >
          완료 보기
        </Button>
      </div>
    </div>
  );
}
