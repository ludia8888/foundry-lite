import type {
  ConnectorResourceTestResult,
  SourceConnection,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { CheckCircle2, Eye, Pencil, Save, X } from "lucide-react";
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

import { readTextField, sourceTypeLabel } from "../source-model";
import { SourceCredentialSettings } from "./SourceCredentialSettings";
import { SourceNetworkReadiness } from "./SourceNetworkReadiness";
import { SettingsCard, SettingsRow } from "./SourceSettingsUi";

type SettingsSectionId =
  | "identity"
  | "connection"
  | "credentials"
  | "network";

interface SourceConnectionSettingsProps {
  source: SourceConnection;
  onSaved?: () => void | Promise<void>;
}

interface ConnectionUpdatePayload {
  connectorName: string;
  baseUrl: string;
  allowPrivateNetwork: boolean;
}

interface ConnectionTestPayload {
  connectorName: string;
  resourceName: string;
}

/** Palantir Source setup 구조의 연결 설정 상세. */
export function SourceConnectionSettings({
  source,
  onSaved,
}: SourceConnectionSettingsProps) {
  const [section, setSection] = useState<SettingsSectionId>("connection");
  const config = source.configSummary;
  const auth = readRecordField(config, "auth");
  const resources = readRecordListField(config, "resources");
  const selectedResourceName = readTextField(config, "resourceName");
  const selectedResource =
    resources.find(
      (resource) =>
        readTextField(resource, "resourceName") === selectedResourceName,
    ) ?? resources[0];
  const baseUrl = readTextField(config, "baseUrl");
  const connectorName = readTextField(config, "connectorName");
  const isPrivateNetworkAllowed = config.allowPrivateNetwork === true;
  const connectionConfigFingerprint =
    readTextField(config, "connectionConfigFingerprint") ??
    source.configFingerprint;

  return (
    <div className="mx-auto max-w-5xl overflow-hidden rounded border bg-card">
      <div className="grid min-h-[540px] md:grid-cols-[210px_minmax(0,1fr)]">
        <SettingsNavigation section={section} onSelect={setSection} />
        <div className="min-w-0 p-5 md:p-7">
          <div className="mb-6 border-b pb-4">
            <h2 className="text-lg font-semibold">Source setup</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              외부 시스템 연결과 이 Source가 사용하는 실행 구성을 확인합니다.
            </p>
          </div>
          {section === "identity" ? (
            <IdentitySettings source={source} />
          ) : null}
          {section === "connection" ? (
            source.kind === "kafka" ? (
              <KafkaConnectionDetails source={source} />
            ) : (
              <ConnectorConnectionEditor
                key={`${source.sourceName}:${baseUrl}:${isPrivateNetworkAllowed}`}
                source={source}
                baseUrl={baseUrl}
                connectorName={connectorName}
                selectedResourceName={selectedResourceName}
                selectedResource={selectedResource}
                isPrivateNetworkAllowed={isPrivateNetworkAllowed}
                onSaved={onSaved}
              />
            )
          ) : null}
          {section === "credentials" ? (
            <SourceCredentialSettings
              key={`${source.sourceName}:${connectionConfigFingerprint}`}
              source={source}
              connectorName={connectorName}
              auth={auth}
              configFingerprint={connectionConfigFingerprint}
              onSaved={onSaved}
            />
          ) : null}
          {section === "network" ? (
            <SourceNetworkReadiness
              key={source.sourceName}
              source={source}
              baseUrl={baseUrl}
              isPrivateNetworkAllowed={isPrivateNetworkAllowed}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function SettingsNavigation({
  section,
  onSelect,
}: {
  section: SettingsSectionId;
  onSelect: (section: SettingsSectionId) => void;
}) {
  return (
    <nav
      aria-label="연결 설정 섹션"
      className="border-b bg-muted/25 p-3 md:border-r md:border-b-0"
    >
      <div className="mb-2 px-2 text-[10px] font-semibold tracking-wider text-muted-foreground uppercase">
        Source setup
      </div>
      <SettingsNavButton
        isActive={section === "identity"}
        onClick={() => onSelect("identity")}
      >
        이름 및 위치
      </SettingsNavButton>
      <SettingsNavButton
        isActive={section === "connection"}
        onClick={() => onSelect("connection")}
      >
        연결 상세
      </SettingsNavButton>
      <SettingsNavButton
        isActive={section === "credentials"}
        onClick={() => onSelect("credentials")}
      >
        자격 증명
      </SettingsNavButton>
      <SettingsNavButton
        isActive={section === "network"}
        onClick={() => onSelect("network")}
      >
        네트워크 egress
      </SettingsNavButton>
    </nav>
  );
}

function IdentitySettings({ source }: { source: SourceConnection }) {
  return (
    <SettingsCard
      title="이름 및 위치"
      description="프로젝트 사용자가 보는 이름과 시스템 식별자입니다."
    >
      <dl className="divide-y divide-border/60">
        <SettingsRow label="표시 이름" value={source.displayName} />
        <SettingsRow label="Source 이름" value={source.sourceName} isCode />
        <SettingsRow label="Source 유형" value={sourceTypeLabel(source.kind)} />
        <SettingsRow
          label="출력 데이터셋"
          value={source.targetDatasetRef ?? "구성되지 않음"}
          isCode={source.targetDatasetRef !== null}
        />
      </dl>
    </SettingsCard>
  );
}

function KafkaConnectionDetails({ source }: { source: SourceConnection }) {
  const config = source.configSummary;
  return (
    <div className="space-y-4">
      <SettingsCard
        title="Kafka 연결"
        description="Source 수준의 브로커 연결입니다. topic과 consumer group은 각 Sync에서 선택합니다."
      >
        <dl className="divide-y divide-border/60">
          <SettingsRow
            label="Bootstrap servers"
            value={readTextField(config, "bootstrapServers") ?? "구성되지 않음"}
            isCode
          />
          <SettingsRow
            label="연결 방식"
            value={readTextField(config, "connectionMode") ?? "direct"}
            isCode
          />
          <SettingsRow
            label="TLS"
            value={config.isTlsEnabled === true ? "사용" : "사용 안 함"}
          />
          <SettingsRow
            label="자격 증명"
            value={readTextField(config, "credentialName") ?? "NONE"}
            isCode
          />
        </dl>
      </SettingsCard>
      <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
        <CheckCircle2 className="size-3 text-success" />
        토픽 탐색은 읽기 전용이며 Archive Dataset 커밋은 Sync 실행에서만 일어납니다.
      </p>
    </div>
  );
}

function ConnectorConnectionEditor({
  source,
  baseUrl,
  connectorName,
  selectedResourceName,
  selectedResource,
  isPrivateNetworkAllowed,
  onSaved,
}: {
  source: SourceConnection;
  baseUrl: string | null;
  connectorName: string | null;
  selectedResourceName: string | null;
  selectedResource: Record<string, unknown> | undefined;
  isPrivateNetworkAllowed: boolean;
  onSaved?: () => void | Promise<void>;
}) {
  const client = useFoundryLiteClient();
  const [isEditing, setIsEditing] = useState(false);
  const [baseUrlDraft, setBaseUrlDraft] = useState(baseUrl ?? "");
  const [isPrivateNetworkDraft, setIsPrivateNetworkDraft] = useState(
    isPrivateNetworkAllowed,
  );
  const [testResult, setTestResult] =
    useState<ConnectorResourceTestResult | null>(null);
  const resourceName =
    selectedResourceName ??
    readTextField(selectedResource ?? {}, "resourceName");
  const resourcePath =
    readTextField(selectedResource ?? {}, "resourcePath");
  const isEditable = connectorName !== null && baseUrl !== null;
  const isBaseUrlValid = isValidHttpUrl(baseUrlDraft);
  const hasChanges =
    baseUrlDraft.trim() !== (baseUrl ?? "") ||
    isPrivateNetworkDraft !== isPrivateNetworkAllowed;

  const updateConnection = useFoundryLiteMutation(
    useCallback(
      (payload: ConnectionUpdatePayload) =>
        client.connectors.connections.update(
          payload.connectorName,
          {
            baseUrl: payload.baseUrl,
            allowPrivateNetwork: payload.allowPrivateNetwork,
          },
          {
            idempotencyKey: idempotencyKey(
              "connector-settings-update",
              crypto.randomUUID(),
            ),
          },
        ),
      [client],
    ),
  );
  const testConnection = useFoundryLiteMutation(
    useCallback(
      (payload: ConnectionTestPayload) =>
        client.connectors.resources.test(
          payload.connectorName,
          payload.resourceName,
        ),
      [client],
    ),
  );

  const handleEdit = () => {
    setBaseUrlDraft(baseUrl ?? "");
    setIsPrivateNetworkDraft(isPrivateNetworkAllowed);
    setTestResult(null);
    setIsEditing(true);
  };

  const handleCancel = () => {
    setBaseUrlDraft(baseUrl ?? "");
    setIsPrivateNetworkDraft(isPrivateNetworkAllowed);
    setIsEditing(false);
  };

  const saveDraft = async () => {
    if (!connectorName || !isBaseUrlValid || !hasChanges) return;
    const updated = await updateConnection.execute({
      connectorName,
      baseUrl: baseUrlDraft.trim(),
      allowPrivateNetwork: isPrivateNetworkDraft,
    });
    if (!updated) return;
    setIsEditing(false);
    setTestResult(null);
    toast.success("연결 설정을 저장했습니다");
    await onSaved?.();
  };

  const handleSave = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void saveDraft();
  };

  const handleTest = async () => {
    if (!connectorName || !resourceName) return;
    const result = await testConnection.execute({
      connectorName,
      resourceName,
    });
    if (!result) return;
    setTestResult(result);
    if (result.status.toLowerCase() === "succeeded") {
      toast.success(`미리보기 성공 · ${result.rowCount}행`);
    } else {
      toast.error("미리보기에 실패했습니다");
    }
  };

  return (
    <div className="space-y-4">
      <SettingsCard
        title="연결"
        description="이 Source가 실제 요청을 보낼 외부 endpoint와 리소스입니다."
        actions={
          isEditing ? (
            <div className="flex items-center gap-1.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={handleCancel}
                disabled={updateConnection.isRunning}
              >
                <X className="size-3.5" /> 취소
              </Button>
              <Button
                type="submit"
                form="source-connection-edit-form"
                size="sm"
                disabled={
                  updateConnection.isRunning ||
                  !isBaseUrlValid ||
                  !hasChanges
                }
              >
                <Save className="size-3.5" />
                {updateConnection.isRunning ? "저장 중..." : "변경사항 저장"}
              </Button>
            </div>
          ) : isEditable ? (
            <div className="flex items-center gap-1.5">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void handleTest()}
                disabled={!resourceName || testConnection.isRunning}
              >
                <Eye className="size-3.5" />
                {testConnection.isRunning ? "미리보는 중..." : "미리보기"}
              </Button>
              <Button type="button" size="sm" onClick={handleEdit}>
                <Pencil className="size-3.5" /> 편집
              </Button>
            </div>
          ) : null
        }
      >
        {isEditing ? (
          <form
            id="source-connection-edit-form"
            className="space-y-4 p-4"
            onSubmit={handleSave}
          >
            <div className="space-y-1.5">
              <label
                htmlFor="source-connection-base-url"
                className="text-[11px] font-medium"
              >
                Base URL
              </label>
              <Input
                id="source-connection-base-url"
                data-testid="source-connection-base-url"
                value={baseUrlDraft}
                onChange={(event) => setBaseUrlDraft(event.target.value)}
                className="h-8 font-mono text-xs"
                aria-invalid={!isBaseUrlValid}
              />
              <p
                className={cn(
                  "text-[10px]",
                  isBaseUrlValid
                    ? "text-muted-foreground"
                    : "text-destructive",
                )}
              >
                {isBaseUrlValid
                  ? "http:// 또는 https:// 주소를 입력하세요."
                  : "유효한 HTTP(S) 주소가 필요합니다."}
              </p>
            </div>
            <div className="flex items-start justify-between gap-4 rounded border bg-muted/20 p-3">
              <div>
                <label
                  htmlFor="source-private-network-switch"
                  className="text-xs font-medium"
                >
                  사설 네트워크 주소 허용
                </label>
                <p className="mt-0.5 text-[10px] text-muted-foreground">
                  에이전트 또는 내부 네트워크 정책이 준비된 경우에만 켜세요.
                </p>
              </div>
              <Switch
                id="source-private-network-switch"
                data-testid="source-private-network-switch"
                checked={isPrivateNetworkDraft}
                onCheckedChange={setIsPrivateNetworkDraft}
                aria-label="사설 네트워크 주소 허용"
              />
            </div>
          </form>
        ) : (
          <dl className="divide-y divide-border/60">
            <SettingsRow
              label="Base URL"
              value={baseUrl ?? "해당 Source 유형에서 제공하지 않음"}
              isCode={baseUrl !== null}
            />
            <SettingsRow
              label="커넥터"
              value={connectorName ?? source.sourceName}
              isCode
            />
            <SettingsRow
              label="리소스"
              value={resourceName ?? "구성되지 않음"}
              isCode={resourceName !== null}
            />
            <SettingsRow
              label="요청 경로"
              value={resourcePath ?? "구성되지 않음"}
              isCode={resourcePath !== null}
            />
          </dl>
        )}
      </SettingsCard>
      {updateConnection.error ? (
        <ErrorState
          error={updateConnection.error}
          onRetry={() => void saveDraft()}
        />
      ) : null}
      <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
        <CheckCircle2 className="size-3 text-success" />
        미리보기는 외부 데이터를 읽기만 하며 Dataset에 커밋하지 않습니다.
      </p>
      {testConnection.error ? (
        <ErrorState
          error={testConnection.error}
          onRetry={() => void handleTest()}
        />
      ) : null}
      {testResult ? <ConnectionPreviewResult result={testResult} /> : null}
    </div>
  );
}

function ConnectionPreviewResult({
  result,
}: {
  result: ConnectorResourceTestResult;
}) {
  const isSucceeded = result.status.toLowerCase() === "succeeded";
  const columns = Object.keys(result.sampleRows[0] ?? {}).slice(0, 6);
  const errorMessage =
    readTextField(result.error, "message") ??
    readTextField(result.error, "detail");
  return (
    <section
      aria-live="polite"
      className={cn(
        "overflow-hidden rounded border",
        isSucceeded ? "border-success/30" : "border-destructive/30",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/15 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold">연결 미리보기</span>
          <StatusPill intent={isSucceeded ? "success" : "danger"}>
            {isSucceeded ? "성공" : "실패"}
          </StatusPill>
        </div>
        <span className="font-mono text-[10px] text-muted-foreground">
          {result.rowCount} rows · {result.resourceName}
        </span>
      </div>
      {isSucceeded && columns.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead className="bg-muted/20 text-muted-foreground">
              <tr>
                {columns.map((column) => (
                  <th
                    key={column}
                    scope="col"
                    className="whitespace-nowrap px-3 py-1.5 font-medium"
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {result.sampleRows.slice(0, 3).map((row, index) => (
                <tr key={previewRowKey(row, index)}>
                  {columns.map((column) => (
                    <td key={column} className="max-w-56 truncate px-3 py-1.5 font-mono">
                      {previewCell(row[column])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="px-3 py-2 text-[11px] text-muted-foreground">
          {errorMessage ??
            (isSucceeded
              ? "연결은 성공했지만 미리보기 행이 없습니다."
              : "외부 Source 응답과 네트워크 정책을 확인하세요.")}
        </p>
      )}
    </section>
  );
}

function isValidHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value.trim());
    return (
      (parsed.protocol === "http:" || parsed.protocol === "https:") &&
      parsed.username.length === 0 &&
      parsed.password.length === 0
    );
  } catch {
    return false;
  }
}

function previewRowKey(row: Record<string, unknown>, index: number): string {
  const identity = row.id ?? row.key ?? row.name;
  return identity === undefined
    ? `preview-row-${index}-${JSON.stringify(row).slice(0, 40)}`
    : String(identity);
}

function previewCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function readRecordField(
  value: Record<string, unknown>,
  key: string,
): Record<string, unknown> | null {
  const field = value[key];
  return field !== null && typeof field === "object" && !Array.isArray(field)
    ? (field as Record<string, unknown>)
    : null;
}

function readRecordListField(
  value: Record<string, unknown>,
  key: string,
): Record<string, unknown>[] {
  const field = value[key];
  return Array.isArray(field)
    ? field.filter(
        (item): item is Record<string, unknown> =>
          item !== null && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}

function SettingsNavButton({
  isActive,
  onClick,
  children,
}: {
  isActive: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={isActive ? "page" : undefined}
      className={cn(
        "block w-full rounded px-2 py-1.5 text-left text-xs",
        isActive
          ? "bg-accent font-medium text-foreground"
          : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
