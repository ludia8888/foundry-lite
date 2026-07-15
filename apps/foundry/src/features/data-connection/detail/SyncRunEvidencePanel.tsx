import type { SourceManagedSyncRun } from "@foundry-lite/sdk";
import { ExternalLink, FileWarning, GitCommitHorizontal } from "lucide-react";
import { Link } from "react-router";

import { StatusPill } from "@/components/shared/StatusPill";

import {
  formatTimestamp,
  statusIntent,
  statusLabel,
  toOperationsHref,
} from "../source-model";
import { SyncRunEvidenceBlock } from "./SyncRunEvidenceBlock";
import { SyncNetworkEvidenceCard } from "./SyncNetworkEvidenceCard";

interface SyncRunEvidencePanelProps {
  run: SourceManagedSyncRun;
}

/** 선택한 빌드의 커밋·체크포인트·오류를 다시 확인할 수 있는 운영 증거 패널. */
export function SyncRunEvidencePanel({ run }: SyncRunEvidencePanelProps) {
  const operationsHref = toOperationsHref(run.operationsPath);
  return (
    <section
      aria-label="선택한 실행 증거"
      data-testid="sync-run-evidence"
      className="mt-2 rounded border bg-card"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
        <div className="flex items-center gap-2">
          <GitCommitHorizontal className="size-3.5 text-muted-foreground" />
          <span className="font-mono text-[11px] font-semibold">{run.runId}</span>
          <StatusPill intent={statusIntent(run.status)}>{statusLabel(run.status)}</StatusPill>
        </div>
        {operationsHref ? (
          <Link to={operationsHref} className="flex items-center gap-1 text-[11px] text-primary hover:underline">
            Operations에서 열기 <ExternalLink className="size-3" />
          </Link>
        ) : null}
      </header>
      <div className="grid gap-3 p-3 lg:grid-cols-3">
        <dl className="grid grid-cols-[84px_1fr] gap-y-1 text-[11px]">
          <dt className="text-muted-foreground">트리거</dt>
          <dd>{run.triggerType === "manual" ? "수동" : run.triggerType === "recovery" ? "복구" : "예약"}</dd>
          <dt className="text-muted-foreground">시작</dt>
          <dd className="font-mono">{formatTimestamp(run.startedAt)}</dd>
          <dt className="text-muted-foreground">완료</dt>
          <dd className="font-mono">{formatTimestamp(run.completedAt)}</dd>
          <dt className="text-muted-foreground">데이터셋 버전</dt>
          <dd className="break-all font-mono">{run.datasetVersionId ?? "커밋 없음"}</dd>
        </dl>
        <SyncRunEvidenceBlock title="체크포인트 시작" value={run.checkpointStart} />
        <SyncRunEvidenceBlock title="체크포인트 종료" value={run.checkpointEnd} />
      </div>
      {run.networkEvidence ? <SyncNetworkEvidenceCard evidence={run.networkEvidence} /> : null}
      {run.error ? (
        <div className="border-t border-destructive/20 bg-destructive/5 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-destructive">
            <FileWarning className="size-3.5" /> 실패 원인
          </div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[10px] leading-4">
            {JSON.stringify(run.error, null, 2)}
          </pre>
        </div>
      ) : null}
    </section>
  );
}
