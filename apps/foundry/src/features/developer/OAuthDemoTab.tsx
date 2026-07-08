import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { CheckCircle2, KeyRound, XCircle } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { CopyField } from "./CopyField";
import type { OsdkClientRow } from "./developer-model";

interface OAuthDemoTabProps {
  clients: OsdkClientRow[];
}

type FlowStep = {
  label: string;
  status: "pending" | "running" | "success" | "error";
  detail?: string;
};

type TokenResult = {
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  expiresIn: number;
  scope: string;
  sessionId: string;
};

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function createCodeVerifier(): string {
  const random = new Uint8Array(32);
  crypto.getRandomValues(random);
  return base64UrlEncode(random);
}

async function createCodeChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64UrlEncode(new Uint8Array(digest));
}

function readString(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function readNumber(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  return typeof value === "number" ? value : 0;
}

/**
 * OAuth 데모 탭.
 * auth.osdkOAuth.authorize → token (PKCE S256) flow.
 * 성공 시 토큰 증거, 실패 시 typed error + request id 노출.
 */
export function OAuthDemoTab({ clients }: OAuthDemoTabProps) {
  const client = useFoundryLiteClient();
  const activeClients = clients.filter((item) => item.status === "active");
  // redirect URI가 있는 클라이언트를 우선 선택한다 (authorize에 redirect URI가 필수).
  const defaultClient =
    activeClients.find((item) => item.redirectUris.length > 0) ??
    activeClients[0];
  const [selectedClientId, setSelectedClientId] = useState<string>(
    defaultClient?.clientId ?? "",
  );
  const [forceFailure, setForceFailure] = useState(false);
  const [steps, setSteps] = useState<FlowStep[]>([]);
  const [token, setToken] = useState<TokenResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [isRunning, setIsRunning] = useState(false);

  const selectedClient = clients.find(
    (item) => item.clientId === selectedClientId,
  );
  const redirectUri =
    selectedClient?.redirectUris[0] ?? "http://127.0.0.1:4173/oauth/callback";
  const scopes = selectedClient?.allowedScopes ?? [];

  const runFlow = async () => {
    if (!selectedClient) return;
    setIsRunning(true);
    setToken(null);
    setError(null);
    const initial: FlowStep[] = [
      {
        label: "1. PKCE code_verifier / code_challenge 생성",
        status: "running",
      },
      { label: "2. authorize — authorization code 발급", status: "pending" },
      { label: "3. token — access/refresh token 교환", status: "pending" },
    ];
    setSteps(initial);

    const update = (index: number, patch: Partial<FlowStep>) =>
      setSteps((prev) =>
        prev.map((step, i) => (i === index ? { ...step, ...patch } : step)),
      );

    try {
      const verifier = createCodeVerifier();
      const challenge = await createCodeChallenge(verifier);
      update(0, {
        status: "success",
        detail: `challenge=${challenge.slice(0, 16)}… (S256)`,
      });

      update(1, { status: "running" });
      const authorizeResponse = await client.auth.osdkOAuth.authorize({
        clientId: selectedClient.clientId,
        redirectUri,
        codeChallenge: challenge,
        codeChallengeMethod: "S256",
        scopes,
        state: `demo-${Date.now()}`,
      });
      const code = readString(authorizeResponse, "code");
      update(1, {
        status: "success",
        detail: `code=${code.slice(0, 20)}…`,
      });

      update(2, { status: "running" });
      // forceFailure이면 잘못된 code_verifier로 PKCE 검증 실패를 유도한다.
      const tokenResponse = await client.auth.osdkOAuth.token({
        clientId: selectedClient.clientId,
        code,
        redirectUri,
        codeVerifier: forceFailure ? `${verifier}-tampered` : verifier,
      });
      const claims =
        (tokenResponse.claims as Record<string, unknown> | undefined) ?? {};
      setToken({
        accessToken: readString(tokenResponse, "accessToken"),
        refreshToken: readString(tokenResponse, "refreshToken"),
        tokenType: readString(tokenResponse, "tokenType"),
        expiresIn: readNumber(tokenResponse, "expiresIn"),
        scope: readString(claims, "scope"),
        sessionId: readString(tokenResponse, "sessionId"),
      });
      update(2, { status: "success", detail: "토큰 발급 완료" });
    } catch (caught) {
      setError(caught);
      setSteps((prev) => {
        const runningIndex = prev.findIndex(
          (step) => step.status === "running",
        );
        if (runningIndex === -1) return prev;
        return prev.map((step, i) =>
          i === runningIndex ? { ...step, status: "error" } : step,
        );
      });
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="section-label">
        OAuth 데모 — Authorization Code + PKCE
      </div>
      <p className="text-xs text-muted-foreground">
        선택한 클라이언트로 authorize → token flow를 실제 실행합니다. 성공 시
        토큰 증거를, 실패 시 typed error와 request id를 노출합니다.
      </p>

      {activeClients.length === 0 ? (
        <div className="rounded border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
          활성 클라이언트가 없습니다. 먼저 클라이언트를 생성하세요.
        </div>
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-52 space-y-1">
            <div className="section-label">클라이언트</div>
            <Select
              value={selectedClientId}
              onValueChange={setSelectedClientId}
            >
              <SelectTrigger size="sm" className="w-full">
                <SelectValue placeholder="클라이언트 선택" />
              </SelectTrigger>
              <SelectContent>
                {activeClients.map((item) => (
                  <SelectItem key={item.id} value={item.clientId}>
                    <span className="font-mono text-[11px]">
                      {item.clientId}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="flex cursor-pointer items-center gap-1.5 pb-1.5 text-xs">
            <input
              type="checkbox"
              checked={forceFailure}
              onChange={(event) => setForceFailure(event.target.checked)}
              className="size-3.5 accent-destructive"
            />
            PKCE 실패 유도 (typed error 확인)
          </label>
          <Button
            size="sm"
            onClick={() => void runFlow()}
            disabled={isRunning || !selectedClient}
          >
            <KeyRound className="size-3.5" /> flow 실행
          </Button>
        </div>
      )}

      {selectedClient ? (
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span>redirect_uri={redirectUri}</span>
          <span>scope={scopes.length > 0 ? scopes.join(" ") : "(none)"}</span>
        </div>
      ) : null}

      {selectedClient && selectedClient.redirectUris.length === 0 ? (
        <div className="rounded border border-warning/40 bg-warning/5 px-3 py-2 text-[11px] text-warning">
          이 클라이언트는 등록된 redirect URI가 없습니다. authorize 단계에서
          실패합니다 — 클라이언트를 수정해 redirect URI를 추가하세요.
        </div>
      ) : null}

      {steps.length > 0 ? (
        <div className="space-y-1.5 rounded border bg-card p-3">
          {steps.map((step) => (
            <div key={step.label} className="flex items-center gap-2 text-xs">
              {step.status === "success" ? (
                <CheckCircle2 className="size-3.5 text-success" />
              ) : step.status === "error" ? (
                <XCircle className="size-3.5 text-destructive" />
              ) : step.status === "running" ? (
                <span className="size-3.5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              ) : (
                <span className="size-3.5 rounded-full border border-muted-foreground/40" />
              )}
              <span
                className={
                  step.status === "pending" ? "text-muted-foreground" : ""
                }
              >
                {step.label}
              </span>
              {step.detail ? (
                <span className="font-mono text-[11px] text-muted-foreground">
                  {step.detail}
                </span>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {error ? <ErrorState error={error} /> : null}

      {token ? (
        <div className="space-y-2 rounded border border-success/40 bg-success/5 p-3">
          <div className="flex items-center gap-1.5 text-[13px] font-semibold text-success">
            <CheckCircle2 className="size-4" /> 토큰 발급 성공
          </div>
          <div className="flex flex-wrap gap-2">
            <StatusPill intent="neutral">type={token.tokenType}</StatusPill>
            <StatusPill intent="info">expires_in={token.expiresIn}s</StatusPill>
            {token.scope ? (
              <StatusPill intent="neutral">scope={token.scope}</StatusPill>
            ) : null}
          </div>
          <CopyField label="access_token (JWT)" value={token.accessToken} />
          <CopyField label="refresh_token" value={token.refreshToken} />
          <div className="font-mono text-[11px] text-muted-foreground">
            session_id={token.sessionId}
          </div>
        </div>
      ) : null}
    </div>
  );
}
