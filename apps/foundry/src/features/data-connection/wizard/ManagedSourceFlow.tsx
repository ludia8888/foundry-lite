import type {
  ConnectorConnection,
  ConnectorResource,
  ConnectorResourceTestResult,
} from "@foundry-lite/sdk";
import type { SourceWizardInput } from "@foundry-lite/sdk/screen-recipes";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteProvidedSourceWizard,
} from "@foundry-lite/sdk/react";
import { ExternalLink, Play, RotateCw, Table2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import type { WizardCompletion } from "./CsvUploadFlow";
import type { WizardTemplate } from "./TemplatePickerStep";
import type { ConnectionMode } from "./ConnectionMethodStep";
import {
  isValidDatasetRef,
  isValidIdentifier,
  readTextField,
  sanitizeIdentifier,
  statusIntent,
  statusLabel,
  toDatasetHref,
  toOperationsHref,
} from "../source-model";
import { CredentialNetworkStep, SyncConfigStep } from "./ManagedSourceSteps";
import {
  EvidenceList,
  EvidenceRow,
  PhaseTimeline,
  resolvePhaseItems,
} from "./WizardEvidence";
import { WizardField } from "./WizardFields";
import { WizardStepLayout } from "./WizardStepLayout";

const MANAGED_STEPS = [
  { id: "credential", title: "자격 증명 & 네트워크" },
  { id: "sync", title: "동기화 설정" },
  { id: "run", title: "실행 & 증거" },
] as const;

/** 관리형 run data-plane이 실제로 실행 가능한 소스 타입. */
const RUNNABLE_SOURCE_TYPES = new Set(["rest_api", "postgres_jdbc"]);

const normalizeManagedSyncCapability = (capability: string): string =>
  capability === "batch_file" ? "batch" : capability;

function managedSyncCapabilities(capabilities: readonly string[]): string[] {
  return Array.from(new Set(capabilities.map(normalizeManagedSyncCapability)));
}

interface ManagedFormState {
  displayName: string;
  sourceNameInput: string;
  isSourceNameTouched: boolean;
  hasCredential: boolean;
  authScheme: string;
  secretValue: string;
  hasNetworkPolicy: boolean;
  allowedHostsText: string;
  capability: string;
  datasetRefInput: string;
  isDatasetRefTouched: boolean;
  syncMode: string;
  scheduleMode: string;
  everySecondsText: string;
  connectorName: string;
  resourceName: string;
  restBaseUrl: string;
  restResourcePath: string;
  restItemsPath: string;
  restNextCursorPath: string;
  restCursorQueryParam: string;
  restCursorKey: string;
  restSchemaColumnsText: string;
  restPrimaryKeyText: string;
  databaseUrlSecretRef: string;
  tableName: string;
  shouldStartRun: boolean;
}

interface WizardKeys {
  connectorConnection: string;
  connectorResource: string;
  credential: string;
  networkPolicy: string;
  sync: string;
  run: string;
}

interface RestConnectorSetupEvidence {
  connection: ConnectorConnection;
  resource: ConnectorResource;
  testResult: ConnectorResourceTestResult | null;
}

interface ManagedSourceFlowProps {
  template: WizardTemplate;
  initialDisplayName: string;
  connectionMode: ConnectionMode;
  /** 연결 방식 단계에서 선택/등록된 에이전트 (agent_proxy일 때). */
  agentId: string | null;
  onExit: () => void;
  onCancel: () => void;
  onComplete: (completion: WizardCompletion) => void;
}

/**
 * 관리형 소스 위저드: 연결 방식 → 자격 증명/네트워크 정책 → managed sync 생성 →
 * run 시작 → 운영 증거. createSourceWizardRecipe.run을 그대로 사용한다.
 */
export function ManagedSourceFlow({
  template,
  initialDisplayName,
  connectionMode,
  agentId,
  onExit,
  onCancel,
  onComplete,
}: ManagedSourceFlowProps) {
  const isRunSupported = RUNNABLE_SOURCE_TYPES.has(template.sourceType);
  const managedCapabilities = managedSyncCapabilities(template.capabilities);
  const client = useFoundryLiteClient();
  const [stepIndex, setStepIndex] = useState(0);
  const [form, setForm] = useState<ManagedFormState>({
    displayName: initialDisplayName,
    sourceNameInput: "",
    isSourceNameTouched: false,
    hasCredential: true,
    authScheme: "bearer",
    secretValue: "",
    hasNetworkPolicy: true,
    allowedHostsText: "",
    capability: managedCapabilities.includes("batch")
      ? "batch"
      : (managedCapabilities[0] ?? "batch"),
    datasetRefInput: "",
    isDatasetRefTouched: false,
    syncMode: "SNAPSHOT",
    scheduleMode: "manual",
    everySecondsText: "3600",
    connectorName: "",
    resourceName: "",
    restBaseUrl: "",
    restResourcePath: "",
    restItemsPath: "items",
    restNextCursorPath: "nextCursor",
    restCursorQueryParam: "cursor",
    restCursorKey: "cursor",
    restSchemaColumnsText: "",
    restPrimaryKeyText: "",
    databaseUrlSecretRef: "",
    tableName: "",
    shouldStartRun: isRunSupported,
  });
  const keysRef = useRef<WizardKeys | null>(null);
  const wizard = useFoundryLiteProvidedSourceWizard();
  const [restConnectorSetup, setRestConnectorSetup] =
    useState<RestConnectorSetupEvidence | null>(null);
  const [restConnectorError, setRestConnectorError] = useState<unknown>(null);
  const [isPreparingRestConnector, setIsPreparingRestConnector] =
    useState(false);

  const updateForm = (patch: Partial<ManagedFormState>) => {
    keysRef.current = null;
    setForm((current) => ({ ...current, ...patch }));
  };

  const sourceName = form.isSourceNameTouched
    ? form.sourceNameInput
    : sanitizeIdentifier(form.displayName);
  const datasetRef = form.isDatasetRefTouched
    ? form.datasetRefInput
    : sourceName
      ? `demo.${sourceName}_synced`
      : "";
  const syncName = sourceName ? `${sourceName}_sync` : "";
  const credentialName = sourceName ? `${sourceName}_cred` : "";
  const defaultCredentialSecretRef = credentialName
    ? `source_${credentialName}`
    : "";
  const databaseUrlSecretRef =
    form.databaseUrlSecretRef.trim() || defaultCredentialSecretRef;
  const policyName = sourceName ? `${sourceName}_policy` : "";

  const sourceNameError =
    sourceName && !isValidIdentifier(sourceName)
      ? "영문/숫자/밑줄만 사용할 수 있습니다 (숫자로 시작 불가)."
      : null;
  const datasetRefError =
    datasetRef && !isValidDatasetRef(datasetRef)
      ? "namespace.name 형식이어야 합니다."
      : null;

  const isNameValid =
    form.displayName.trim().length > 0 &&
    sourceName.length > 0 &&
    !sourceNameError;
  const isCredentialValid =
    !form.hasCredential || form.secretValue.trim().length > 0;
  const isTypeConfigValid = !form.shouldStartRun
    ? true
    : template.sourceType === "rest_api"
      ? form.connectorName.trim().length > 0 &&
        form.resourceName.trim().length > 0 &&
        form.restBaseUrl.trim().length > 0 &&
        form.restResourcePath.trim().length > 0
      : template.sourceType === "postgres_jdbc"
        ? databaseUrlSecretRef.length > 0 && form.tableName.trim().length > 0
        : true;
  const isScheduleValid =
    form.scheduleMode !== "scheduled" ||
    Number.parseInt(form.everySecondsText, 10) > 0;
  const isSyncValid =
    datasetRef.length > 0 &&
    !datasetRefError &&
    isScheduleValid &&
    isTypeConfigValid;

  const buildInput = (keys: WizardKeys): SourceWizardInput => {
    const allowedHosts = form.allowedHostsText
      .split(",")
      .map((host) => host.trim())
      .filter(Boolean);
    const configSummary: Record<string, unknown> = {
      ...(form.connectorName ? { connectorName: form.connectorName } : {}),
      ...(form.resourceName ? { resourceName: form.resourceName } : {}),
      ...(template.sourceType === "postgres_jdbc" && databaseUrlSecretRef
        ? { databaseUrlSecretRef }
        : {}),
      ...(form.tableName ? { tableName: form.tableName } : {}),
    };
    const schedule =
      form.scheduleMode === "scheduled"
        ? {
            mode: "interval",
            everySeconds: Number.parseInt(form.everySecondsText, 10),
          }
        : { mode: "manual" };
    return {
      sourceType: template.sourceType,
      ...(form.hasCredential
        ? {
            credential: {
              payload: {
                credentialName,
                displayName: `${form.displayName.trim()} 자격 증명`,
                kind: template.sourceType,
                authScheme: form.authScheme,
                secretValue: form.secretValue,
              },
              idempotencyKey: keys.credential,
            },
          }
        : {}),
      ...(form.hasNetworkPolicy
        ? {
            networkPolicy: {
              payload: {
                policyName,
                displayName: `${form.displayName.trim()} 네트워크 정책`,
                mode: connectionMode,
                ...(connectionMode === "agent_proxy" && agentId
                  ? { agentId }
                  : {}),
                ...(allowedHosts.length > 0 ? { allowedHosts } : {}),
              },
              idempotencyKey: keys.networkPolicy,
            },
          }
        : {}),
      sync: {
        payload: {
          syncName,
          sourceName,
          displayName: form.displayName.trim(),
          sourceType: template.sourceType,
          capability: normalizeManagedSyncCapability(form.capability),
          targetDatasetRef: datasetRef,
          mode: form.syncMode,
          schedule,
          configSummary,
        },
        idempotencyKey: keys.sync,
      },
      ...(form.shouldStartRun
        ? {
            startRun: {
              payload: { triggerType: "manual" },
              idempotencyKey: keys.run,
            },
          }
        : {}),
    };
  };

  const handleRun = async () => {
    const keys = keysRef.current ?? {
      connectorConnection: idempotencyKey("rest_connector", sourceName),
      connectorResource: idempotencyKey("rest_connector_resource", sourceName),
      credential: idempotencyKey("source_credential", sourceName),
      networkPolicy: idempotencyKey("source_network_policy", sourceName),
      sync: idempotencyKey("source_managed_sync", syncName),
      run: idempotencyKey("source_sync_run", syncName),
    };
    keysRef.current = keys;
    setRestConnectorError(null);
    try {
      await ensureRestConnector(keys);
    } catch (error) {
      setRestConnectorError(error);
      return;
    }
    await wizard.run(buildInput(keys));
  };

  const ensureRestConnector = async (
    keys: WizardKeys,
  ): Promise<RestConnectorSetupEvidence | null> => {
    if (template.sourceType !== "rest_api") return null;
    setIsPreparingRestConnector(true);
    try {
      const connectorName = form.connectorName.trim();
      const resourceName = form.resourceName.trim();
      const connection = await client.connectors.connections.create(
        {
          connectorName,
          displayName: `${form.displayName.trim()} REST connector`,
          baseUrl: form.restBaseUrl.trim(),
          auth: restConnectorAuth(
            form,
            form.databaseUrlSecretRef.trim() || defaultCredentialSecretRef,
          ),
          allowPrivateNetwork: connectionMode === "agent_proxy",
        },
        { idempotencyKey: keys.connectorConnection },
      );
      const resource = await client.connectors.resources.upsert(
        connectorName,
        resourceName,
        {
          datasetRef,
          resourcePath: form.restResourcePath.trim(),
          pagination: {
            strategy: "cursor",
            itemsPath: form.restItemsPath.trim() || "items",
            nextCursorPath: form.restNextCursorPath.trim() || "nextCursor",
            cursorQueryParam: form.restCursorQueryParam.trim() || "cursor",
            cursorKey: form.restCursorKey.trim() || "cursor",
          },
          schemaColumns: splitCommaText(form.restSchemaColumnsText),
          primaryKey: splitCommaText(form.restPrimaryKeyText),
        },
        { idempotencyKey: keys.connectorResource },
      );
      const testResult = form.hasCredential
        ? null
        : await client.connectors.resources.test(connectorName, resourceName);
      if (testResult && testResult.status.toLowerCase() !== "succeeded") {
        throw new Error(
          `REST connector resource test failed: ${testResult.status} rows=${testResult.rowCount}`,
        );
      }
      const evidence = { connection, resource, testResult };
      setRestConnectorSetup(evidence);
      return evidence;
    } finally {
      setIsPreparingRestConnector(false);
    }
  };

  const phaseItems = useMemo(
    () =>
      resolvePhaseItems(
        [
          {
            id: "templates",
            label: "템플릿 확인",
            isDone: wizard.templates.length > 0,
            detail: template.sourceType,
          },
          {
            id: "credential",
            label: "자격 증명 저장",
            isDone: wizard.credential !== null,
            isSkipped: !form.hasCredential,
            detail: wizard.credential?.credentialName,
          },
          {
            id: "network",
            label: "네트워크 정책 준비",
            isDone: wizard.networkPolicy !== null,
            isSkipped: !form.hasNetworkPolicy,
            detail: wizard.networkPolicy?.policyName,
          },
          {
            id: "connector",
            label: "REST connector/resource 준비",
            isDone:
              template.sourceType !== "rest_api" || restConnectorSetup !== null,
            isSkipped: template.sourceType !== "rest_api",
            detail:
              restConnectorSetup === null
                ? undefined
                : `${restConnectorSetup.connection.connectorName}/${restConnectorSetup.resource.resourceName}`,
          },
          {
            id: "sync",
            label: "관리형 동기화 생성",
            isDone: wizard.sync !== null,
            detail: wizard.sync?.syncName,
          },
          {
            id: "run",
            label: "동기화 run 시작",
            isDone: wizard.syncRun !== null,
            isSkipped: !form.shouldStartRun,
            detail: wizard.syncRun?.runId,
          },
          {
            id: "evidence",
            label: "운영 증거 연결",
            isDone:
              wizard.phase === "operations_evidence" ||
              wizard.phase === "ready_for_ontology",
            detail: wizard.operationsPath ?? undefined,
          },
          {
            id: "ready",
            label: "온톨로지 연결 준비 완료",
            isDone:
              wizard.phase === "ready_for_ontology" &&
              !isFailedStatus(wizard.syncRun?.status),
          },
        ],
        {
          hasError:
            wizard.error !== null ||
            restConnectorError !== null ||
            isFailedStatus(wizard.syncRun?.status),
          isRunning: wizard.isRunning || isPreparingRestConnector,
        },
      ),
    [
      wizard,
      form,
      template.sourceType,
      restConnectorSetup,
      restConnectorError,
      isPreparingRestConnector,
    ],
  );

  const handleBack = () => {
    if (stepIndex === 0) onExit();
    else setStepIndex(stepIndex - 1);
  };

  return (
    <WizardStepLayout
      title={form.displayName.trim() || "이름 없는 소스"}
      subtitle={`${template.displayName} · ${MANAGED_STEPS[stepIndex].title}`}
      steps={MANAGED_STEPS}
      activeIndex={stepIndex}
      onBack={handleBack}
      onCancel={onCancel}
    >
      {stepIndex === 0 ? (
        <CredentialNetworkStep
          identitySection={
            <section className="space-y-3 rounded border bg-card p-4">
              <div className="text-xs font-semibold">소스 식별</div>
              <div className="grid gap-3 md:grid-cols-2">
                <WizardField
                  label="표시 이름"
                  helper="프로젝트에 저장 단계에서 지정한 이름입니다."
                >
                  <Input
                    value={form.displayName}
                    onChange={(event) =>
                      updateForm({ displayName: event.target.value })
                    }
                    placeholder="예: 주문 ERP 연결"
                    className="h-8 text-xs"
                  />
                </WizardField>
                <WizardField
                  label="소스 이름"
                  helper="시스템 식별자입니다."
                  error={sourceNameError}
                >
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
                    placeholder="orders_erp"
                    className="h-8 font-mono text-xs"
                  />
                </WizardField>
              </div>
            </section>
          }
          connectionMode={connectionMode}
          hasCredential={form.hasCredential}
          onHasCredentialChange={(value) =>
            updateForm({ hasCredential: value })
          }
          authScheme={form.authScheme}
          onAuthSchemeChange={(value) => updateForm({ authScheme: value })}
          secretValue={form.secretValue}
          onSecretValueChange={(value) => updateForm({ secretValue: value })}
          credentialName={credentialName}
          hasNetworkPolicy={form.hasNetworkPolicy}
          onHasNetworkPolicyChange={(value) =>
            updateForm({ hasNetworkPolicy: value })
          }
          allowedHostsText={form.allowedHostsText}
          onAllowedHostsChange={(value) =>
            updateForm({ allowedHostsText: value })
          }
          policyName={policyName}
          agentId={agentId ?? ""}
          canContinue={isNameValid && isCredentialValid}
          onBack={handleBack}
          onContinue={() => setStepIndex(1)}
        />
      ) : null}
      {stepIndex === 1 ? (
        <SyncConfigStep
          sourceType={template.sourceType}
          capabilities={managedCapabilities}
          capability={form.capability}
          onCapabilityChange={(value) => updateForm({ capability: value })}
          syncName={syncName}
          datasetRef={datasetRef}
          datasetRefError={datasetRefError}
          onDatasetRefChange={(value) =>
            updateForm({ isDatasetRefTouched: true, datasetRefInput: value })
          }
          syncMode={form.syncMode}
          onSyncModeChange={(value) => updateForm({ syncMode: value })}
          scheduleMode={form.scheduleMode}
          onScheduleModeChange={(value) => updateForm({ scheduleMode: value })}
          everySecondsText={form.everySecondsText}
          onEverySecondsChange={(value) =>
            updateForm({ everySecondsText: value })
          }
          typeConfigSection={
            <TypeConfigSection
              sourceType={template.sourceType}
              form={form}
              databaseUrlSecretRef={databaseUrlSecretRef}
              onChange={updateForm}
            />
          }
          shouldStartRun={form.shouldStartRun}
          onShouldStartRunChange={(value) =>
            updateForm({ shouldStartRun: value })
          }
          isRunSupported={isRunSupported}
          canContinue={isSyncValid}
          onBack={handleBack}
          onContinue={() => setStepIndex(2)}
        />
      ) : null}
      {stepIndex === 2 ? (
        <RunEvidenceStep
          wizard={wizard}
          keys={keysRef.current}
          agentId={agentId}
          restConnectorSetup={restConnectorSetup}
          restConnectorError={restConnectorError}
          isPreparingRestConnector={isPreparingRestConnector}
          phaseItems={phaseItems}
          onRun={() => void handleRun()}
          onComplete={onComplete}
        />
      ) : null}
    </WizardStepLayout>
  );
}

function TypeConfigSection({
  sourceType,
  form,
  databaseUrlSecretRef,
  onChange,
}: {
  sourceType: string;
  form: ManagedFormState;
  databaseUrlSecretRef: string;
  onChange: (patch: Partial<ManagedFormState>) => void;
}) {
  if (sourceType === "rest_api") {
    return (
      <section className="space-y-3 rounded border bg-card p-4">
        <div className="text-xs font-semibold">REST 커넥터 연결</div>
        <p className="text-[11px] text-muted-foreground">
          입력한 연결과 리소스를 먼저 생성한 뒤 managed sync run을 실행합니다.
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          <WizardField label="커넥터 이름">
            <Input
              value={form.connectorName}
              onChange={(event) =>
                onChange({ connectorName: event.target.value })
              }
              placeholder="orders_rest_connector"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardField label="Base URL">
            <Input
              value={form.restBaseUrl}
              onChange={(event) =>
                onChange({ restBaseUrl: event.target.value })
              }
              placeholder="https://api.github.com"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardField label="리소스 이름">
            <Input
              value={form.resourceName}
              onChange={(event) =>
                onChange({ resourceName: event.target.value })
              }
              placeholder="orders"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardField label="리소스 경로">
            <Input
              value={form.restResourcePath}
              onChange={(event) =>
                onChange({ restResourcePath: event.target.value })
              }
              placeholder="/search/repositories?q=foundry&per_page=2"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardField
            label="items path"
            helper="응답 JSON에서 row 배열이 있는 위치입니다."
          >
            <Input
              value={form.restItemsPath}
              onChange={(event) =>
                onChange({ restItemsPath: event.target.value })
              }
              placeholder="items"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardField label="next cursor path">
            <Input
              value={form.restNextCursorPath}
              onChange={(event) =>
                onChange({ restNextCursorPath: event.target.value })
              }
              placeholder="nextCursor"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardField label="schema columns">
            <Input
              value={form.restSchemaColumnsText}
              onChange={(event) =>
                onChange({ restSchemaColumnsText: event.target.value })
              }
              placeholder="id,name,full_name,html_url"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardField label="primary key">
            <Input
              value={form.restPrimaryKeyText}
              onChange={(event) =>
                onChange({ restPrimaryKeyText: event.target.value })
              }
              placeholder="id"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
        </div>
      </section>
    );
  }
  if (sourceType === "postgres_jdbc") {
    return (
      <section className="space-y-3 rounded border bg-card p-4">
        <div className="text-xs font-semibold">데이터베이스 연결</div>
        <div className="grid gap-3 md:grid-cols-2">
          <WizardField
            label="DB URL secret 참조"
            helper="vault에 저장된 접속 문자열 secret 이름"
          >
            <Input
              value={databaseUrlSecretRef}
              onChange={(event) =>
                onChange({ databaseUrlSecretRef: event.target.value })
              }
              placeholder="orders_db_url"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
          <WizardField label="테이블 이름">
            <Input
              value={form.tableName}
              onChange={(event) => onChange({ tableName: event.target.value })}
              placeholder="public.orders"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
        </div>
      </section>
    );
  }
  return null;
}

function RunEvidenceStep({
  wizard,
  keys,
  agentId,
  restConnectorSetup,
  restConnectorError,
  isPreparingRestConnector,
  phaseItems,
  onRun,
  onComplete,
}: {
  wizard: ReturnType<typeof useFoundryLiteProvidedSourceWizard>;
  keys: WizardKeys | null;
  agentId: string | null;
  restConnectorSetup: RestConnectorSetupEvidence | null;
  restConnectorError: unknown;
  isPreparingRestConnector: boolean;
  phaseItems: ReturnType<typeof resolvePhaseItems>;
  onRun: () => void;
  onComplete: (completion: WizardCompletion) => void;
}) {
  const hasFailedSyncRun = isFailedStatus(wizard.syncRun?.status);
  const isReady = wizard.phase === "ready_for_ontology" && !hasFailedSyncRun;
  const hasStarted =
    isPreparingRestConnector ||
    restConnectorSetup !== null ||
    restConnectorError !== null ||
    wizard.isRunning ||
    wizard.sync !== null ||
    wizard.error !== null;
  const operationsHref = toOperationsHref(wizard.operationsPath);
  const datasetHref =
    wizard.syncRun?.datasetVersionId != null
      ? toDatasetHref(wizard.datasetRef)
      : null;
  const runError = wizard.syncRun?.error ?? null;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">실행 & 증거</h2>
        {isReady ? <StatusPill intent="success">완료</StatusPill> : null}
        {wizard.isRunning || isPreparingRestConnector ? (
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
          아래 버튼을 누르면 자격 증명 저장부터 sync run 시작까지 한 번에
          실행되고 각 단계 증거가 기록됩니다.
        </p>
      )}
      {restConnectorError ? <ErrorState error={restConnectorError} /> : null}
      {wizard.error ? <ErrorState error={wizard.error} /> : null}
      {restConnectorSetup ? (
        <EvidenceList title="REST connector 증거">
          <EvidenceRow
            label="connector"
            value={restConnectorSetup.connection.connectorName}
          />
          <EvidenceRow
            label="base url"
            value={restConnectorSetup.connection.baseUrl}
          />
          <EvidenceRow
            label="resource"
            value={`${restConnectorSetup.resource.resourceName} · ${restConnectorSetup.resource.resourcePath}`}
          />
          <EvidenceRow
            label="resource test"
            value={
              restConnectorSetup.testResult
                ? `${restConnectorSetup.testResult.status} · rows=${restConnectorSetup.testResult.rowCount}`
                : "credential 생성 후 sync run에서 검증"
            }
          />
        </EvidenceList>
      ) : null}
      {isReady ? (
        <EvidenceList title="생성 결과 증거">
          <EvidenceRow label="request id" value={wizard.requestId} />
          <EvidenceRow label="sync 멱등 키" value={keys?.sync} />
          <EvidenceRow label="run 멱등 키" value={keys?.run} />
          <EvidenceRow
            label="자격 증명"
            value={
              wizard.credential
                ? `${wizard.credential.credentialName} (${wizard.credential.authScheme}) · secret=${wizard.credential.secretRef.value}`
                : null
            }
          />
          <EvidenceRow
            label="에이전트"
            value={agentId ?? wizard.agent?.agentId}
          />
          <EvidenceRow
            label="네트워크 정책"
            value={wizard.networkPolicy?.policyName}
          />
          <EvidenceRow
            label="동기화"
            value={
              wizard.sync
                ? `${wizard.sync.syncName} · ${wizard.sync.configFingerprint}`
                : null
            }
          />
          <EvidenceRow label="run id" value={wizard.syncRun?.runId}>
            {wizard.syncRun ? (
              <span className="flex items-center gap-1.5">
                <span className="truncate">{wizard.syncRun.runId}</span>
                <StatusPill intent={statusIntent(wizard.syncRun.status)}>
                  {statusLabel(wizard.syncRun.status)}
                </StatusPill>
              </span>
            ) : undefined}
          </EvidenceRow>
          <EvidenceRow label="대상 데이터셋" value={wizard.datasetRef} />
          <EvidenceRow label="operations" value={wizard.operationsPath} />
        </EvidenceList>
      ) : null}
      {runError ? (
        <div className="rounded border border-warning/40 bg-warning/5 p-3 text-xs">
          <div className="font-semibold text-warning">
            run이 실패 증거로 기록되었습니다
          </div>
          <div className="mt-1 font-mono text-[11px] text-muted-foreground">
            {readTextField(runError, "message") ?? JSON.stringify(runError)}
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">
            운영 증거 링크에서 재시도 여부를 확인하세요.
          </div>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {!isReady ? (
          <Button
            size="sm"
            disabled={wizard.isRunning || isPreparingRestConnector}
            onClick={onRun}
          >
            {wizard.error || restConnectorError ? (
              <>
                <RotateCw className="size-3.5" /> 같은 키로 다시 시도
              </>
            ) : (
              <>
                <Play className="size-3.5" /> 소스 생성 & 실행
              </>
            )}
          </Button>
        ) : null}
        {datasetHref ? (
          <Button asChild size="sm" variant={isReady ? "default" : "outline"}>
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
        {wizard.sync ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              onComplete({
                sourceName: wizard.sync?.sourceName ?? null,
                syncName: wizard.sync?.syncName ?? null,
              })
            }
          >
            동기화 상세로 이동
          </Button>
        ) : null}
      </div>
      {keys && !isReady ? (
        <div className="font-mono text-[11px] text-muted-foreground">
          idempotency-key(sync)={keys.sync}
        </div>
      ) : null}
    </div>
  );
}

function restConnectorAuth(
  form: ManagedFormState,
  secretRef: string,
): { mode: "none" } | { mode: "bearer"; tokenSecretRef: string } | {
  mode: "header";
  headerName: string;
  headerValueSecretRef: string;
} {
  if (!form.hasCredential) return { mode: "none" };
  if (form.authScheme === "api_key") {
    return {
      mode: "header",
      headerName: "Authorization",
      headerValueSecretRef: secretRef,
    };
  }
  return { mode: "bearer", tokenSecretRef: secretRef };
}

function splitCommaText(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function isFailedStatus(status: string | null | undefined): boolean {
  return status?.toLowerCase() === "failed";
}
