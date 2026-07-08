import type { SourceConnection } from "@foundry-lite/sdk";
import { ArrowRight, Loader2, MinusCircle, Play, Table2 } from "lucide-react";
import { useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import type { StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

import { statusIntent, statusLabel } from "../source-model";
import { WizardField } from "../wizard/WizardFields";
import type { BatchSyncItem, BatchSyncPhase } from "./use-batch-sync-creation";
import {
  buildBatchDatasetRef,
  useBatchSyncCreation,
} from "./use-batch-sync-creation";

const PREFIX_PATTERN = /^[A-Za-z0-9_]*$/;

const PHASE_LABELS: Record<BatchSyncPhase, string> = {
  pending: "대기",
  creating: "생성 중",
  created: "생성 완료",
  running: "실행 중",
  succeeded: "성공",
  failed: "실패",
};

const PHASE_INTENTS: Record<BatchSyncPhase, StatusIntent> = {
  pending: "neutral",
  creating: "info",
  created: "success",
  running: "info",
  succeeded: "success",
  failed: "danger",
};

interface ExploreSyncPanelProps {
  source: SourceConnection;
  selectedTables: readonly string[];
  databaseUrlSecretRef: string;
  onRemoveTable: (tableName: string) => void;
  onSyncCreated?: (syncName: string) => void;
}

/**
 * 우측 "Foundry로 동기화할 테이블" 패널 (공식 db-explorer 우측 pane):
 * 선택 목록 + 데이터셋 접두사 + "생성 후 sync 실행" + 일괄 생성 버튼 + 증거.
 */
export function ExploreSyncPanel({
  source,
  selectedTables,
  databaseUrlSecretRef,
  onRemoveTable,
  onSyncCreated,
}: ExploreSyncPanelProps) {
  const [prefix, setPrefix] = useState("");
  const [shouldRunAfterCreate, setShouldRunAfterCreate] = useState(true);
  const batch = useBatchSyncCreation(source);

  const prefixError = PREFIX_PATTERN.test(prefix)
    ? null
    : "영문/숫자/밑줄만 사용할 수 있습니다.";
  const canCreate =
    selectedTables.length > 0 &&
    prefixError === null &&
    databaseUrlSecretRef.length > 0 &&
    !batch.isRunning;

  const handleCreate = () => {
    if (!canCreate) return;
    void batch.execute({
      tables: selectedTables,
      prefix,
      databaseUrlSecretRef,
      shouldRunAfterCreate,
    });
  };

  const firstCreatedSync = batch.items.find((item) => item.sync !== null);

  return (
    <div className="flex w-72 shrink-0 flex-col border-l bg-primary/[0.04]">
      <div className="border-b px-3 py-2">
        <div className="text-[13px] font-semibold">
          Foundry로 동기화할 테이블
        </div>
        <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
          데이터 동기화를 완료하려면 선택한 테이블로 Sync를 생성하세요.
        </p>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {selectedTables.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            좌측 트리 또는 그래프에서 ⊕ 버튼으로 테이블을 추가하세요.
          </p>
        ) : (
          <ul className="space-y-1">
            {selectedTables.map((tableName) => (
              <li
                key={tableName}
                className="flex items-center gap-1.5 rounded border bg-card px-2 py-1.5"
              >
                <Table2 className="size-3.5 shrink-0 text-primary" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-[11px] font-medium">
                    {tableName}
                  </span>
                  <span className="block truncate font-mono text-[10px] text-muted-foreground">
                    {buildBatchDatasetRef(prefix, tableName)}
                  </span>
                </span>
                <button
                  type="button"
                  className="text-muted-foreground/70 hover:text-destructive"
                  onClick={() => onRemoveTable(tableName)}
                  aria-label={`${tableName} 선택 제거`}
                >
                  <MinusCircle className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <WizardField
          label="데이터셋 접두사"
          helper="생성되는 데이터셋 이름 앞에 붙습니다 (sync.{접두사}{테이블})."
          error={prefixError}
        >
          <Input
            value={prefix}
            onChange={(event) => setPrefix(event.target.value)}
            placeholder="verify5_"
            className="h-8 bg-card font-mono text-xs"
          />
        </WizardField>
        <label className="flex items-center gap-2 text-xs">
          <Checkbox
            checked={shouldRunAfterCreate}
            onCheckedChange={(checked) =>
              setShouldRunAfterCreate(checked === true)
            }
          />
          생성 후 sync 실행
        </label>
        <Button
          size="sm"
          className="w-full"
          disabled={!canCreate}
          onClick={handleCreate}
        >
          {batch.isRunning ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <ArrowRight className="size-3.5" />
          )}
          {selectedTables.length}개 데이터셋 동기화 생성
        </Button>
        <p className="text-[10px] text-muted-foreground">
          새 데이터셋은 <span className="font-mono">sync</span> 네임스페이스에
          생성됩니다.
        </p>
        {batch.items.length > 0 ? (
          <div className="space-y-2">
            <div className="section-label">동기화 생성 증거</div>
            {batch.items.map((item) => (
              <BatchItemEvidence key={item.syncName} item={item} />
            ))}
            {firstCreatedSync && onSyncCreated && !batch.isRunning ? (
              <Button
                size="sm"
                variant="outline"
                className="w-full bg-card"
                onClick={() => onSyncCreated(firstCreatedSync.syncName)}
              >
                <Play className="size-3.5" /> 동기화 탭에서 보기
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function BatchItemEvidence({ item }: { item: BatchSyncItem }) {
  return (
    <div className="rounded border bg-card p-2">
      <div className="flex items-center gap-1.5">
        <Table2 className="size-3.5 shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] font-medium">
          {item.tableName}
        </span>
        <StatusPill intent={PHASE_INTENTS[item.phase]}>
          {PHASE_LABELS[item.phase]}
        </StatusPill>
      </div>
      <dl className="mt-1.5 space-y-0.5 font-mono text-[10px] text-muted-foreground">
        <EvidenceLine label="sync" value={item.syncName} />
        <EvidenceLine label="dataset" value={item.datasetRef} />
        <EvidenceLine
          label="fingerprint"
          value={item.sync ? item.sync.configFingerprint.slice(0, 24) : null}
        />
        <EvidenceLine label="request_id" value={item.createKey} />
        {item.runKey ? (
          <EvidenceLine label="run_request_id" value={item.runKey} />
        ) : null}
        {item.run ? (
          <>
            <EvidenceLine label="run_id" value={item.run.runId} />
            <div className="flex items-center gap-1.5">
              <dt className="w-20 shrink-0">run_status</dt>
              <dd>
                <StatusPill intent={statusIntent(item.run.status)}>
                  {statusLabel(item.run.status)}
                </StatusPill>
              </dd>
            </div>
          </>
        ) : null}
      </dl>
      {item.errorMessage ? (
        <p className="mt-1 text-[10px] break-all text-destructive">
          {item.errorMessage}
        </p>
      ) : null}
    </div>
  );
}

function EvidenceLine({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  if (!value) return null;
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="w-20 shrink-0">{label}</dt>
      <dd className="min-w-0 flex-1 truncate text-foreground">{value}</dd>
    </div>
  );
}
