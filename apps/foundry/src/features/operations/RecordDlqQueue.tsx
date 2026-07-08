import { idempotencyKey } from "@foundry-lite/sdk";
import type { DeadLetterRecord } from "@foundry-lite/sdk";
import {
  useFoundryLiteProvidedRecordDlqControls,
  useFoundryLiteProvidedRecordDlqQueue,
} from "@foundry-lite/sdk/react";
import { RotateCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { DataTable } from "@/components/shared/DataTable";
import type { DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import {
  EvidenceRow,
  MutationEvidence,
  SectionHeader,
} from "./OperationsPrimitives";
import {
  dlqStatusMeta,
  formatTimestamp,
  readRowString,
} from "./operations-format";

type ActionEvidence = {
  action: "retry" | "discard";
  recordId: string;
  idempotencyKey: string | null;
  requestId: string | null;
  status: string;
  replayRunId?: string | null;
  isIdempotentReplay?: boolean;
};

export function RecordDlqQueue() {
  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<ActionEvidence | null>(null);

  const queue = useFoundryLiteProvidedRecordDlqQueue({ selectedRecordId });
  const controls = useFoundryLiteProvidedRecordDlqControls();

  const selectedRecord = queue.selectedRecord;

  const handleRetry = async (record: DeadLetterRecord) => {
    const key = idempotencyKey("operations_dlq_retry", record.id);
    const result = await controls.retry.execute({
      id: record.id,
      idempotencyKey: key,
    });
    if (result) {
      setEvidence({
        action: "retry",
        recordId: record.id,
        idempotencyKey: key,
        requestId: null,
        status: result.status,
        replayRunId: result.replayRunId,
        isIdempotentReplay: result.is_idempotent_replay,
      });
    } else {
      setEvidence({
        action: "retry",
        recordId: record.id,
        idempotencyKey: key,
        requestId: controls.retry.requestId,
        status: "실패",
      });
    }
    await queue.reload();
  };

  const handleDiscard = async (record: DeadLetterRecord) => {
    const result = await controls.discard.execute({ id: record.id });
    if (result) {
      setEvidence({
        action: "discard",
        recordId: record.id,
        idempotencyKey: null,
        requestId: null,
        status: result.status,
      });
    } else {
      setEvidence({
        action: "discard",
        recordId: record.id,
        idempotencyKey: null,
        requestId: controls.discard.requestId,
        status: "실패",
      });
    }
    await queue.reload();
  };

  const columns: readonly DataTableColumn<DeadLetterRecord>[] = [
    {
      key: "id",
      header: "record id",
      isMono: true,
      className: "max-w-[180px]",
      render: (record) => <span className="block truncate">{record.id}</span>,
    },
    {
      key: "errorKind",
      header: "오류",
      render: (record) => (
        <span className="text-[11px]">{record.error_kind}</span>
      ),
    },
    {
      key: "status",
      header: "상태",
      render: (record) => {
        const meta = dlqStatusMeta(record.status);
        return <StatusPill intent={meta.intent}>{meta.label}</StatusPill>;
      },
    },
    {
      key: "attempts",
      header: "시도",
      isMono: true,
      render: (record) => record.attempts,
    },
  ];

  return (
    <div className="flex min-h-0 flex-col gap-3 lg:h-full lg:flex-row">
      <div className="flex min-h-0 flex-col gap-2 lg:w-[52%]">
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground">
            {queue.records.length}건 · 미해결 {queue.openRecords.length}건
            {queue.hasFailedReplay
              ? ` · 재시도 실패 ${queue.failedReplayRecords.length}건`
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
          <LoadingState rowCount={6} />
        ) : queue.error ? (
          <ErrorState error={queue.error} onRetry={() => void queue.reload()} />
        ) : queue.hasRecords ? (
          <DataTable
            columns={columns}
            rows={queue.records}
            rowKey={(record) => record.id}
            selectedKey={selectedRecord?.id ?? null}
            onRowClick={(record) => setSelectedRecordId(record.id)}
            className="min-h-0 flex-1"
          />
        ) : (
          <EmptyState
            title="DLQ가 비어 있습니다"
            description="격리된 record가 없습니다. 스트림/변환 실패 시 여기에 격리 record가 쌓입니다."
          />
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {selectedRecord ? (
          <RecordDetail
            record={selectedRecord}
            canRetry={queue.canRetrySelected}
            canDiscard={queue.canDiscardSelected}
            isBusy={controls.isBusy}
            evidence={
              evidence?.recordId === selectedRecord.id ? evidence : null
            }
            retryError={controls.retry.error}
            discardError={controls.discard.error}
            onRetry={() => void handleRetry(selectedRecord)}
            onDiscard={() => void handleDiscard(selectedRecord)}
          />
        ) : (
          <EmptyState
            title="record를 선택하세요"
            description="좌측에서 격리 record를 선택하면 payload·오류·재시도/폐기 제어를 확인할 수 있습니다."
          />
        )}
      </div>
    </div>
  );
}

function RecordDetail({
  record,
  canRetry,
  canDiscard,
  isBusy,
  evidence,
  retryError,
  discardError,
  onRetry,
  onDiscard,
}: {
  record: DeadLetterRecord;
  canRetry: boolean;
  canDiscard: boolean;
  isBusy: boolean;
  evidence: ActionEvidence | null;
  retryError: unknown;
  discardError: unknown;
  onRetry: () => void;
  onDiscard: () => void;
}) {
  const meta = dlqStatusMeta(record.status);
  return (
    <div className="space-y-3">
      <div className="rounded border bg-card p-3">
        <div className="mb-2 flex items-center gap-2">
          <span className="section-label">record 상세</span>
          <StatusPill intent={meta.intent}>{meta.label}</StatusPill>
          <div className="ml-auto flex items-center gap-1.5">
            <Button
              size="sm"
              onClick={onRetry}
              disabled={!canRetry || isBusy}
              title={
                canRetry ? undefined : "이 상태에서는 재시도할 수 없습니다"
              }
            >
              <RotateCw className="size-3.5" /> 재시도
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={onDiscard}
              disabled={!canDiscard || isBusy}
              title={canDiscard ? undefined : "이미 종료된 record입니다"}
            >
              <Trash2 className="size-3.5" /> 폐기
            </Button>
          </div>
        </div>
        <dl className="divide-y">
          <EvidenceRow label="record id" value={record.id} />
          <EvidenceRow label="source event" value={record.source_event_id} />
          <EvidenceRow label="source run" value={record.source_run_id ?? "—"} />
          <EvidenceRow
            label="dataset ref"
            value={readRowString(record.metadata, "dataset_ref") ?? "—"}
          />
          <EvidenceRow
            label="원본 request id"
            value={readRowString(record.metadata, "request_id") ?? "—"}
          />
          <EvidenceRow label="payload hash" value={record.payload_hash} />
          <EvidenceRow
            label="replay status"
            value={record.replay_status}
            mono={false}
          />
          <EvidenceRow
            label="격리 시각"
            value={formatTimestamp(record.first_failed_at)}
          />
        </dl>
      </div>

      <div className="rounded border border-destructive/30 bg-destructive/5 p-3">
        <SectionHeader label="오류" hint={record.error_kind} />
        <p className="mt-1.5 text-[12px] text-foreground/90">
          {record.error_message}
        </p>
      </div>

      {evidence ? (
        <MutationEvidence
          idempotencyKey={evidence.idempotencyKey}
          requestId={evidence.requestId}
        >
          <span>
            {evidence.action === "retry" ? "retry" : "discard"} 결과=
            {evidence.status}
          </span>
          {evidence.replayRunId ? (
            <span>replay_run={evidence.replayRunId}</span>
          ) : null}
          {evidence.isIdempotentReplay ? (
            <span>idempotent_replay=true</span>
          ) : null}
        </MutationEvidence>
      ) : null}

      {retryError ? <ErrorState error={retryError} onRetry={onRetry} /> : null}
      {discardError ? <ErrorState error={discardError} /> : null}

      <div className="rounded border bg-card p-3">
        <SectionHeader label="payload" />
        <pre className="mt-2 max-h-56 overflow-auto rounded border bg-muted/40 p-2 font-mono text-[11px] whitespace-pre-wrap text-foreground/90">
          {JSON.stringify(record.payload, null, 2)}
        </pre>
      </div>
    </div>
  );
}
