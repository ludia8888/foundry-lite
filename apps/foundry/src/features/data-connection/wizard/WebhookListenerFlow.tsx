import { idempotencyKey } from "@foundry-lite/sdk";
import { useFoundryLiteProvidedSourceOnboarding } from "@foundry-lite/sdk/react";
import { Copy, Play, RotateCw, Table2, Webhook } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import {
  isValidDatasetRef,
  isValidIdentifier,
  readTextField,
  sanitizeIdentifier,
  toDatasetHref,
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

const WEBHOOK_STEPS = [
  { id: "configure", title: "리스너 구성" },
  { id: "endpoint", title: "생성 & 엔드포인트" },
] as const;

interface WebhookFormState {
  displayName: string;
  sourceNameInput: string;
  isSourceNameTouched: boolean;
  datasetRefInput: string;
  isDatasetRefTouched: boolean;
  connectorNameInput: string;
  isConnectorNameTouched: boolean;
  resourceName: string;
  signingSecretRefInput: string;
  isSigningSecretRefTouched: boolean;
}

interface WebhookListenerFlowProps {
  initialDisplayName: string;
  onExit: () => void;
  onCancel: () => void;
  onComplete: (completion: WizardCompletion) => void;
}

/**
 * 인바운드 웹훅 리스너 온보딩 flow: 리스너 구성 →
 * sources.webhookListeners.create(멱등 키 필수) → 인바운드 엔드포인트/헤더 증거 표시.
 */
export function WebhookListenerFlow({
  initialDisplayName,
  onExit,
  onCancel,
  onComplete,
}: WebhookListenerFlowProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [form, setForm] = useState<WebhookFormState>({
    displayName: initialDisplayName,
    sourceNameInput: "",
    isSourceNameTouched: false,
    datasetRefInput: "",
    isDatasetRefTouched: false,
    connectorNameInput: "",
    isConnectorNameTouched: false,
    resourceName: "events",
    signingSecretRefInput: "",
    isSigningSecretRefTouched: false,
  });
  const createKeyRef = useRef<string | null>(null);
  const onboarding = useFoundryLiteProvidedSourceOnboarding();

  const updateForm = (patch: Partial<WebhookFormState>) => {
    createKeyRef.current = null;
    setForm((current) => ({ ...current, ...patch }));
  };

  const sourceName = form.isSourceNameTouched
    ? form.sourceNameInput
    : sanitizeIdentifier(form.displayName);
  const datasetRef = form.isDatasetRefTouched
    ? form.datasetRefInput
    : sourceName
      ? `demo.${sourceName}_events`
      : "";
  const connectorName = form.isConnectorNameTouched
    ? form.connectorNameInput
    : sourceName
      ? `${sourceName}_connector`
      : "";
  const signingSecretRef = form.isSigningSecretRefTouched
    ? form.signingSecretRefInput
    : sourceName
      ? `source_${sourceName}_signing`
      : "";

  const sourceNameError =
    sourceName && !isValidIdentifier(sourceName)
      ? "영문/숫자/밑줄만 사용할 수 있습니다 (숫자로 시작 불가)."
      : null;
  const datasetRefError =
    datasetRef && !isValidDatasetRef(datasetRef)
      ? "namespace.name 형식이어야 합니다 (예: demo.webhook_events)."
      : null;
  const canCreate =
    form.displayName.trim().length > 0 &&
    sourceName.length > 0 &&
    !sourceNameError &&
    datasetRef.length > 0 &&
    !datasetRefError &&
    connectorName.trim().length > 0 &&
    form.resourceName.trim().length > 0 &&
    signingSecretRef.trim().length > 0;

  const handleCreate = async () => {
    if (!canCreate) return;
    if (!createKeyRef.current) {
      createKeyRef.current = idempotencyKey("source_webhook", sourceName);
    }
    await onboarding.run({
      kind: "webhook_listener",
      payload: {
        sourceName,
        displayName: form.displayName.trim(),
        datasetRef,
        connectorName: connectorName.trim(),
        resourceName: form.resourceName.trim(),
        signingSecretRef: signingSecretRef.trim(),
      },
      idempotencyKey: createKeyRef.current,
    });
  };

  const phaseItems = useMemo(
    () =>
      resolvePhaseItems(
        [
          {
            id: "listener",
            label: "웹훅 리스너 생성",
            isDone: onboarding.source !== null,
            detail: onboarding.source?.sourceName,
          },
          {
            id: "endpoint",
            label: "인바운드 엔드포인트 발급",
            isDone:
              readTextField(onboarding.source?.configSummary, "inboundUrl") !==
              null,
          },
          {
            id: "ready",
            label: "이벤트 수신 준비 완료",
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
      title={form.displayName.trim() || "이름 없는 소스"}
      subtitle={`인바운드 웹훅 · ${WEBHOOK_STEPS[stepIndex].title}`}
      steps={WEBHOOK_STEPS}
      activeIndex={stepIndex}
      onBack={handleBack}
      onCancel={onCancel}
    >
      {stepIndex === 0 ? (
        <div className="space-y-4">
          <div>
            <h2 className="text-base font-semibold">웹훅 리스너 구성</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              외부 시스템이 이벤트를 push할 수 있는 서명 검증 엔드포인트를
              만듭니다.
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
                placeholder="예: 주문 이벤트 웹훅"
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
                placeholder="orders_webhook"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
          </div>
          <WizardField
            label="대상 데이터셋"
            helper="수신한 이벤트가 append 커밋될 데이터셋 참조입니다."
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
              placeholder="demo.webhook_events"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <div className="grid gap-4 md:grid-cols-2">
            <WizardField
              label="커넥터 이름"
              helper="이벤트 출처를 식별하는 논리 커넥터 이름입니다."
            >
              <Input
                value={connectorName}
                onChange={(event) =>
                  updateForm({
                    isConnectorNameTouched: true,
                    connectorNameInput: event.target.value,
                  })
                }
                placeholder="orders_connector"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
            <WizardField
              label="리소스 이름"
              helper="커넥터 내 이벤트 리소스 이름입니다."
            >
              <Input
                value={form.resourceName}
                onChange={(event) =>
                  updateForm({ resourceName: event.target.value })
                }
                placeholder="events"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
          </div>
          <WizardField
            label="서명 secret 참조"
            helper="이벤트 서명(HMAC) 검증에 사용할 vault secret 이름입니다."
          >
            <Input
              value={signingSecretRef}
              onChange={(event) =>
                updateForm({
                  isSigningSecretRefTouched: true,
                  signingSecretRefInput: event.target.value,
                })
              }
              placeholder="source_orders_webhook_signing"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardStepFooter
            right={
              <Button
                size="sm"
                disabled={!canCreate}
                onClick={() => setStepIndex(1)}
              >
                다음
              </Button>
            }
          />
        </div>
      ) : null}
      {stepIndex === 1 ? (
        <WebhookEndpointStep
          onboarding={onboarding}
          createKey={createKeyRef.current}
          phaseItems={phaseItems}
          datasetRef={datasetRef}
          onCreate={() => void handleCreate()}
          onComplete={onComplete}
        />
      ) : null}
    </WizardStepLayout>
  );
}

function WebhookEndpointStep({
  onboarding,
  createKey,
  phaseItems,
  datasetRef,
  onCreate,
  onComplete,
}: {
  onboarding: ReturnType<typeof useFoundryLiteProvidedSourceOnboarding>;
  createKey: string | null;
  phaseItems: ReturnType<typeof resolvePhaseItems>;
  datasetRef: string;
  onCreate: () => void;
  onComplete: (completion: WizardCompletion) => void;
}) {
  const isReady = onboarding.phase === "ready_for_ontology";
  const hasStarted =
    onboarding.isRunning ||
    onboarding.source !== null ||
    onboarding.error !== null;
  const summary = onboarding.source?.configSummary ?? null;
  const inboundUrl = readTextField(summary, "inboundUrl");
  const requiredHeaders = Array.isArray(summary?.requiredHeaders)
    ? summary.requiredHeaders.filter(
        (header): header is string => typeof header === "string",
      )
    : [];
  const datasetHref = toDatasetHref(onboarding.source?.targetDatasetRef);

  const handleCopyUrl = async () => {
    if (!inboundUrl) return;
    try {
      await navigator.clipboard.writeText(inboundUrl);
      toast.success("엔드포인트 URL을 복사했습니다");
    } catch {
      toast.error("복사에 실패했습니다");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">리스너 생성 & 엔드포인트</h2>
        {isReady ? <StatusPill intent="success">생성 완료</StatusPill> : null}
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
          아래 버튼을 누르면 리스너가 생성되고{" "}
          <span className="font-mono">{datasetRef}</span> 데이터셋으로 이벤트를
          받을 인바운드 엔드포인트가 발급됩니다.
        </p>
      )}
      {onboarding.error ? <ErrorState error={onboarding.error} /> : null}
      {inboundUrl ? (
        <div className="rounded border bg-card">
          <div className="section-label flex items-center gap-1.5 border-b px-3 py-2">
            <Webhook className="size-3" /> 인바운드 엔드포인트
          </div>
          <div className="space-y-2 p-3">
            <div className="flex items-center gap-1.5">
              <span className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 font-mono text-[11px]">
                POST {inboundUrl}
              </span>
              <Button
                variant="ghost"
                size="icon"
                className="size-6 shrink-0"
                onClick={() => void handleCopyUrl()}
                aria-label="엔드포인트 URL 복사"
              >
                <Copy className="size-3" />
              </Button>
            </div>
            <div className="text-[11px] text-muted-foreground">
              필수 서명 헤더:{" "}
              {requiredHeaders.map((header) => (
                <span
                  key={header}
                  className="mr-1.5 rounded bg-muted px-1.5 py-0.5 font-mono"
                >
                  {header}
                </span>
              ))}
            </div>
          </div>
        </div>
      ) : null}
      {isReady && onboarding.source ? (
        <EvidenceList title="생성 결과 증거">
          <EvidenceRow label="request id" value={onboarding.requestId} />
          <EvidenceRow label="idempotency key" value={createKey} />
          <EvidenceRow label="소스" value={onboarding.source.sourceName} />
          <EvidenceRow
            label="대상 데이터셋"
            value={onboarding.source.targetDatasetRef}
          />
          <EvidenceRow
            label="커넥터/리소스"
            value={`${readTextField(summary, "connectorName") ?? "—"} / ${
              readTextField(summary, "resourceName") ?? "—"
            }`}
          />
          <EvidenceRow
            label="서명 secret"
            value={readTextField(summary, "signingSecretRef")}
          />
          <EvidenceRow
            label="구성 지문"
            value={onboarding.source.configFingerprint}
          />
        </EvidenceList>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {!isReady ? (
          <Button size="sm" disabled={onboarding.isRunning} onClick={onCreate}>
            {onboarding.error ? (
              <>
                <RotateCw className="size-3.5" /> 같은 키로 다시 시도
              </>
            ) : (
              <>
                <Play className="size-3.5" /> 리스너 생성
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
      {createKey && !isReady ? (
        <div className="font-mono text-[11px] text-muted-foreground">
          idempotency-key={createKey}
        </div>
      ) : null}
    </div>
  );
}
