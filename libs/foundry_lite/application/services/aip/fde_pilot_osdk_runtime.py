"""Portable runtime source embedded in each generated high-level application OSDK."""

from __future__ import annotations

_RUNTIME_SOURCE = r"""export type FoundryLiteObject<ApiName extends string, Properties> = {
  readonly objectType: ApiName;
  readonly objectId: string;
  readonly objectVersion: number;
  readonly properties: Properties;
};

export type OsdkObjectType<TObject extends FoundryLiteObject<string, object>> = {
  readonly kind: "object";
  readonly apiName: TObject["objectType"];
  readonly primaryKey: string;
  readonly titleProperty: string;
  readonly properties: readonly string[];
};

export type OsdkActionType<TPayload, TResult> = {
  readonly kind: "action";
  readonly apiName: string;
  readonly targetObjectType: string;
  readonly targetKind: "object";
  readonly __payload?: TPayload;
  readonly __result?: TResult;
};

export type ActionApplyResponse = {
  readonly status: string;
  readonly actionRunId?: string;
  readonly target?: { readonly objectType: string; readonly objectId: string };
  readonly newObjectVersion?: number;
};

export type FoundryLiteSession = {
  readonly accessToken?: string | null;
  readonly context?: {
    readonly tenantId?: string;
    readonly userId?: string;
    readonly roles?: readonly string[];
    readonly applicationId?: string;
    readonly clientId?: string;
    readonly scopes?: readonly string[];
  };
};

export type FoundryLiteDomainOsHost = {
  readonly baseUrl?: string;
  readonly sessionProvider?: () => FoundryLiteSession | Promise<FoundryLiteSession>;
};

export type OsdkPage<TObject> = {
  readonly data: TObject[];
  readonly items: TObject[];
  readonly nextCursor: string | null;
  readonly nextPageToken: string | null;
};

type ObjectSet<TObject> = {
  fetchPage(options?: { pageSize?: number; pageToken?: string | null }): Promise<OsdkPage<TObject>>;
};

type ActionRequest = {
  readonly objectType?: string;
  readonly objectId: string;
  readonly expectedObjectVersion: number;
  readonly params: object;
};

type ActionInvoker<TResult> = {
  startAction(
    payload: ActionRequest,
    options: { idempotencyKey: string; waitSeconds?: number },
  ): Promise<TResult>;
};

export type FoundryLiteOsdkClient = {
  <TObject extends FoundryLiteObject<string, object>>(resource: OsdkObjectType<TObject>): ObjectSet<TObject>;
  <TPayload extends ActionRequest, TResult>(resource: OsdkActionType<TPayload, TResult>): ActionInvoker<TResult>;
};

type RuntimeConfig = {
  readonly baseUrl: string;
  readonly sessionProvider: () => FoundryLiteSession | Promise<FoundryLiteSession>;
};

declare global {
  var __FOUNDRY_LITE_DOMAIN_OS__: FoundryLiteDomainOsHost | undefined;
}

export function createBrowserFoundryLiteOsdkClient(): FoundryLiteOsdkClient {
  const host = globalThis.__FOUNDRY_LITE_DOMAIN_OS__;
  return createFoundryLiteOsdkClient({
    baseUrl: host?.baseUrl ?? import.meta.env.VITE_FOUNDRY_LITE_API_BASE_URL ?? "",
    sessionProvider: host?.sessionProvider ?? (() => ({})),
  });
}

export function createFoundryLiteOsdkClient(config: RuntimeConfig): FoundryLiteOsdkClient {
  return ((resource: OsdkObjectType<FoundryLiteObject<string, object>> | OsdkActionType<ActionRequest, object>) => {
    if (resource.kind === "object") return objectSet(config, resource);
    return actionInvoker(config, resource);
  }) as FoundryLiteOsdkClient;
}

function objectSet<TObject extends FoundryLiteObject<string, object>>(
  config: RuntimeConfig,
  resource: OsdkObjectType<TObject>,
): ObjectSet<TObject> {
  return {
    async fetchPage(options = {}) {
      const payload = await request<{ items: TObject[]; nextCursor?: string | null }>(
        config,
        `/api/objects/${encodeURIComponent(resource.apiName)}/query`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit: options.pageSize ?? 50, cursor: options.pageToken ?? null }),
        },
      );
      const items = Array.isArray(payload.items) ? payload.items : [];
      const cursor = payload.nextCursor ?? null;
      return { data: items, items, nextCursor: cursor, nextPageToken: cursor };
    },
  };
}

function actionInvoker<TResult>(
  config: RuntimeConfig,
  resource: OsdkActionType<ActionRequest, TResult>,
): ActionInvoker<TResult> {
  return {
    startAction(payload, options) {
      const wait = options.waitSeconds === undefined ? "" : `?waitSeconds=${options.waitSeconds}`;
      return request<TResult>(config, `/api/actions/${encodeURIComponent(resource.apiName)}/runs${wait}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": requireKey(options.idempotencyKey) },
        body: JSON.stringify({
          target: { objectType: payload.objectType ?? resource.targetObjectType, objectId: payload.objectId },
          expectedObjectVersion: payload.expectedObjectVersion,
          params: payload.params,
        }),
      });
    },
  };
}

async function request<TResult>(config: RuntimeConfig, path: string, init: RequestInit): Promise<TResult> {
  const session = await config.sessionProvider();
  const context = session.context ?? {};
  const response = await fetch(`${config.baseUrl.replace(/\/$/, "")}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(session.accessToken ? { Authorization: `Bearer ${session.accessToken}` } : {}),
      ...(context.tenantId ? { "X-Tenant-ID": context.tenantId } : {}),
      ...(context.userId ? { "X-User-ID": context.userId } : {}),
      ...(context.roles?.length ? { "X-Roles": context.roles.join(",") } : {}),
      ...(context.applicationId ? { "X-Application-ID": context.applicationId } : {}),
      ...(context.clientId ? { "X-Client-ID": context.clientId } : {}),
      ...(context.scopes?.length ? { "X-Scopes": context.scopes.join(" ") } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = typeof body?.detail?.message === "string"
      ? body.detail.message
      : `요청을 완료하지 못했습니다 (${response.status}).`;
    throw new Error(message);
  }
  return response.json() as Promise<TResult>;
}

function requireKey(value: string): string {
  if (!value.trim()) throw new Error("업무 실행 키가 비어 있어 중복 실행을 막을 수 없습니다.");
  return value;
}
"""


def portable_runtime_source() -> str:
    """Return the self-contained runtime used only inside the generated app OSDK."""

    return _RUNTIME_SOURCE


__all__ = ["portable_runtime_source"]
