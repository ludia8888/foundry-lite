import type { OsdkApplication } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { Fingerprint, KeyRound, ShieldCheck } from "lucide-react";
import { useMemo } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Badge } from "@/components/ui/badge";
import { DEMO_CONTEXT } from "@/lib/api";

import { useSecurityQuery } from "./use-security-query";

interface OsdkClientRow {
  clientId: string;
  status: string;
  redirectUris: string[];
  allowedScopes: string[];
  accessTokenTtl: number | null;
  refreshTokenTtl: number | null;
}

interface OsdkAppView {
  id: string;
  displayName: string;
  appApiName: string;
  status: string;
  clients: OsdkClientRow[];
  scopes: string[];
}

function readString(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function readStringArray(
  record: Record<string, unknown>,
  key: string,
): string[] {
  const value = record[key];
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === "string")
    : [];
}

function readNumber(
  record: Record<string, unknown>,
  key: string,
): number | null {
  const value = record[key];
  return typeof value === "number" ? value : null;
}

/** OsdkApplication 원시 응답(Record 기반)을 화면 모델로 정규화한다. */
function toAppView(app: OsdkApplication): OsdkAppView {
  const application = app.application;
  const clients: OsdkClientRow[] = app.clients.map((client) => ({
    clientId: readString(client, "client_id"),
    status: readString(client, "status") || "unknown",
    redirectUris: readStringArray(client, "redirect_uris"),
    allowedScopes: readStringArray(client, "allowed_scopes"),
    accessTokenTtl: readNumber(client, "access_token_ttl_seconds"),
    refreshTokenTtl: readNumber(client, "refresh_token_ttl_seconds"),
  }));
  const scopeSet = new Set<string>();
  for (const resource of app.resources) {
    for (const scope of readStringArray(resource, "scopes"))
      scopeSet.add(scope);
  }
  return {
    id: readString(application, "id"),
    displayName:
      readString(application, "display_name") ||
      readString(application, "app_api_name"),
    appApiName: readString(application, "app_api_name"),
    status: readString(application, "status") || "unknown",
    clients,
    scopes: [...scopeSet],
  };
}

function statusIntent(status: string): "success" | "neutral" | "warning" {
  if (status === "active") return "success";
  if (status === "inactive") return "warning";
  return "neutral";
}

/**
 * 인증/세션 탭.
 *
 * 상단: 현재 요청의 header-trust principal(tenant/user/roles) 표시.
 * 하단: developerConsole.osdkApplications.list의 OSDK 앱/클라이언트/스코프 목록과
 * osdkOAuth authorization_code flow evidence.
 */
export function AuthSessionPanel() {
  const client = useFoundryLiteClient();

  const appsQuery = useSecurityQuery(["security", "osdk-apps"], () =>
    client.developerConsole.osdkApplications.list(),
  );
  const apps = useMemo(
    () => (appsQuery.data ?? []).map(toAppView),
    [appsQuery.data],
  );

  return (
    <div className="space-y-4">
      <section className="space-y-2 rounded border bg-card p-3">
        <div className="flex items-center gap-1.5">
          <Fingerprint className="size-4 text-primary" />
          <span className="text-[13px] font-semibold">
            현재 세션 (header-trust)
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground">
          header-trust 인증 프로필에서 신뢰되는 요청 principal입니다. 모든 SDK
          호출은 이 컨텍스트 헤더로 서명됩니다.
        </p>
        <dl className="grid grid-cols-[100px_minmax(0,1fr)] gap-x-3 gap-y-1 text-[12px]">
          <dt className="text-muted-foreground">Tenant</dt>
          <dd className="font-mono text-[11px]">{DEMO_CONTEXT.tenantId}</dd>
          <dt className="text-muted-foreground">User</dt>
          <dd className="font-mono text-[11px]">{DEMO_CONTEXT.userId}</dd>
          <dt className="text-muted-foreground">Roles</dt>
          <dd className="flex flex-wrap gap-1">
            {(DEMO_CONTEXT.roles ?? []).map((role) => (
              <Badge
                key={role}
                variant="secondary"
                className="font-mono text-[10px]"
              >
                {role}
              </Badge>
            ))}
          </dd>
        </dl>
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <KeyRound className="size-4 text-primary" />
            <span className="text-[13px] font-semibold">
              OAuth 앱 &amp; 스코프
            </span>
          </div>
          <span className="text-[11px] text-muted-foreground">
            {apps.length}개 OSDK 애플리케이션
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground">
          Developer Console에 등록된 OSDK 애플리케이션의 authorization code
          grant 클라이언트와 리소스 접근 스코프입니다. OAuth 세션 발급/갱신/폐기
          이력은 감사 관점 탭에서 확인할 수 있습니다.
        </p>

        {appsQuery.isLoading ? (
          <LoadingState rowCount={3} />
        ) : appsQuery.error ? (
          <ErrorState
            error={appsQuery.error}
            onRetry={() => void appsQuery.reload()}
          />
        ) : apps.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="등록된 OSDK 앱이 없습니다"
            description="Developer Console에서 OSDK 애플리케이션을 만들면 OAuth 클라이언트와 스코프가 여기에 표시됩니다."
          />
        ) : (
          <div className="space-y-2">
            {apps.map((app) => (
              <OsdkAppCard key={app.id} app={app} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function OsdkAppCard({ app }: { app: OsdkAppView }) {
  return (
    <div className="space-y-2 rounded border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-semibold">{app.displayName}</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {app.appApiName}
        </span>
        <StatusPill intent={statusIntent(app.status)}>{app.status}</StatusPill>
      </div>

      <div className="space-y-1">
        <span className="section-label">클라이언트</span>
        {app.clients.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            authorization code grant 클라이언트가 없습니다.
          </p>
        ) : (
          <div className="space-y-1">
            {app.clients.map((c) => (
              <div
                key={c.clientId}
                className="flex flex-wrap items-center gap-2 rounded border px-2 py-1.5"
              >
                <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
                  {c.clientId}
                </span>
                <StatusPill intent={statusIntent(c.status)}>
                  {c.status}
                </StatusPill>
                <span className="text-[11px] text-muted-foreground">
                  access {c.accessTokenTtl ?? "—"}s · refresh{" "}
                  {c.refreshTokenTtl ?? "—"}s
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-1">
        <span className="section-label">리소스 접근 스코프</span>
        {app.scopes.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            등록된 리소스 스코프가 없습니다.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1">
            {app.scopes.map((scope) => (
              <Badge
                key={scope}
                variant="outline"
                className="font-mono text-[10px]"
              >
                {scope}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
