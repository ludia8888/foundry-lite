import { idempotencyKey } from "@foundry-lite/sdk";
import { useFoundryLiteProvidedSourceOnboarding } from "@foundry-lite/sdk/react";
import { ExternalLink, Play, RotateCw, Table2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

import {
  isValidDatasetRef,
  isValidIdentifier,
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

const DEBEZIUM_STEPS = [
  { id: "configure", title: "스트림 구성" },
  { id: "run", title: "실행 & 증거" },
] as const;

interface DebeziumFormState {
  displayName: string;
  sourceNameInput: string;
  isSourceNameTouched: boolean;
  datasetRefInput: string;
  isDatasetRefTouched: boolean;
  streamNameInput: string;
  isStreamNameTouched: boolean;
  topic: string;
  consumerGroup: string;
  primaryKeyText: string;
  shouldStartSync: boolean;
}

interface DebeziumKeys {
  create: string;
  startSync: string;
}

interface DebeziumCdcFlowProps {
  initialDisplayName: string;
  onExit: () => void;
  onCancel: () => void;
  onComplete: (completion: WizardCompletion) => void;
}

/**
 * Debezium CDC 온보딩 flow: 스트림/토픽 구성 →
 * sources.cdc.debezium.create(+선택 startSync, 멱등 키 필수) → 실행 증거 확인.
 */
export function DebeziumCdcFlow({
  initialDisplayName,
  onExit,
  onCancel,
  onComplete,
}: DebeziumCdcFlowProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [form, setForm] = useState<DebeziumFormState>({
    displayName: initialDisplayName,
    sourceNameInput: "",
    isSourceNameTouched: false,
    datasetRefInput: "",
    isDatasetRefTouched: false,
    streamNameInput: "",
    isStreamNameTouched: false,
    topic: "",
    consumerGroup: "",
    primaryKeyText: "order_id",
    shouldStartSync: true,
  });
  const keysRef = useRef<DebeziumKeys | null>(null);
  const onboarding = useFoundryLiteProvidedSourceOnboarding();

  const updateForm = (patch: Partial<DebeziumFormState>) => {
    keysRef.current = null;
    setForm((current) => ({ ...current, ...patch }));
  };

  const sourceName = form.isSourceNameTouched
    ? form.sourceNameInput
    : sanitizeIdentifier(form.displayName);
  const datasetRef = form.isDatasetRefTouched
    ? form.datasetRefInput
    : sourceName
      ? `demo.${sourceName}_cdc`
      : "";
  const streamName = form.isStreamNameTouched
    ? form.streamNameInput
    : sourceName
      ? `${sourceName}_stream`
      : "";

  const sourceNameError =
    sourceName && !isValidIdentifier(sourceName)
      ? "영문/숫자/밑줄만 사용할 수 있습니다 (숫자로 시작 불가)."
      : null;
  const datasetRefError =
    datasetRef && !isValidDatasetRef(datasetRef)
      ? "namespace.name 형식이어야 합니다 (예: demo.orders_cdc)."
      : null;
  const canConfigure =
    form.displayName.trim().length > 0 &&
    sourceName.length > 0 &&
    !sourceNameError &&
    datasetRef.length > 0 &&
    !datasetRefError &&
    streamName.trim().length > 0 &&
    form.topic.trim().length > 0 &&
    form.primaryKeyText
      .split(",")
      .some((column) => column.trim().length > 0);

  const handleRun = async () => {
    const keys = keysRef.current ?? {
      create: idempotencyKey("source_debezium", sourceName),
      startSync: idempotencyKey("source_debezium_sync", sourceName),
    };
    keysRef.current = keys;
    const primaryKey = form.primaryKeyText
      .split(",")
      .map((column) => column.trim())
      .filter(Boolean);
    await onboarding.run({
      kind: "debezium_cdc",
      payload: {
        sourceName,
        displayName: form.displayName.trim(),
        datasetRef,
        streamName: streamName.trim(),
        topic: form.topic.trim(),
        ...(form.consumerGroup.trim()
          ? { consumerGroup: form.consumerGroup.trim() }
          : {}),
        primaryKey,
      },
      createIdempotencyKey: keys.create,
      ...(form.shouldStartSync
        ? { startSync: {}, startSyncIdempotencyKey: keys.startSync }
        : {}),
    });
  };

  const phaseItems = useMemo(
    () =>
      resolvePhaseItems(
        [
          {
            id: "source",
            label: "CDC 소스 생성",
            isDone: onboarding.source !== null,
            detail: onboarding.source?.sourceName,
          },
          {
            id: "sync",
            label: "CDC sync 시작 (이벤트 소비)",
            isDone: onboarding.testResult !== null,
            isSkipped: !form.shouldStartSync,
            detail: readTextField(onboarding.testResult, "status") ?? undefined,
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
    [onboarding, form.shouldStartSync],
  );

  const handleBack = () => {
    if (stepIndex === 0) onExit();
    else setStepIndex(stepIndex - 1);
  };

  return (
    <WizardStepLayout
      title={form.displayName.trim() || "이름 없는 소스"}
      subtitle={`Debezium CDC · ${DEBEZIUM_STEPS[stepIndex].title}`}
      steps={DEBEZIUM_STEPS}
      activeIndex={stepIndex}
      onBack={handleBack}
      onCancel={onCancel}
    >
      {stepIndex === 0 ? (
        <div className="space-y-4">
          <div>
            <h2 className="text-base font-semibold">CDC 스트림 구성</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Debezium CDC envelope 토픽을 구독해 변경 이벤트를 데이터셋으로
              적재합니다.
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
                placeholder="예: 주문 테이블 CDC"
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
                placeholder="orders_cdc"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
          </div>
          <WizardField
            label="대상 데이터셋"
            helper="CDC 이벤트가 커밋될 데이터셋 참조입니다."
            error={datasetRefError}
          >
            <Input
              value={datasetRef}
              onChange={(event) =>
                updateForm({
                  isDatasetRefTouched: true,
                  datasetRefInput: event.target.value,
                })
              }
              placeholder="demo.orders_cdc"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <div className="grid gap-4 md:grid-cols-2">
            <WizardField
              label="스트림 이름"
              helper="CDC 스트림의 논리 이름입니다."
            >
              <Input
                value={streamName}
                onChange={(event) =>
                  updateForm({
                    isStreamNameTouched: true,
                    streamNameInput: event.target.value,
                  })
                }
                placeholder="orders_stream"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
            <WizardField
              label="토픽"
              helper="Debezium이 발행하는 Kafka 토픽 이름입니다."
            >
              <Input
                value={form.topic}
                onChange={(event) => updateForm({ topic: event.target.value })}
                placeholder="dbserver1.public.orders"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
          </div>
          <WizardField
            label="컨슈머 그룹 (선택)"
            helper="비우면 기본값 foundry-lite-cdc를 사용합니다."
          >
            <Input
              value={form.consumerGroup}
              onChange={(event) =>
                updateForm({ consumerGroup: event.target.value })
              }
              placeholder="foundry-lite-cdc"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardField
            label="CDC primary key"
            helper="객체 id를 안정적으로 만들 컬럼입니다. 여러 개면 쉼표로 구분합니다."
          >
            <Input
              value={form.primaryKeyText}
              onChange={(event) =>
                updateForm({ primaryKeyText: event.target.value })
              }
              placeholder="order_id"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <label className="flex items-center gap-2 text-xs">
            <Checkbox
              checked={form.shouldStartSync}
              onCheckedChange={(checked) =>
                updateForm({ shouldStartSync: checked === true })
              }
            />
            생성 직후 CDC sync를 시작해 대기 중인 이벤트를 소비합니다
          </label>
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
        <DebeziumRunStep
          onboarding={onboarding}
          keys={keysRef.current}
          phaseItems={phaseItems}
          shouldStartSync={form.shouldStartSync}
          onRun={() => void handleRun()}
          onComplete={onComplete}
        />
      ) : null}
    </WizardStepLayout>
  );
}

function DebeziumRunStep({
  onboarding,
  keys,
  phaseItems,
  shouldStartSync,
  onRun,
  onComplete,
}: {
  onboarding: ReturnType<typeof useFoundryLiteProvidedSourceOnboarding>;
  keys: DebeziumKeys | null;
  phaseItems: ReturnType<typeof resolvePhaseItems>;
  shouldStartSync: boolean;
  onRun: () => void;
  onComplete: (completion: WizardCompletion) => void;
}) {
  const isReady = onboarding.phase === "ready_for_ontology";
  const hasStarted =
    onboarding.isRunning ||
    onboarding.source !== null ||
    onboarding.error !== null;
  const summary = onboarding.source?.configSummary ?? null;
  const testStatus = readTextField(onboarding.testResult, "status");
  const datasetHref = toDatasetHref(onboarding.source?.targetDatasetRef);
  const operationsHref = toOperationsHref(onboarding.operationsPath);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">실행 & 증거</h2>
        {isReady ? <StatusPill intent="success">완료</StatusPill> : null}
        {onboarding.isRunning ? (
          <StatusPill intent="info">실행 중</StatusPill>
        ) : null}
      </div>
      {hasStarted ? (
        <div className="rounded border bg-card p-3">
          <div className="section-label mb-2">실행 진행 증거</div>
          <PhaseTimeline items={phaseItems} />
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          아래 버튼을 누르면 CDC 소스 생성
          {shouldStartSync ? "과 첫 sync 시작이" : "이"} 한 번에 실행되고 각
          단계 증거가 기록됩니다.
        </p>
      )}
      {onboarding.error ? <ErrorState error={onboarding.error} /> : null}
      {isReady && onboarding.source ? (
        <EvidenceList title="생성 결과 증거">
          <EvidenceRow label="request id" value={onboarding.requestId} />
          <EvidenceRow label="create 멱등 키" value={keys?.create} />
          <EvidenceRow
            label="sync 멱등 키"
            value={shouldStartSync ? keys?.startSync : null}
          />
          <EvidenceRow label="소스" value={onboarding.source.sourceName} />
          <EvidenceRow
            label="스트림/토픽"
            value={`${readTextField(summary, "streamName") ?? "—"} · ${
              readTextField(summary, "topic") ?? "—"
            }`}
          />
          <EvidenceRow
            label="컨슈머 그룹"
            value={readTextField(summary, "consumerGroup")}
          />
          <EvidenceRow
            label="CDC primary key"
            value={
              Array.isArray(summary?.primaryKey)
                ? summary.primaryKey.join(", ")
                : null
            }
          />
          <EvidenceRow
            label="대상 데이터셋"
            value={onboarding.source.targetDatasetRef}
          />
          <EvidenceRow label="sync 결과">
            {testStatus ? (
              <span className="flex items-center gap-1.5">
                <span className="truncate">{testStatus}</span>
                {testStatus === "no_events" ? (
                  <StatusPill intent="neutral">이벤트 없음</StatusPill>
                ) : null}
              </span>
            ) : (
              <span>—</span>
            )}
          </EvidenceRow>
          <EvidenceRow
            label="구성 지문"
            value={onboarding.source.configFingerprint}
          />
          <EvidenceRow label="operations" value={onboarding.operationsPath} />
        </EvidenceList>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {!isReady ? (
          <Button size="sm" disabled={onboarding.isRunning} onClick={onRun}>
            {onboarding.error ? (
              <>
                <RotateCw className="size-3.5" /> 같은 키로 다시 시도
              </>
            ) : (
              <>
                <Play className="size-3.5" /> CDC 소스 생성{" "}
                {shouldStartSync ? "& sync 시작" : ""}
              </>
            )}
          </Button>
        ) : null}
        {isReady && datasetHref ? (
          <Button asChild variant="outline" size="sm">
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
        {isReady ? (
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
        ) : null}
      </div>
      {keys && !isReady ? (
        <div className="font-mono text-[11px] text-muted-foreground">
          idempotency-key(create)={keys.create}
        </div>
      ) : null}
    </div>
  );
}
