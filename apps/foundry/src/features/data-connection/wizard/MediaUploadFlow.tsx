import type { MediaSet } from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteProvidedSourceOnboarding,
} from "@foundry-lite/sdk/react";
import { FileUp, Play, RotateCw } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import {
  isValidIdentifier,
  readTextField,
  sanitizeIdentifier,
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

const MEDIA_STEPS = [
  { id: "configure", title: "소스 구성" },
  { id: "upload", title: "업로드 & 실행" },
  { id: "evidence", title: "증거 확인" },
] as const;

const SCHEMA_TYPES = [
  { value: "document", label: "document (문서)" },
  { value: "imagery", label: "imagery (이미지)" },
] as const;

interface MediaFormState {
  displayName: string;
  sourceNameInput: string;
  isSourceNameTouched: boolean;
  mediaSetMode: "create" | "existing";
  mediaSetNamespace: string;
  mediaSetNameInput: string;
  isMediaSetNameTouched: boolean;
  existingMediaSetId: string;
  schemaType: string;
}

interface MediaUploadFlowProps {
  initialDisplayName: string;
  onExit: () => void;
  onCancel: () => void;
  onComplete: (completion: WizardCompletion) => void;
}

function fileFormat(file: File): string {
  const ext = file.name.split(".").pop();
  return ext && ext !== file.name ? ext.toLowerCase() : "bin";
}

/**
 * 미디어 업로드 온보딩 flow: 미디어셋 준비(생성 또는 기존 지정) → 파일 업로드
 * (sources.media.uploadAndCommit, 멱등 키 필수) → 미디어 커밋 증거 확인.
 */
export function MediaUploadFlow({
  initialDisplayName,
  onExit,
  onCancel,
  onComplete,
}: MediaUploadFlowProps) {
  const client = useFoundryLiteClient();
  const [stepIndex, setStepIndex] = useState(0);
  const [form, setForm] = useState<MediaFormState>({
    displayName: initialDisplayName,
    sourceNameInput: "",
    isSourceNameTouched: false,
    mediaSetMode: "create",
    mediaSetNamespace: "demo",
    mediaSetNameInput: "",
    isMediaSetNameTouched: false,
    existingMediaSetId: "",
    schemaType: "document",
  });
  const [file, setFile] = useState<File | null>(null);
  const [logicalPathInput, setLogicalPathInput] = useState("");
  const [isLogicalPathTouched, setIsLogicalPathTouched] = useState(false);
  const [setupError, setSetupError] = useState<unknown>(null);
  const createdMediaSetRef = useRef<MediaSet | null>(null);
  const uploadKeyRef = useRef<string | null>(null);
  const onboarding = useFoundryLiteProvidedSourceOnboarding();

  const updateForm = (patch: Partial<MediaFormState>) => {
    uploadKeyRef.current = null;
    setForm((current) => ({ ...current, ...patch }));
  };

  const sourceName = form.isSourceNameTouched
    ? form.sourceNameInput
    : sanitizeIdentifier(form.displayName);
  const mediaSetName = form.isMediaSetNameTouched
    ? form.mediaSetNameInput
    : sourceName
      ? `${sourceName}_media`
      : "";
  const logicalPath = isLogicalPathTouched
    ? logicalPathInput
    : file
      ? `uploads/${file.name}`
      : "";

  const sourceNameError =
    sourceName && !isValidIdentifier(sourceName)
      ? "영문/숫자/밑줄만 사용할 수 있습니다 (숫자로 시작 불가)."
      : null;
  const isMediaSetValid =
    form.mediaSetMode === "create"
      ? form.mediaSetNamespace.trim().length > 0 && mediaSetName.length > 0
      : form.existingMediaSetId.trim().length > 0;
  const canConfigure =
    form.displayName.trim().length > 0 &&
    sourceName.length > 0 &&
    !sourceNameError &&
    isMediaSetValid;
  const canRun = canConfigure && file !== null && logicalPath.trim().length > 0;

  const resolveMediaSetId = async (): Promise<string> => {
    if (form.mediaSetMode === "existing") {
      return form.existingMediaSetId.trim();
    }
    if (createdMediaSetRef.current) {
      return createdMediaSetRef.current.media_set_id;
    }
    if (!file) throw new Error("업로드할 파일을 먼저 선택하세요.");
    const format = fileFormat(file);
    const mediaSet = await client.media.sets.create({
      namespace: form.mediaSetNamespace.trim(),
      name: mediaSetName,
      schemaType: form.schemaType,
      primaryFormat: format,
      allowedInputFormats: [format],
      classification: "internal",
    });
    createdMediaSetRef.current = mediaSet;
    return mediaSet.media_set_id;
  };

  const handleRunUpload = async () => {
    if (!file || !canRun) return;
    setSetupError(null);
    let mediaSetId: string;
    try {
      mediaSetId = await resolveMediaSetId();
    } catch (error) {
      setSetupError(error);
      return;
    }
    if (!uploadKeyRef.current) {
      uploadKeyRef.current = idempotencyKey("source_media_upload", sourceName);
    }
    const finalState = await onboarding.run({
      kind: "media_upload",
      payload: {
        sourceName,
        displayName: form.displayName.trim(),
        mediaSetId,
        logicalPath: logicalPath.trim(),
        file,
        fileName: file.name,
        schemaType: form.schemaType,
        format: fileFormat(file),
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
            id: "media_set",
            label: "미디어셋 준비",
            isDone:
              createdMediaSetRef.current !== null ||
              form.mediaSetMode === "existing",
            detail:
              createdMediaSetRef.current?.media_set_id ??
              (form.mediaSetMode === "existing"
                ? form.existingMediaSetId
                : undefined),
          },
          {
            id: "source",
            label: "소스 등록",
            isDone: onboarding.source !== null,
            detail: onboarding.source?.sourceName,
          },
          {
            id: "commit",
            label: "미디어 업로드 & 커밋",
            isDone: onboarding.mediaCommitResult !== null,
            detail:
              readTextField(onboarding.mediaCommitResult, "mediaItemId") ??
              undefined,
          },
          {
            id: "ready",
            label: "온톨로지 연결 준비 완료",
            isDone: onboarding.phase === "ready_for_ontology",
          },
        ],
        {
          hasError: onboarding.error !== null || setupError !== null,
          isRunning: onboarding.isRunning,
        },
      ),
    [onboarding, form.mediaSetMode, form.existingMediaSetId, setupError],
  );

  const handleBack = () => {
    if (stepIndex === 0) onExit();
    else setStepIndex(stepIndex - 1);
  };

  return (
    <WizardStepLayout
      title={form.displayName.trim() || "이름 없는 소스"}
      subtitle={`미디어 업로드 · ${MEDIA_STEPS[stepIndex].title}`}
      steps={MEDIA_STEPS}
      activeIndex={stepIndex}
      onBack={handleBack}
      onCancel={onCancel}
    >
      {stepIndex === 0 ? (
        <div className="space-y-4">
          <div>
            <h2 className="text-base font-semibold">소스 구성</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              이미지/문서 파일을 미디어셋으로 커밋하는 업로드 소스입니다.
            </p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <WizardField
              label="표시 이름"
              helper="목록에 보여질 소스 이름입니다."
            >
              <Input
                value={form.displayName}
                onChange={(event) =>
                  updateForm({ displayName: event.target.value })
                }
                placeholder="예: 제품 이미지 업로드"
                className="h-8 text-xs"
              />
            </WizardField>
            <WizardField label="소스 이름" error={sourceNameError}>
              <Input
                value={sourceName}
                onChange={(event) =>
                  updateForm({
                    isSourceNameTouched: true,
                    sourceNameInput:
                      sanitizeIdentifier(event.target.value) ||
                      event.target.value,
                  })
                }
                placeholder="product_images"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
          </div>
          <WizardField
            label="미디어셋"
            helper="업로드 파일이 커밋될 미디어셋을 새로 만들거나 기존 ID를 지정합니다."
          >
            <Select
              value={form.mediaSetMode}
              onValueChange={(value) =>
                updateForm({ mediaSetMode: value as "create" | "existing" })
              }
            >
              <SelectTrigger size="sm" className="w-64 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="create">새 미디어셋 생성</SelectItem>
                <SelectItem value="existing">기존 미디어셋 사용</SelectItem>
              </SelectContent>
            </Select>
          </WizardField>
          {form.mediaSetMode === "create" ? (
            <div className="grid gap-4 md:grid-cols-2">
              <WizardField label="미디어셋 네임스페이스">
                <Input
                  value={form.mediaSetNamespace}
                  onChange={(event) =>
                    updateForm({ mediaSetNamespace: event.target.value })
                  }
                  placeholder="demo"
                  className="h-8 font-mono text-xs"
                />
              </WizardField>
              <WizardField label="미디어셋 이름">
                <Input
                  value={mediaSetName}
                  onChange={(event) =>
                    updateForm({
                      isMediaSetNameTouched: true,
                      mediaSetNameInput: event.target.value,
                    })
                  }
                  placeholder="product_images_media"
                  className="h-8 font-mono text-xs"
                />
              </WizardField>
            </div>
          ) : (
            <WizardField
              label="미디어셋 ID"
              helper="기존 미디어셋의 ID(mset_...)를 입력하세요."
            >
              <Input
                value={form.existingMediaSetId}
                onChange={(event) =>
                  updateForm({ existingMediaSetId: event.target.value })
                }
                placeholder="mset_0123456789abcdef"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
          )}
          <WizardField
            label="스키마 타입"
            helper="미디어셋 스키마 타입과 업로드 파일 타입이 일치해야 합니다."
          >
            <Select
              value={form.schemaType}
              onValueChange={(value) => updateForm({ schemaType: value })}
            >
              <SelectTrigger size="sm" className="w-64 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SCHEMA_TYPES.map((type) => (
                  <SelectItem key={type.value} value={type.value}>
                    {type.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
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
            <h2 className="text-base font-semibold">미디어 파일 업로드</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              업로드한 파일은 미디어셋 트랜잭션으로 커밋되고 버전 증거가
              남습니다.
            </p>
          </div>
          <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded border border-dashed p-8 text-center transition-colors hover:border-primary/50 hover:bg-accent/30">
            <FileUp className="size-6 text-muted-foreground/70" />
            {file ? (
              <>
                <span className="text-[13px] font-medium">{file.name}</span>
                <span className="font-mono text-[11px] text-muted-foreground">
                  {file.size.toLocaleString()} bytes · format={fileFormat(file)}
                </span>
              </>
            ) : (
              <>
                <span className="text-[13px] font-medium">
                  미디어 파일 선택
                </span>
                <span className="text-xs text-muted-foreground">
                  클릭해서 업로드할 파일을 선택하세요.
                </span>
              </>
            )}
            <input
              type="file"
              className="hidden"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                uploadKeyRef.current = null;
              }}
            />
          </label>
          <WizardField
            label="논리 경로"
            helper="미디어셋 안에서 파일을 식별하는 경로입니다."
          >
            <Input
              value={logicalPath}
              onChange={(event) => {
                setIsLogicalPathTouched(true);
                setLogicalPathInput(event.target.value);
                uploadKeyRef.current = null;
              }}
              placeholder="uploads/product_001.png"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          {onboarding.isRunning || onboarding.error || onboarding.source ? (
            <div className="rounded border bg-card p-3">
              <div className="section-label mb-2">실행 진행 증거</div>
              <PhaseTimeline items={phaseItems} />
            </div>
          ) : null}
          {setupError ? <ErrorState error={setupError} /> : null}
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
                {onboarding.error || setupError ? (
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
        <MediaEvidenceStep
          onboarding={onboarding}
          uploadKey={uploadKeyRef.current}
          phaseItems={phaseItems}
          onComplete={onComplete}
        />
      ) : null}
    </WizardStepLayout>
  );
}

function MediaEvidenceStep({
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
  const commit = onboarding.mediaCommitResult;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">미디어 커밋 증거 확인</h2>
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
        <EvidenceRow label="미디어셋" value={onboarding.mediaSetId} />
        <EvidenceRow
          label="논리 경로"
          value={readTextField(onboarding.source?.configSummary, "logicalPath")}
        />
        <EvidenceRow
          label="media transaction"
          value={readTextField(commit, "mediaTransactionId")}
        />
        <EvidenceRow
          label="media item"
          value={readTextField(commit, "mediaItemId")}
        />
        <EvidenceRow
          label="item version"
          value={readTextField(commit, "mediaItemVersionId")}
        />
        <EvidenceRow
          label="content hash"
          value={readTextField(onboarding.source?.configSummary, "contentHash")}
        />
      </EvidenceList>
      <div className="flex flex-wrap items-center gap-2">
        <Button
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
