import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { History, KeyRound, Plus, RotateCw, ShieldX } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DataTable } from "@/components/shared/DataTable";
import type { DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { CopyField } from "./CopyField";
import { CreateClientDialog } from "./CreateClientDialog";
import {
  formatTimestamp,
  statusIntent,
  type OsdkClientRow,
} from "./developer-model";
import { useOsdkClients } from "./use-developer-queries";

interface ClientsTabProps {
  appId: string;
  /** 앱이 연결한 리소스 scope 전체 — 클라이언트 허용 scope는 이 부분집합이어야 한다. */
  availableScopes: string[];
  onClientsChanged: () => void;
}

/**
 * 클라이언트 관리 탭.
 * createClient / deactivateClient + client id/secret 증거.
 */
export function ClientsTab({
  appId,
  availableScopes,
  onClientsChanged,
}: ClientsTabProps) {
  const client = useFoundryLiteClient();
  const clientsQuery = useOsdkClients(appId);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createdClient, setCreatedClient] = useState<OsdkClientRow | null>(
    null,
  );
  const [pendingRowId, setPendingRowId] = useState<string | null>(null);
  const [lastKey, setLastKey] = useState<string | null>(null);
  const [secretReceipt, setSecretReceipt] = useState<{
    clientId: string;
    clientSecret: string;
  } | null>(null);
  const [secretHistory, setSecretHistory] = useState<
    Array<Record<string, unknown>>
  >([]);

  const deactivate = useFoundryLiteMutation(
    ({ rowId, key }: { rowId: string; key: string }) =>
      client.developerConsole.osdkApplications.deactivateClient(appId, rowId, {
        idempotencyKey: key,
      }),
    { lockKey: ({ rowId }) => `developer:client:deactivate:${rowId}` },
  );
  const rotateSecret = useFoundryLiteMutation(
    ({ rowId, key }: { rowId: string; key: string }) =>
      client.developerConsole.osdkApplications.rotateClientSecret(
        appId,
        rowId,
        { reason: "Operator rotation from Developer Console" },
        { idempotencyKey: key },
      ),
    { lockKey: ({ rowId }) => `developer:client-secret:rotate:${rowId}` },
  );
  const revokeSecret = useFoundryLiteMutation(
    ({ rowId, key }: { rowId: string; key: string }) =>
      client.developerConsole.osdkApplications.revokeClientSecret(appId, rowId, {
        idempotencyKey: key,
      }),
    { lockKey: ({ rowId }) => `developer:client-secret:revoke:${rowId}` },
  );

  const handleDeactivate = async (row: OsdkClientRow) => {
    const key = idempotencyKey("osdk_client_deactivate", row.id);
    setPendingRowId(row.id);
    setLastKey(key);
    const result = await deactivate.execute({ rowId: row.id, key });
    setPendingRowId(null);
    if (result) {
      toast.success(`클라이언트 ${row.clientId} 비활성화됨`);
      await clientsQuery.reload();
      onClientsChanged();
    }
  };

  const handleCreated = (row: OsdkClientRow) => {
    setCreatedClient(row);
    void clientsQuery.reload();
    onClientsChanged();
  };

  const handleRotateSecret = async (row: OsdkClientRow) => {
    const key = idempotencyKey("osdk_client_secret_rotate", row.id);
    setLastKey(key);
    const result = await rotateSecret.execute({ rowId: row.id, key });
    if (result && typeof result.clientSecret === "string") {
      setSecretReceipt({ clientId: row.clientId, clientSecret: result.clientSecret });
      toast.success(`${row.clientId}의 Client Secret이 교체되었습니다`);
      await clientsQuery.reload();
      onClientsChanged();
    }
  };

  const handleRevokeSecret = async (row: OsdkClientRow) => {
    const key = idempotencyKey("osdk_client_secret_revoke", row.id);
    setLastKey(key);
    const result = await revokeSecret.execute({ rowId: row.id, key });
    if (result) {
      setSecretReceipt(null);
      toast.success(`${row.clientId}의 Client Secret이 폐기되었습니다`);
      await clientsQuery.reload();
      onClientsChanged();
    }
  };

  const handleSecretHistory = async (row: OsdkClientRow) => {
    try {
      const history = await client.developerConsole.osdkApplications.listClientSecretVersions(
        appId,
        row.id,
      );
      setSecretHistory(history);
    } catch {
      toast.error("Client Secret 이력을 불러오지 못했습니다");
    }
  };

  const columns: readonly DataTableColumn<OsdkClientRow>[] = [
    {
      key: "clientId",
      header: "CLIENT ID",
      isMono: true,
      render: (row) => row.clientId,
    },
    {
      key: "status",
      header: "상태",
      render: (row) => (
        <StatusPill intent={statusIntent(row.status)}>{row.status}</StatusPill>
      ),
    },
    {
      key: "scopes",
      header: "허용 scope",
      render: (row) =>
        row.allowedScopes.length > 0 ? (
          <span className="font-mono text-[11px]">
            {row.allowedScopes.join(", ")}
          </span>
        ) : (
          <span className="text-muted-foreground">-</span>
        ),
    },
    {
      key: "redirects",
      header: "redirect URIs",
      render: (row) =>
        row.redirectUris.length > 0 ? (
          <span className="font-mono text-[11px]">
            {row.redirectUris.length}개
          </span>
        ) : (
          <span className="text-muted-foreground">-</span>
        ),
    },
    {
      key: "created",
      header: "생성",
      className: "text-muted-foreground",
      render: (row) => formatTimestamp(row.createdAt),
    },
    {
      key: "actions",
      header: "",
      className: "text-right",
      render: (row) =>
        row.status === "active" ? (
          <div className="flex justify-end gap-1">
            {row.redirectUris.length === 0 ? (
              <>
                <Button variant="outline" size="xs" onClick={() => void handleRotateSecret(row)}>
                  <RotateCw className="size-3" /> {row.currentSecretId ? "교체" : "발급"}
                </Button>
                <Button variant="ghost" size="xs" onClick={() => void handleSecretHistory(row)}>
                  <History className="size-3" /> 이력
                </Button>
                {row.currentSecretId ? (
                  <Button variant="ghost" size="xs" onClick={() => void handleRevokeSecret(row)}>
                    <ShieldX className="size-3" /> 폐기
                  </Button>
                ) : null}
              </>
            ) : null}
            <Button
              variant="outline"
              size="xs"
              onClick={() => void handleDeactivate(row)}
              disabled={deactivate.isRunning && pendingRowId === row.id}
            >
              비활성화
            </Button>
          </div>
        ) : (
          <span className="text-[11px] text-muted-foreground">-</span>
        ),
    },
  ];

  const rows = clientsQuery.data ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="section-label">OAuth 클라이언트 ({rows.length})</div>
        <Button size="sm" onClick={() => setIsCreateOpen(true)}>
          <Plus className="size-3.5" /> 새 클라이언트
        </Button>
      </div>

      {createdClient ? (
        <div className="space-y-2 rounded border border-success/40 bg-success/5 p-3">
          <div className="flex items-center gap-1.5 text-[13px] font-semibold text-success">
            <KeyRound className="size-4" /> 클라이언트가 생성되었습니다
          </div>
          <CopyField label="Client ID" value={createdClient.clientId} />
          {createdClient.clientSecret ? (
            <CopyField
              label="Client Secret"
              value={createdClient.clientSecret}
              sensitiveNote="이 secret은 다시 조회할 수 없습니다. 안전한 곳에 복사해 두세요."
            />
          ) : (
            <p className="text-[11px] text-muted-foreground">
              PKCE public client는 secret이 발급되지 않습니다. authorization
              code + PKCE flow를 사용하세요.
            </p>
          )}
          <div className="flex justify-end">
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setCreatedClient(null)}
            >
              닫기
            </Button>
          </div>
        </div>
      ) : null}

      {secretReceipt ? (
        <div className="space-y-2 rounded border border-warning/40 bg-warning/5 p-3">
          <div className="flex items-center gap-1.5 text-[13px] font-semibold">
            <KeyRound className="size-4" /> {secretReceipt.clientId} 새 Client Secret
          </div>
          <CopyField
            label="Client Secret"
            value={secretReceipt.clientSecret}
            sensitiveNote="이 값은 지금 한 번만 표시됩니다. 안전한 비밀 저장소에 복사하세요."
          />
          <div className="flex justify-end">
            <Button variant="ghost" size="xs" onClick={() => setSecretReceipt(null)}>닫기</Button>
          </div>
        </div>
      ) : null}

      {secretHistory.length > 0 ? (
        <div className="space-y-2 rounded border bg-card p-3">
          <div className="section-label">Client Secret 변경 이력</div>
          {secretHistory.map((item) => (
            <div key={String(item.secretId)} className="flex flex-wrap justify-between gap-2 border-t pt-2 text-[11px] first:border-t-0 first:pt-0">
              <span className="font-mono">{String(item.secretVersion)}</span>
              <span>{String(item.status)}</span>
              <span className="text-muted-foreground">마지막 사용 {String(item.lastUsedAt ?? "-")}</span>
            </div>
          ))}
        </div>
      ) : null}

      {clientsQuery.error ? (
        <ErrorState
          error={clientsQuery.error}
          onRetry={() => void clientsQuery.reload()}
        />
      ) : null}

      {clientsQuery.isLoading ? (
        <LoadingState rowCount={3} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title="클라이언트가 없습니다"
          description="OAuth 클라이언트를 만들어 앱에 인증하고 토큰을 발급받으세요."
          action={
            <Button size="sm" onClick={() => setIsCreateOpen(true)}>
              <Plus className="size-3.5" /> 새 클라이언트
            </Button>
          }
        />
      ) : (
        <DataTable columns={columns} rows={rows} rowKey={(row) => row.id} />
      )}

      {deactivate.error ? <ErrorState error={deactivate.error} /> : null}
      {rotateSecret.error ? <ErrorState error={rotateSecret.error} /> : null}
      {revokeSecret.error ? <ErrorState error={revokeSecret.error} /> : null}
      {lastKey ? (
        <div className="font-mono text-[11px] text-muted-foreground">
          idempotency_key={lastKey}
        </div>
      ) : null}

      <CreateClientDialog
        appId={appId}
        availableScopes={availableScopes}
        open={isCreateOpen}
        onOpenChange={setIsCreateOpen}
        onCreated={handleCreated}
      />
    </div>
  );
}
