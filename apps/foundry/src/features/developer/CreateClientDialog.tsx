import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

import { toClientRow, type OsdkClientRow } from "./developer-model";

interface CreateClientDialogProps {
  appId: string;
  /** 앱이 연결한 리소스 scope 전체 — 허용 scope는 이 부분집합이어야 한다. */
  availableScopes: string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (client: OsdkClientRow) => void;
}

function splitLines(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/**
 * OAuth 클라이언트 생성 다이얼로그.
 * developerConsole.osdkApplications.createClient (idempotency).
 * 응답의 client id/secret 증거는 상위 Clients 탭에서 노출한다.
 */
export function CreateClientDialog({
  appId,
  availableScopes,
  open,
  onOpenChange,
  onCreated,
}: CreateClientDialogProps) {
  const client = useFoundryLiteClient();
  const [clientId, setClientId] = useState("");
  const [redirectUris, setRedirectUris] = useState("");
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(new Set());
  const [lastKey, setLastKey] = useState<string | null>(null);

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) => {
      const next = new Set(prev);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  };

  const mutation = useFoundryLiteMutation(
    ({
      payloadClientId,
      redirects,
      scopes,
      key,
    }: {
      payloadClientId: string;
      redirects: string[];
      scopes: string[];
      key: string;
    }) =>
      client.developerConsole.osdkApplications.createClient(
        appId,
        {
          clientId: payloadClientId,
          redirectUris: redirects,
          allowedScopes: scopes,
        },
        { idempotencyKey: key },
      ),
    {
      lockKey: ({ payloadClientId }) =>
        `developer:client:create:${payloadClientId}`,
    },
  );

  const reset = () => {
    setClientId("");
    setRedirectUris("");
    setSelectedScopes(new Set());
    setLastKey(null);
  };

  const canSubmit = clientId.trim().length > 0 && !mutation.isRunning;

  const handleSubmit = async () => {
    const trimmedClientId = clientId.trim();
    const key = idempotencyKey("osdk_client_create", trimmedClientId);
    setLastKey(key);
    const created = await mutation.execute({
      payloadClientId: trimmedClientId,
      redirects: splitLines(redirectUris),
      scopes: [...selectedScopes],
      key,
    });
    if (created) {
      onCreated(toClientRow(created));
      reset();
      onOpenChange(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>새 OAuth 클라이언트</DialogTitle>
          <DialogDescription>
            앱에 인증하는 OAuth 클라이언트를 등록합니다. redirect URI와 허용
            scope는 authorization code(PKCE) flow에 사용됩니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="client-id" className="section-label">
              client id
            </Label>
            <Input
              id="client-id"
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
              placeholder="order-portal-web"
              className="font-mono"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="client-redirects" className="section-label">
              redirect URIs
            </Label>
            <Input
              id="client-redirects"
              value={redirectUris}
              onChange={(event) => setRedirectUris(event.target.value)}
              placeholder="http://127.0.0.1:4173/oauth/callback"
              className="font-mono text-[11px]"
            />
            <p className="text-[11px] text-muted-foreground">
              쉼표 또는 줄바꿈으로 여러 개 입력.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label className="section-label">허용 scope</Label>
            {availableScopes.length === 0 ? (
              <p className="rounded border border-dashed px-2 py-2 text-[11px] text-muted-foreground">
                앱에 연결된 리소스 scope가 없습니다. 먼저 개요 탭에서 리소스를
                연결하세요.
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {availableScopes.map((scope) => {
                  const isSelected = selectedScopes.has(scope);
                  return (
                    <button
                      key={scope}
                      type="button"
                      onClick={() => toggleScope(scope)}
                      className={cn(
                        "rounded border px-2 py-0.5 font-mono text-[11px] transition-colors",
                        isSelected
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:bg-muted",
                      )}
                    >
                      {scope}
                    </button>
                  );
                })}
              </div>
            )}
            <p className="text-[11px] text-muted-foreground">
              허용 scope는 앱이 연결한 리소스 범위의 부분집합이어야 합니다.
            </p>
          </div>
          {mutation.error ? <ErrorState error={mutation.error} /> : null}
          {lastKey ? (
            <div className="rounded border bg-muted/40 px-2 py-1 font-mono text-[11px] text-muted-foreground">
              idempotency_key={lastKey}
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isRunning}
          >
            취소
          </Button>
          <Button
            size="sm"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
          >
            클라이언트 생성
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
