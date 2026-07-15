import type {
  RestConnectorAuthInput,
  SourceConnection,
  SourceCredential,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import type { FoundryLiteMutationState } from "@foundry-lite/sdk/react";
import { KeyRound, Plus, Save, ShieldCheck, X } from "lucide-react";
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
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

import { formatTimestamp, readTextField } from "../source-model";
import { useSourceCredentials } from "../use-source-queries";
import {
  CopyableValue,
  SettingsCard,
  SettingsRow,
} from "./SourceSettingsUi";

type AuthMode = "none" | "bearer" | "basic" | "header";

interface CredentialBindingPayload {
  connectorName: string;
  auth: RestConnectorAuthInput;
}

interface CredentialCreatePayload {
  credentialName: string;
  displayName: string;
  kind: string;
  authScheme: string;
  secretValue: string;
}

interface SourceCredentialSettingsProps {
  source: SourceConnection;
  connectorName: string | null;
  auth: Record<string, unknown> | null;
  configFingerprint: string;
  onSaved?: () => void | Promise<void>;
}

/** Source가 사용할 비밀 참조를 생성하고 REST connector 인증에 연결한다. */
export function SourceCredentialSettings({
  source,
  connectorName,
  auth,
  configFingerprint,
  onSaved,
}: SourceCredentialSettingsProps) {
  const client = useFoundryLiteClient();
  const credentialsQuery = useSourceCredentials();
  const currentMode = authMode(auth);
  const currentSecretRef = authSecretRef(auth);
  const currentHeaderName =
    readTextField(auth ?? {}, "headerName") ?? "Authorization";
  const [isEditing, setIsEditing] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [modeDraft, setModeDraft] = useState<AuthMode>(currentMode);
  const [secretRefDraft, setSecretRefDraft] = useState(
    currentSecretRef ?? "",
  );
  const [headerNameDraft, setHeaderNameDraft] =
    useState(currentHeaderName);
  const credentials = credentialsQuery.data ?? [];
  const currentCredential = credentials.find(
    (credential) => credential.secretRef.name === currentSecretRef,
  );
  const hasBindingChanges =
    modeDraft !== currentMode ||
    (modeDraft !== "none" && secretRefDraft !== (currentSecretRef ?? "")) ||
    (modeDraft === "header" && headerNameDraft.trim() !== currentHeaderName);

  const bindCredential = useFoundryLiteMutation(
    useCallback(
      (payload: CredentialBindingPayload) =>
        client.connectors.connections.update(
          payload.connectorName,
          { auth: payload.auth },
          {
            idempotencyKey: idempotencyKey(
              "source_credential_binding",
              crypto.randomUUID(),
            ),
          },
        ),
      [client],
    ),
    {
      lockKey: (payload) =>
        `sources:credentials:bind:${payload.connectorName}`,
    },
  );

  const createCredential = useFoundryLiteMutation(
    useCallback(
      (payload: CredentialCreatePayload) =>
        client.sources.credentials.create(payload, {
          idempotencyKey: idempotencyKey(
            "source_credential",
            `${payload.credentialName}:${crypto.randomUUID()}`,
          ),
        }),
      [client],
    ),
    {
      lockKey: (payload) =>
        `sources:credentials:create:${payload.credentialName}`,
    },
  );

  const resetDraft = () => {
    setModeDraft(currentMode);
    setSecretRefDraft(currentSecretRef ?? "");
    setHeaderNameDraft(currentHeaderName);
  };

  const handleApply = async () => {
    if (!connectorName || !hasBindingChanges) return;
    const nextAuth = credentialAuth(
      modeDraft,
      secretRefDraft,
      headerNameDraft,
    );
    if (!nextAuth) return;
    const updated = await bindCredential.execute({
      connectorName,
      auth: nextAuth,
    });
    if (!updated) return;
    setIsEditing(false);
    toast.success("Source 인증정보를 적용했습니다");
    await onSaved?.();
  };

  const handleCreated = async (credential: SourceCredential) => {
    setSecretRefDraft(credential.secretRef.name);
    setModeDraft(
      credential.authScheme === "api_key"
        ? "header"
        : credential.authScheme === "basic_auth"
          ? "basic"
          : "bearer",
    );
    setIsCreating(false);
    await credentialsQuery.reload();
    toast.success("인증정보를 만들었습니다. 적용을 눌러 Source에 연결하세요");
  };

  if (credentialsQuery.isLoading && !credentialsQuery.data) {
    return <LoadingState rowCount={4} />;
  }

  return (
    <div className="space-y-4">
      <SettingsCard
        title="자격 증명"
        description="비밀 값은 표시하지 않고 보안 저장소의 참조만 Source에 연결합니다."
        actions={
          isEditing ? (
            <div className="flex gap-1.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={bindCredential.isRunning}
                onClick={() => {
                  resetDraft();
                  setIsEditing(false);
                  setIsCreating(false);
                }}
              >
                <X className="size-3.5" /> 취소
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={
                  bindCredential.isRunning ||
                  !hasBindingChanges ||
                  !canApplyCredential(modeDraft, secretRefDraft, headerNameDraft)
                }
                onClick={() => void handleApply()}
              >
                <Save className="size-3.5" />
                {bindCredential.isRunning ? "적용 중..." : "인증정보 적용"}
              </Button>
            </div>
          ) : connectorName ? (
            <Button
              type="button"
              size="sm"
              onClick={() => {
                resetDraft();
                setIsEditing(true);
              }}
            >
              <KeyRound className="size-3.5" /> 인증정보 변경
            </Button>
          ) : null
        }
      >
        {isEditing ? (
          <div className="space-y-4 p-4">
            <CredentialBindingForm
              mode={modeDraft}
              onModeChange={setModeDraft}
              secretRef={secretRefDraft}
              onSecretRefChange={setSecretRefDraft}
              headerName={headerNameDraft}
              onHeaderNameChange={setHeaderNameDraft}
              credentials={credentials}
            />
            <div className="border-t pt-3">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setIsCreating((value) => !value)}
              >
                <Plus className="size-3.5" /> 새 인증정보
              </Button>
            </div>
            {isCreating ? (
              <CredentialCreateForm
                source={source}
                mutation={createCredential}
                onCreated={handleCreated}
                onCancel={() => setIsCreating(false)}
              />
            ) : null}
          </div>
        ) : (
          <dl className="divide-y divide-border/60">
            <SettingsRow
              label="인증 방식"
              value={authModeLabel(currentMode)}
            />
            <SettingsRow
              label="연결된 인증정보"
              value={
                currentMode === "none"
                  ? "사용하지 않음"
                  : currentCredential?.displayName ??
                    currentSecretRef ??
                    "참조를 확인할 수 없음"
              }
              isCode={!currentCredential && currentSecretRef !== null}
            />
            <SettingsRow
              label="마지막 변경"
              value={
                currentCredential
                  ? formatTimestamp(currentCredential.updatedAt)
                  : "—"
              }
            />
            <SettingsRow
              label="비밀 값"
              value={
                currentMode === "none"
                  ? "저장하지 않음"
                  : "보안 저장소에 보관 · 화면과 로그에서 숨김"
              }
            />
          </dl>
        )}
      </SettingsCard>
      {credentialsQuery.error ? (
        <ErrorState
          error={credentialsQuery.error}
          onRetry={() => void credentialsQuery.reload()}
        />
      ) : null}
      {bindCredential.error ? (
        <ErrorState
          error={bindCredential.error}
          onRetry={() => void handleApply()}
        />
      ) : null}
      {createCredential.error ? (
        <ErrorState error={createCredential.error} />
      ) : null}
      <SettingsCard
        title="구성 무결성"
        description="인증 또는 연결 설정이 달라지면 이 지문도 변경됩니다."
      >
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <CopyableValue value={configFingerprint} />
          <StatusPill intent="neutral">
            <ShieldCheck className="size-3" /> secret redacted
          </StatusPill>
        </div>
      </SettingsCard>
    </div>
  );
}

