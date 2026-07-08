import type {
  ActionWritebackQueueItem,
  ActionWritebackRecoveryApprovalRequest,
  ActionWritebackRecoveryItem,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteProvidedWritebackReconciliationQueue,
  useFoundryLiteProvidedWritebackResolve,
} from "@foundry-lite/sdk/react";
import { CheckCircle2 } from "lucide-react";
import { useState } from "react";

import { DataTable } from "@/components/shared/DataTable";
import type { DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import type { StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import {
  EvidenceRow,
  MutationEvidence,
  SectionHeader,
} from "./OperationsPrimitives";
import { formatTimestamp } from "./operations-format";

const STATUS_META: Record<string, { label: string; intent: StatusIntent }> = {
  outcome_unknown: { label: "결과 미확정", intent: "warning" },
  compensation_required: { label: "보상 필요", intent: "danger" },
  retryable: { label: "재시도 가능", intent: "info" },
  reconciled: { label: "조정 완료", intent: "success" },
};

function statusMeta(status: string): { label: string; intent: StatusIntent } {
  return STATUS_META[status] ?? { label: status, intent: "neutral" };
}

type ApproveRecoveryPayload = {
  writebackId: string;
  payload: ActionWritebackRecoveryApprovalRequest;
};

export function WritebackReconciliation() {
  const [selectedWritebackId, setSelectedWritebackId] = useState<string | null>(
    null,
  );
  const [lastResolvedItem, setLastResolvedItem] =
    useState<ActionWritebackQueueItem | null>(null);
  const [lastApprovedItem, setLastApprovedItem] =
    useState<ActionWritebackQueueItem | null>(null);
  const [remoteResourceId, setRemoteResourceId] = useState("");
  const [approvalId, setApprovalId] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [approvalExternalWritebackUri, setApprovalExternalWritebackUri] =
    useState("");
  const [lastRequestId, setLastRequestId] = useState<string | null>(null);

  const client = useFoundryLiteClient();
  const queue = useFoundryLiteProvidedWritebackReconciliationQueue({
    selectedWritebackId,
  });
  const resolve = useFoundryLiteProvidedWritebackResolve();
  const approveRecovery = useFoundryLiteMutation<
    ActionWritebackRecoveryItem,
    ApproveRecoveryPayload
  >(
    (payload) =>
      client.operations.reconciliation.approveRecovery(
        payload.writebackId,
        payload.payload,
      ),
    {
      lockKey: (payload) =>
        `operations:writeback-reconciliation:approve-recovery:${payload.writebackId}`,
    },
  );

  const completedSelected =
    (lastApprovedItem?.writebackId === selectedWritebackId
      ? lastApprovedItem
      : null) ??
    (lastResolvedItem?.writebackId === selectedWritebackId
      ? lastResolvedItem
      : null);
  const selected = completedSelected ?? queue.selectedWriteback;

  const handleResolve = async (item: ActionWritebackQueueItem) => {
    const trimmed = remoteResourceId.trim();
    const result = await resolve.execute({
      writebackId: item.writebackId,
      payload: {
        remoteStatus: "succeeded",
        remoteResourceId: trimmed || item.writebackId,
      },
    });
    setLastRequestId(resolve.requestId);
    if (result) {
      setLastResolvedItem(item);
      await queue.reload();
    }
  };

  const handleApproveRecovery = async (item: ActionWritebackQueueItem) => {
    const trimmedApprovalId = approvalId.trim();
    const trimmedReason = approvalReason.trim();
    const trimmedUri = approvalExternalWritebackUri.trim();
    if (!trimmedApprovalId || !trimmedReason) return;
    const result = await approveRecovery.execute({
      writebackId: item.writebackId,
      payload: {
        approvalId: trimmedApprovalId,
        reason: trimmedReason,
        ...(trimmedUri ? { externalWritebackUri: trimmedUri } : {}),
      },
    });
    if (result) {
      setLastApprovedItem(item);
      await queue.reload();
    }
  };

  const columns: readonly DataTableColumn<ActionWritebackQueueItem>[] = [
    {
      key: "writebackId",
      header: "writeback id",
      isMono: true,
      className: "max-w-[200px]",
      render: (item) => (
        <span className="block truncate">{item.writebackId}</span>
      ),
    },
    {
      key: "connector",
      header: "커넥터",
      render: (item) => <span className="text-[11px]">{item.connectorId}</span>,
    },
    {
      key: "status",
      header: "상태",
      render: (item) => {
        const meta = statusMeta(item.status);
        return <StatusPill intent={meta.intent}>{meta.label}</StatusPill>;
      },
    },
    {
      key: "attempts",
      header: "시도",
      isMono: true,
      render: (item) => item.attempts,
    },
  ];

  return (
    <div className="flex min-h-0 flex-col gap-3 lg:h-full lg:flex-row">
      <div className="flex min-h-0 flex-col gap-2 lg:w-[52%]">
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground">
            {queue.items.length}건
            {queue.hasCompensationRequired
              ? ` · 보상 필요 ${queue.compensationRequiredItems.length}건`
              : ""}
            {queue.hasOutcomeUnknown
              ? ` · 미확정 ${queue.outcomeUnknownItems.length}건`
              : ""}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void queue.reload()}
            disabled={queue.isRefreshing}
          >
            새로고침
          </Button>
        </div>

        {queue.isLoading && !queue.data ? (
          <LoadingState rowCount={5} />
        ) : queue.error ? (
          <ErrorState error={queue.error} onRetry={() => void queue.reload()} />
        ) : queue.hasItems ? (
          <DataTable
            columns={columns}
            rows={queue.items}
            rowKey={(item) => item.writebackId}
            selectedKey={selected?.writebackId ?? null}
            onRowClick={(item) => {
              setSelectedWritebackId(item.writebackId);
              setLastResolvedItem(null);
              setLastApprovedItem(null);
              setRemoteResourceId("");
              setApprovalId("");
              setApprovalReason("");
              setApprovalExternalWritebackUri(externalWritebackUri(item));
            }}
            className="min-h-0 flex-1"
          />
        ) : (
          <EmptyState
            title="조정 대기 writeback이 없습니다"
            description="외부 커넥터 writeback 결과가 미확정이거나 보상이 필요할 때 여기에 표시됩니다."
          />
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {selected ? (
          <WritebackDetail
            item={selected}
            canResolve={queue.canResolveSelected}
            isBusy={resolve.isRunning}
            isApproving={approveRecovery.isRunning}
            remoteResourceId={remoteResourceId}
            onRemoteResourceIdChange={setRemoteResourceId}
            onResolve={() => void handleResolve(selected)}
            approvalId={approvalId}
            approvalReason={approvalReason}
            approvalExternalWritebackUri={approvalExternalWritebackUri}
            onApprovalIdChange={setApprovalId}
            onApprovalReasonChange={setApprovalReason}
            onApprovalExternalWritebackUriChange={setApprovalExternalWritebackUri}
            onApproveRecovery={() => void handleApproveRecovery(selected)}
            resolveError={resolve.error}
            approveError={approveRecovery.error}
            reconciliation={resolve.reconciliation}
            approval={approveRecovery.result}
            hasObjectEdit={resolve.hasObjectEdit}
            alreadyReconciled={resolve.alreadyReconciled}
            requestId={lastRequestId}
            approvalRequestId={approveRecovery.requestId}
          />
        ) : (
          <EmptyState
            title="writeback을 선택하세요"
            description="외부 상태를 확인한 뒤 remote resource id를 입력해 idempotent하게 조정합니다."
          />
        )}
      </div>
    </div>
  );
}

function WritebackDetail({
  item,
  canResolve,
  isBusy,
  isApproving,
  remoteResourceId,
  onRemoteResourceIdChange,
  onResolve,
  approvalId,
  approvalReason,
  approvalExternalWritebackUri,
  onApprovalIdChange,
  onApprovalReasonChange,
  onApprovalExternalWritebackUriChange,
  onApproveRecovery,
  resolveError,
  approveError,
  reconciliation,
  approval,
  hasObjectEdit,
  alreadyReconciled,
  requestId,
  approvalRequestId,
}: {
  item: ActionWritebackQueueItem;
  canResolve: boolean;
  isBusy: boolean;
  isApproving: boolean;
  remoteResourceId: string;
  onRemoteResourceIdChange: (value: string) => void;
  onResolve: () => void;
  approvalId: string;
  approvalReason: string;
  approvalExternalWritebackUri: string;
  onApprovalIdChange: (value: string) => void;
  onApprovalReasonChange: (value: string) => void;
  onApprovalExternalWritebackUriChange: (value: string) => void;
  onApproveRecovery: () => void;
  resolveError: unknown;
  approveError: unknown;
  reconciliation: ReturnType<
    typeof useFoundryLiteProvidedWritebackResolve
  >["reconciliation"];
  approval: ActionWritebackRecoveryItem | null;
  hasObjectEdit: boolean;
  alreadyReconciled: boolean;
  requestId: string | null;
  approvalRequestId: string | null;
}) {
  const meta = statusMeta(item.status);
  const canApproveRecovery =
    canResolve && approvalId.trim().length > 0 && approvalReason.trim().length > 0;
  return (
    <div className="space-y-3">
      <div className="rounded border bg-card p-3">
        <div className="mb-2 flex items-center gap-2">
          <span className="section-label">writeback 상세</span>
          <StatusPill intent={meta.intent}>{meta.label}</StatusPill>
        </div>
        <dl className="divide-y">
          <EvidenceRow label="writeback id" value={item.writebackId} />
          <EvidenceRow label="action run" value={item.actionRunId} />
          <EvidenceRow label="커넥터" value={item.connectorId} />
          <EvidenceRow label="mode" value={item.mode} mono={false} />
          <EvidenceRow label="idempotency key" value={item.idempotencyKey} />
          <EvidenceRow label="생성" value={formatTimestamp(item.createdAt)} />
        </dl>
      </div>

      <div className="rounded border bg-card p-3">
        <SectionHeader
          label="조정 실행"
          hint="외부 상태를 confirmed로 반영 (idempotent)"
        />
        <div className="mt-2 space-y-2">
          <Input
            value={remoteResourceId}
            onChange={(event) => onRemoteResourceIdChange(event.target.value)}
            placeholder="remote resource id (미입력 시 writeback id 사용)"
            className="h-8 text-[12px]"
          />
          <Button
            size="sm"
            onClick={onResolve}
            disabled={!canResolve || isBusy}
            title={canResolve ? undefined : "이 상태는 조정 대상이 아닙니다"}
          >
            <CheckCircle2 className="size-3.5" /> succeeded로 조정
          </Button>
        </div>
      </div>

      <div className="rounded border bg-card p-3">
        <SectionHeader
          label="승인 후 recovery"
          hint="민감 writeback은 approval id/reason을 남긴 뒤 외부 lookup으로 복구"
        />
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          <Input
            value={approvalId}
            onChange={(event) => onApprovalIdChange(event.target.value)}
            placeholder="approval id"
            className="h-8 text-[12px]"
          />
          <Input
            value={approvalReason}
            onChange={(event) => onApprovalReasonChange(event.target.value)}
            placeholder="approval reason"
            className="h-8 text-[12px]"
          />
          <Input
            value={approvalExternalWritebackUri}
            onChange={(event) =>
              onApprovalExternalWritebackUriChange(event.target.value)
            }
            placeholder="externalWritebackUri (저장값 사용 가능)"
            className="h-8 text-[12px] md:col-span-2"
          />
          <div className="md:col-span-2">
            <Button
              size="sm"
              onClick={onApproveRecovery}
              disabled={!canApproveRecovery || isApproving}
              title={
                canApproveRecovery
                  ? undefined
                  : "approval id와 reason이 필요합니다"
              }
            >
              <CheckCircle2 className="size-3.5" /> 승인 후 recovery 실행
            </Button>
          </div>
        </div>
      </div>

      {reconciliation ? (
        <MutationEvidence requestId={requestId}>
          <span>status={reconciliation.status}</span>
          <span>remote_status={reconciliation.remoteStatus}</span>
          {reconciliation.objectEditId ? (
            <span>object_edit={reconciliation.objectEditId}</span>
          ) : null}
          {hasObjectEdit ? <span>object_edit_applied=true</span> : null}
          {alreadyReconciled ? <span>already_reconciled=true</span> : null}
        </MutationEvidence>
      ) : null}

      {approval ? (
        <MutationEvidence requestId={approvalRequestId}>
          <span>decision={approval.decision}</span>
          <span>approval_id={approval.approvalId}</span>
          <span>approval_reason={approval.approvalReason}</span>
          {approval.remoteStatus ? (
            <span>remote_status={approval.remoteStatus}</span>
          ) : null}
          {approval.remoteResourceId ? (
            <span>remote_resource={approval.remoteResourceId}</span>
          ) : null}
        </MutationEvidence>
      ) : null}

      {resolveError ? (
        <ErrorState error={resolveError} onRetry={onResolve} />
      ) : null}

      {approveError ? (
        <ErrorState error={approveError} onRetry={onApproveRecovery} />
      ) : null}

      <div className="rounded border bg-card p-3">
        <SectionHeader label="request payload" />
        <pre className="mt-2 max-h-48 overflow-auto rounded border bg-muted/40 p-2 font-mono text-[11px] whitespace-pre-wrap text-foreground/90">
          {JSON.stringify(item.request, null, 2)}
        </pre>
      </div>
    </div>
  );
}

function externalWritebackUri(item: ActionWritebackQueueItem): string {
  const value = item.request.externalWritebackUri;
  return typeof value === "string" ? value : "";
}