function CredentialBindingForm({
  mode,
  onModeChange,
  secretRef,
  onSecretRefChange,
  headerName,
  onHeaderNameChange,
  credentials,
}: {
  mode: AuthMode;
  onModeChange: (value: AuthMode) => void;
  secretRef: string;
  onSecretRefChange: (value: string) => void;
  headerName: string;
  onHeaderNameChange: (value: string) => void;
  credentials: readonly SourceCredential[];
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <label className="space-y-1.5 text-[11px] font-medium">
        <span>인증 방식</span>
        <Select value={mode} onValueChange={(value) => onModeChange(value as AuthMode)}>
          <SelectTrigger className="h-8 w-full text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">인증 없음</SelectItem>
            <SelectItem value="bearer">Bearer token</SelectItem>
            <SelectItem value="basic">Basic 인증</SelectItem>
            <SelectItem value="header">API key header</SelectItem>
          </SelectContent>
        </Select>
      </label>
      {mode !== "none" ? (
        <label className="space-y-1.5 text-[11px] font-medium">
          <span>인증정보</span>
          <Select value={secretRef || undefined} onValueChange={onSecretRefChange}>
            <SelectTrigger
              data-testid="source-credential-select"
              className="h-8 w-full text-xs"
            >
              <SelectValue placeholder="인증정보 선택" />
            </SelectTrigger>
            <SelectContent>
              {credentials.map((credential) => (
                <SelectItem
                  key={credential.credentialName}
                  value={credential.secretRef.name}
                >
                  {credential.displayName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      ) : null}
      {mode === "header" ? (
        <label className="space-y-1.5 text-[11px] font-medium md:col-span-2">
          <span>Header 이름</span>
          <Input
            value={headerName}
            onChange={(event) => onHeaderNameChange(event.target.value)}
            placeholder="Authorization 또는 X-API-Key"
            className="h-8 font-mono text-xs"
          />
        </label>
      ) : null}
    </div>
  );
}

function CredentialCreateForm({
  source,
  mutation,
  onCreated,
  onCancel,
}: {
  source: SourceConnection;
  mutation: FoundryLiteMutationState<SourceCredential, CredentialCreatePayload>;
  onCreated: (credential: SourceCredential) => void | Promise<void>;
  onCancel: () => void;
}) {
  const [displayName, setDisplayName] = useState(`${source.displayName} 인증정보`);
  const [credentialName, setCredentialName] = useState(
    `${source.sourceName}_credential_${Date.now().toString(36)}`,
  );
  const [authScheme, setAuthScheme] = useState("bearer");
  const [secretValue, setSecretValue] = useState("");
  const isNameValid = /^[A-Za-z_][A-Za-z0-9_]*$/.test(credentialName);
  const canCreate =
    displayName.trim().length > 0 && isNameValid && secretValue.length > 0;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canCreate) return;
    const credential = await mutation.execute({
      credentialName,
      displayName: displayName.trim(),
      kind: source.kind === "rest" ? "rest_api" : source.kind,
      authScheme,
      secretValue,
    });
    if (!credential) return;
    setSecretValue("");
    await onCreated(credential);
  };

  return (
    <form
      className="space-y-3 rounded border border-primary/20 bg-primary/[0.025] p-3"
      onSubmit={(event) => void handleSubmit(event)}
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold">새 인증정보 만들기</div>
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            생성 후 비밀 값은 다시 표시되지 않습니다.
          </p>
        </div>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          취소
        </Button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1 text-[11px] font-medium">
          <span>표시 이름</span>
          <Input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            className="h-8 text-xs"
          />
        </label>
        <label className="space-y-1 text-[11px] font-medium">
          <span>시스템 이름</span>
          <Input
            data-testid="source-new-credential-name"
            value={credentialName}
            onChange={(event) => setCredentialName(event.target.value)}
            aria-invalid={!isNameValid}
            className="h-8 font-mono text-xs"
          />
        </label>
        <label className="space-y-1 text-[11px] font-medium">
          <span>비밀 유형</span>
          <Select value={authScheme} onValueChange={setAuthScheme}>
            <SelectTrigger className="h-8 w-full text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="bearer">Bearer token</SelectItem>
              <SelectItem value="basic_auth">Basic 인증</SelectItem>
              <SelectItem value="api_key">API key</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <label className="space-y-1 text-[11px] font-medium">
          <span>비밀 값</span>
          <Input
            data-testid="source-new-credential-secret"
            type="password"
            autoComplete="new-password"
            value={secretValue}
            onChange={(event) => setSecretValue(event.target.value)}
            className="h-8 font-mono text-xs"
          />
        </label>
      </div>
      {!isNameValid ? (
        <p className="text-[10px] text-destructive">
          시스템 이름은 영문 또는 밑줄로 시작하고 영문·숫자·밑줄만 사용할 수 있습니다.
        </p>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={!canCreate || mutation.isRunning}>
          <Plus className="size-3.5" />
          {mutation.isRunning ? "만드는 중..." : "인증정보 만들기"}
        </Button>
      </div>
    </form>
  );
}

function authMode(auth: Record<string, unknown> | null): AuthMode {
  const mode = readTextField(auth ?? {}, "mode");
  return mode === "bearer" || mode === "basic" || mode === "header"
    ? mode
    : "none";
}

function authSecretRef(auth: Record<string, unknown> | null): string | null {
  if (authMode(auth) === "bearer") {
    return readTextField(auth ?? {}, "tokenSecretRef");
  }
  if (authMode(auth) === "header") {
    return readTextField(auth ?? {}, "headerValueSecretRef");
  }
  if (authMode(auth) === "basic") {
    return readTextField(auth ?? {}, "basicCredentialsSecretRef");
  }
  return null;
}

function credentialAuth(
  mode: AuthMode,
  secretRef: string,
  headerName: string,
): RestConnectorAuthInput | null {
  if (mode === "none") return { mode: "none" };
  if (!secretRef) return null;
  if (mode === "bearer") {
    return { mode: "bearer", tokenSecretRef: secretRef };
  }
  if (mode === "basic") {
    return { mode: "basic", basicCredentialsSecretRef: secretRef };
  }
  if (!headerName.trim()) return null;
  return {
    mode: "header",
    headerName: headerName.trim(),
    headerValueSecretRef: secretRef,
  };
}

function canApplyCredential(
  mode: AuthMode,
  secretRef: string,
  headerName: string,
): boolean {
  return credentialAuth(mode, secretRef, headerName) !== null;
}

function authModeLabel(mode: AuthMode): string {
  if (mode === "bearer") return "Bearer token";
  if (mode === "basic") return "Basic 인증";
  if (mode === "header") return "API key header";
  return "인증 없음";
}
